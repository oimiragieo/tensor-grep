#![allow(unused_imports, dead_code)]
use crate::native_multi_pattern::collect_native_multi_pattern_matches;
use crate::validation_rollback::{
    collect_validation_rollback_snapshots, emit_rollback_status,
    restore_validation_rollback_snapshots, retained_validation_targets, ValidationRollbackSummary,
};
use anyhow::Context;
#[cfg(feature = "cuda")]
use clap::ValueEnum;
use clap::{Args, CommandFactory, Parser, Subcommand};
use hmac::{Hmac, Mac};
use tensor_grep_rs::rg_passthrough::generated_scan_dir_names;
// Bug #88/#480/#100: `implicit_search_walk_exceeds_ceiling` (the WALK-ceiling probe) moved to
// `rg_passthrough.rs` (a library module, not this binary crate root) -- see the breadcrumb
// comment above `parse_early_ripgrep_args`. `WalkBuilder`'s only remaining consumer in THIS file
// is `count_search_corpus_bytes`, which is cuda-gated, so both imports are cuda-gated now.
use crate::main_env::env_flag_enabled;
use crate::search_files::format_broad_generated_scan_error;
use crate::search_flag_registry::{
    raw_args_contain_any_flag, search_args_contain_any_flag, SEARCH_OPTION_FIRST_FLAGS,
    SEARCH_PYTHON_PASSTHROUGH_FLAGS,
};
#[cfg(feature = "cuda")]
use ignore::overrides::OverrideBuilder;
#[cfg(feature = "cuda")]
use ignore::WalkBuilder;
use process_control::{ChildExt, Control};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::ffi::{OsStr, OsString};
#[cfg(feature = "cuda")]
use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tensor_grep_rs::backend_ast::{
    AstBackend, AstMatch, AstMetaVariables, BatchRewritePlan, BatchRewriteRule,
};
use tensor_grep_rs::broken_pipe::error_chain_has_broken_pipe;
use tensor_grep_rs::crossover::{
    run_crossover_calibration, skip_signal_payload, write_crossover_config, NoCudaBuildError,
};
use tensor_grep_rs::exit_codes::exit_with_run_error;
#[cfg(feature = "cuda")]
use tensor_grep_rs::gpu_native::{
    benchmark_cuda_graph_search_paths, benchmark_pageable_transfer_throughput,
    benchmark_pinned_transfer_throughput, enumerate_cuda_devices, gpu_native_search_paths_multi,
    probe_device_allocation, GpuNativeSearchConfig, GpuNativeSearchStats, GpuPipelineStats,
};
use tensor_grep_rs::index::TrigramIndex;
use tensor_grep_rs::index_mmap::{mmap_index_path, LoadedIndex, MmapIndex};
use tensor_grep_rs::native_search::{
    defaulted_scope_fields, emit_index_binary_match_warning, native_json_text_fields,
    native_search_pattern_compiles, run_native_search, smart_case_pattern_is_case_insensitive,
    NativeSearchConfig, SearchStats,
};
use tensor_grep_rs::python_sidecar::{
    execute_python_passthrough_command, execute_python_passthrough_command_captured,
    execute_python_passthrough_command_with_stdin, execute_sidecar_command, SidecarError,
};
use tensor_grep_rs::rg_passthrough::{
    apply_last_ignore_wins, execute_ripgrep_pcre2_version, execute_ripgrep_search,
    execute_ripgrep_type_list, extract_search_tail_with_matches, implicit_search_paths,
    is_unbounded_implicit_search_walk_refusal, ripgrep_is_available, RipgrepSearchArgs,
};
use tensor_grep_rs::routing::{
    gpu_proof_fields, native_can_serve_plain_text, plain_text_native_cheap_checks_pass,
    plain_text_native_flag_token_is_allowed, route_search, BackendSelection, IndexRoutingState,
    PlainTextNativeRequest, RoutingDecision, SearchRoutingCalibration, SearchRoutingConfig,
};
use tensor_grep_rs::search_error::{
    self, exit_json_search_runtime_error_if_needed, exit_structured_search_error_if_needed,
    handle_search_runtime_error, refuse_structured_pcre2,
};

// Root identity also appears in positional help generated from this binary.
use crate::cli_app::*;
use crate::*;

#[derive(Serialize)]
pub(crate) struct SearchResultJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) requested_gpu_device_ids: Vec<i32>,
    pub(crate) routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) not_gpu_proof_reason: Option<String>,
    pub(crate) query: &'a str,
    pub(crate) path: &'a str,
    pub(crate) total_files: usize,
    pub(crate) total_matches: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) binary_match_files: Option<usize>,
    pub(crate) matched_file_paths: Vec<String>,
    pub(crate) match_counts_by_file: std::collections::BTreeMap<String, usize>,
    pub(crate) matches: Vec<SearchMatchJson>,
    // Task 276 (#314). This is the SECOND envelope -- parallel to the native one at
    // `native_search.rs:2489-2491` but, until now, carrying none of its disclosure.
    // Omit-when-complete, so a complete search stays byte-identical.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_paths_count: Option<usize>,
    // v1.101.22 dogfood: "PATH note is stderr-only -- bare `--json` still returns empty aggregate
    // JSON with no warnings/notes field; agents that ignore stderr can miss it."
    //
    // THE BINARY MUST STAMP THIS, not Python. `--json` is a supported trigger for native
    // delegation and `_run_native_tg_search` STREAMS this document straight through
    // (`_streaming_passthrough_returncode`), so Python never holds it and cannot inject a field
    // without buffering -- which would break streaming for large result sets to fix a zero-match
    // case. Same reasoning that put the broad-scan refusal envelope on this side in #867: the
    // surface that owns the document owns its disclosure.
    //
    // DELIBERATELY NOT part of the incompleteness family above. A search whose PATH defaulted to
    // the cwd RAN TO COMPLETION -- it answered a narrower question than the caller may have meant.
    // Setting `result_incomplete` would be false AND would flip the exit code to 2, breaking the
    // closed 0/1/2 contract. Advisory only; exit stays 1.
    //
    // Omit-when-inapplicable, matching every field above: absent when the caller gave an explicit
    // PATH, so an existing consumer's payload stays byte-identical.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) scope_note: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) remediation: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub(crate) advisory_hints: Vec<String>,
}

/// Terminal record keeps disclosures available on empty and incomplete scans.
#[derive(Serialize)]
pub(crate) struct SearchSummaryNdjson {
    #[serde(rename = "type")]
    pub(crate) record_type: &'static str,
    pub(crate) version: u32,
    pub(crate) total_matches: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) routing_backend: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) routing_reason: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) sidecar_used: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) requested_gpu_device_ids: Option<Vec<i32>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) routing_gpu_device_ids: Option<Vec<i32>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) not_gpu_proof_reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) binary_match_files: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_paths_count: Option<usize>,
    // Task #26, same pair as `SearchResultJson`. A streaming reader that never sees a match record
    // gets ONLY this summary, so leaving the scope disclosure out of it makes `--ndjson` the
    // quietest surface of all -- an empty stream followed by a summary saying nothing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) scope_note: Option<&'static str>,
}

#[cfg(feature = "cuda")]
#[derive(Serialize)]
pub(crate) struct GpuNativeSearchResultJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) query: &'a str,
    pub(crate) path: &'a str,
    pub(crate) total_matches: usize,
    pub(crate) total_files: usize,
    pub(crate) requested_gpu_device_ids: Vec<i32>,
    pub(crate) routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) not_gpu_proof_reason: Option<String>,
    // Task 316: the same incompleteness triple the CPU envelopes carry, filled by the SHARED
    // `incomplete_envelope_fields` so the GPU route cannot drift from them. `skip_serializing_if`
    // keeps a complete GPU scan byte-identical to the pre-316 payload -- the omit-when-complete
    // convention `result_incomplete` already follows everywhere else.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) result_incomplete: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_reason_class: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) incomplete_paths_count: Option<usize>,
    // Task #26, third and last member of the native `--json` envelope population (the others are
    // `SearchResultJson` and `SearchSummaryNdjson`). Enumerated, not sampled: this symptom has
    // taken four fixes precisely because each one closed the route that happened to be reported.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) path_was_defaulted: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) scope_note: Option<&'static str>,
    pub(crate) pipeline: &'a GpuPipelineStats,
    pub(crate) matches: Vec<SearchMatchJson>,
}

#[derive(Serialize)]
pub(crate) struct ApplyVerifyJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) checkpoint: Option<&'a CheckpointCreateSummary>,
    pub(crate) audit_manifest: Option<&'a AuditManifestSummary>,
    pub(crate) plan: &'a tensor_grep_rs::backend_ast::RewritePlan,
    pub(crate) verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
    pub(crate) validation: Option<&'a ValidationSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) rollback: Option<&'a ValidationRollbackSummary>,
}

#[derive(Serialize)]
pub(crate) struct RewriteDiffJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) plan: &'a tensor_grep_rs::backend_ast::RewritePlan,
    pub(crate) diff: String,
}

#[derive(Serialize)]
pub(crate) struct BatchApplyVerifyJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) checkpoint: Option<&'a CheckpointCreateSummary>,
    pub(crate) audit_manifest: Option<&'a AuditManifestSummary>,
    pub(crate) plan: &'a BatchRewritePlan,
    pub(crate) verification: Option<&'a tensor_grep_rs::backend_ast::VerifyResult>,
    pub(crate) validation: Option<&'a ValidationSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) rollback: Option<&'a ValidationRollbackSummary>,
}

#[derive(Serialize)]
pub(crate) struct BatchRewriteDiffJson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) plan: &'a BatchRewritePlan,
    pub(crate) diff: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct CheckpointCreateSummary {
    pub(crate) checkpoint_id: String,
    pub(crate) mode: String,
    pub(crate) root: String,
    pub(crate) scope: String,
    pub(crate) original_path: String,
    pub(crate) created_at: String,
    pub(crate) file_count: usize,
}

#[cfg(windows)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub(crate) struct CheckpointIndexRecord {
    pub(crate) version: u32,
    pub(crate) checkpoint_id: String,
    pub(crate) mode: String,
    pub(crate) root: String,
    pub(crate) created_at: String,
    pub(crate) file_count: usize,
}

#[cfg(windows)]
#[derive(Debug, Clone, Serialize)]
pub(crate) struct CheckpointMetadata {
    pub(crate) version: u32,
    pub(crate) checkpoint_id: String,
    pub(crate) mode: String,
    pub(crate) root: String,
    pub(crate) scope: String,
    pub(crate) original_path: String,
    pub(crate) created_at: String,
    pub(crate) file_count: usize,
    pub(crate) entries: BTreeMap<String, bool>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ValidationSummary {
    pub(crate) success: bool,
    pub(crate) commands: Vec<ValidationCommandResult>,
    /// True when at least one of --lint-cmd/--test-cmd had more edited-file targets than
    /// --max-validation-targets allowed and some targets were skipped (audit #34). Fail-closed
    /// VISIBLE: the cap silently dropping targets would otherwise look like a clean pass.
    pub(crate) validation_targets_truncated: bool,
    /// The real number of edited-file validation targets discovered before any cap was applied
    /// (the max across --lint-cmd/--test-cmd, since both usually see the same edited-file set).
    pub(crate) validation_targets_total: usize,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ValidationCommandResult {
    pub(crate) kind: &'static str,
    pub(crate) command: String,
    pub(crate) success: bool,
    pub(crate) exit_code: Option<i32>,
    pub(crate) stdout: String,
    pub(crate) stderr: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct AuditManifestSummary {
    pub(crate) path: String,
    pub(crate) file_count: usize,
    pub(crate) applied_edit_count: usize,
    pub(crate) signed: bool,
    pub(crate) signature_kind: Option<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RewriteAuditManifest {
    pub(crate) version: u32,
    pub(crate) kind: &'static str,
    pub(crate) created_at: String,
    pub(crate) lang: String,
    pub(crate) path: String,
    pub(crate) plan_total_edits: usize,
    pub(crate) applied_edit_ids: Vec<String>,
    pub(crate) previous_manifest_sha256: Option<String>,
    pub(crate) checkpoint: Option<CheckpointCreateSummary>,
    pub(crate) validation: Option<ValidationSummary>,
    pub(crate) files: Vec<RewriteAuditManifestFile>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) manifest_sha256: Option<String>,
    pub(crate) signature: Option<AuditManifestSignature>,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct RewriteAuditManifestFile {
    pub(crate) path: String,
    pub(crate) edit_ids: Vec<String>,
    pub(crate) before_sha256: String,
    pub(crate) after_sha256: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct AuditManifestSignature {
    pub(crate) kind: &'static str,
    pub(crate) key_path: String,
    pub(crate) value: String,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct RewriteAuditManifestRead {
    pub(crate) kind: String,
    pub(crate) previous_manifest_sha256: Option<String>,
    pub(crate) manifest_sha256: Option<String>,
    pub(crate) signature: Option<AuditManifestSignatureRead>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct AuditManifestSignatureRead {
    pub(crate) kind: String,
    // Retained for deserialization/forward-compatibility but intentionally NOT used for
    // verification: the key must be supplied out-of-band via --signing-key, never read
    // from the manifest being verified (audit S2).
    #[allow(dead_code)]
    pub(crate) key_path: String,
    pub(crate) value: String,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct AuditManifestVerifyChecks {
    pub(crate) digest_valid: bool,
    pub(crate) chain_valid: bool,
    pub(crate) signature_valid: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct AuditManifestVerifyJson {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) manifest_path: String,
    pub(crate) signing_key_path: Option<String>,
    pub(crate) previous_manifest_path: Option<String>,
    pub(crate) kind: Option<String>,
    pub(crate) manifest_sha256: Option<String>,
    pub(crate) previous_manifest_sha256: Option<String>,
    pub(crate) checks: AuditManifestVerifyChecks,
    pub(crate) signature_kind: Option<String>,
    pub(crate) valid: bool,
    pub(crate) errors: Vec<String>,
}

#[derive(Debug, Clone)]
pub(crate) struct BatchRewriteConfig {
    pub(crate) rewrites: Vec<BatchRewriteRule>,
    pub(crate) verify: bool,
}

/// `text`/`bytes` mirror `NativeJsonMatch`'s rg-parity protocol (task #266): valid-UTF-8 content
/// in `text`, otherwise a base64 `bytes` fallback -- exactly one present, computed via
/// `native_json_text_fields`. `raw` is the byte-exact source of truth every producer must
/// populate (`#[serde(skip)]`: never itself part of the JSON/NDJSON wire shape) -- both the
/// plain-text writer (`emit_plain_search_matches_with_line_number`) and the dedup key
/// (`unique_line_matches`) read `raw` directly instead of `text`, so a producer that supplies
/// genuinely non-UTF-8 bytes (currently: the multi-pattern native path, `NativeSearchMatch`/
/// `NativeMultiPatternMatch`) is never silently corrupted OR silently deduplicated against an
/// unrelated match that merely shares the same `text: None`. A producer whose own source is
/// ALWAYS valid UTF-8 by construction (TrigramIndex, AST, the GPU sidecar/native paths) simply
/// sets `raw` to `text`'s own UTF-8 bytes -- `native_json_text_fields` then always returns
/// `Some(text)`/`None`, identical to this struct's pre-#266 behavior for those producers.
#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchMatchJson {
    pub(crate) file: String,
    pub(crate) line: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) text: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) bytes: Option<String>,
    #[serde(skip)]
    pub(crate) raw: Vec<u8>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) range: Option<SearchRangeJson>,
    #[serde(rename = "metaVariables", skip_serializing_if = "Option::is_none")]
    pub(crate) meta_variables: Option<SearchMetaVariablesJson>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) pattern_text: Option<String>,
}

/// Builds the `text`/`bytes`/`raw` trio for a `SearchMatchJson` (or `SearchMatchNdjson`) from a
/// producer whose own source `String` is ALWAYS valid UTF-8 by construction -- TrigramIndex
/// (persists `String`s), AST (`std::fs::read_to_string` fails closed on invalid UTF-8 before this
/// point), and the GPU sidecar/native paths (JSON-deserialized, itself UTF-8). `native_json_text_
/// fields` always returns `Some(text)`/`None` for these, so this is equivalent to (and clearer
/// than) routing them through that helper -- it exists so each of those 4 call sites states its
/// own exemption inline instead of repeating the same `native_json_text_fields(s.as_bytes())`
/// call whose `None` branch can never actually fire for them.
pub(crate) fn guaranteed_utf8_match_fields(
    text: String,
) -> (Option<String>, Option<String>, Vec<u8>) {
    let raw = text.clone().into_bytes();
    (Some(text), None, raw)
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchRangeJson {
    #[serde(rename = "byteOffset")]
    pub(crate) byte_offset: SearchByteOffsetJson,
    pub(crate) start: SearchPositionJson,
    pub(crate) end: SearchPositionJson,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchByteOffsetJson {
    pub(crate) start: usize,
    pub(crate) end: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchPositionJson {
    pub(crate) line: usize,
    pub(crate) column: usize,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchMetaVariablesJson {
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub(crate) single: BTreeMap<String, SearchMetaVariableJson>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub(crate) multi: BTreeMap<String, Vec<SearchMetaVariableJson>>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct SearchMetaVariableJson {
    pub(crate) text: String,
    pub(crate) range: SearchRangeJson,
}

#[derive(Debug, Clone)]
pub(crate) struct AstSourceContext {
    pub(crate) line_starts: Vec<usize>,
}

#[derive(Serialize)]
pub(crate) struct SearchMatchNdjson<'a> {
    pub(crate) version: u32,
    pub(crate) routing_backend: &'static str,
    pub(crate) routing_reason: &'static str,
    pub(crate) sidecar_used: bool,
    pub(crate) requested_gpu_device_ids: Vec<i32>,
    pub(crate) routing_gpu_device_ids: Vec<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_evidence_status: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) gpu_proof: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) native_gpu_unavailable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) not_gpu_proof_reason: Option<String>,
    pub(crate) query: &'a str,
    pub(crate) path: &'a str,
    pub(crate) file: &'a str,
    pub(crate) line: usize,
    // Same text/bytes protocol as `SearchMatchJson` above (task #266).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) text: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) bytes: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) pattern_id: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub(crate) pattern_text: Option<&'a str>,
}

#[derive(Deserialize)]
pub(crate) struct GpuSidecarSearchPayload {
    pub(crate) total_matches: usize,
    pub(crate) total_files: usize,
    pub(crate) matches: Vec<GpuSidecarSearchMatch>,
    #[serde(default)]
    pub(crate) routing_gpu_device_ids: Vec<u32>,
}

#[derive(Deserialize)]
pub(crate) struct GpuSidecarSearchMatch {
    pub(crate) file: String,
    pub(crate) line_number: usize,
    pub(crate) text: String,
    #[serde(default)]
    pub(crate) pattern_id: Option<usize>,
    #[serde(default)]
    pub(crate) pattern_text: Option<String>,
}
