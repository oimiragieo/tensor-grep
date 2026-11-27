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

pub(crate) struct GpuSearchParams<'a> {
    pub(crate) patterns: &'a [String],
    pub(crate) query: &'a str,
    pub(crate) path: &'a str,
    #[cfg_attr(not(feature = "cuda"), allow(dead_code))]
    pub(crate) line_number: bool,
    pub(crate) ignore_case: bool,
    pub(crate) smart_case: bool,
    pub(crate) fixed_strings: bool,
    pub(crate) invert_match: bool,
    pub(crate) count: bool,
    pub(crate) context: Option<usize>,
    pub(crate) max_count: Option<usize>,
    pub(crate) word_regexp: bool,
    pub(crate) globs: Vec<String>,
    pub(crate) hidden: bool,
    pub(crate) max_depth: Option<usize>,
    pub(crate) text: bool,
    pub(crate) no_ignore: bool,
    pub(crate) gpu_device_ids: &'a [i32],
    pub(crate) json: bool,
    pub(crate) ndjson: bool,
    pub(crate) verbose: bool,
    // Task #131 F3 (Backend Fail-Closed Contract): these 5 fields used to have no home on this
    // struct at all, so every GPU-routed request silently dropped them -- `tg PAT
    // --gpu-device-ids 0 --replace X` printed "falling back to native CPU" and ran WITHOUT
    // --replace, exit 0. `replace`/`only_matching` mirror `NativeSearchConfig`'s own fields (it
    // CAN express them); `max_filesize`/`color`/`no_ignore_vcs` have no `NativeSearchConfig`
    // equivalent at all, so the CPU-fallback route must redirect to the rg passthrough or refuse
    // outright when one of those three is set (see `gpu_cpu_fallback_unhonorable_flag`) -- never
    // silently run without them.
    pub(crate) replace: Option<String>,
    pub(crate) only_matching: bool,
    pub(crate) max_filesize: Option<String>,
    pub(crate) color: Option<String>,
    pub(crate) no_ignore_vcs: bool,
    // Audit #105: whether the caller omitted an explicit PATH positional. Threaded into
    // `native_search_config_for_gpu_params`'s `NativeSearchConfig::path_was_implicit` (and the
    // rg_fallback `RipgrepSearchArgs` in `handle_gpu_native_search`) so the CPU fallback this
    // struct eventually reaches, when GPU routing is explicitly requested via
    // `--gpu-device-ids` but unavailable, still gets the native-CPU implicit-walk-ceiling gate.
    // "GPU search requires exactly one path root" (`request.paths.len() != 1`) does NOT imply
    // explicit -- the implicit default is itself a single `["."]` root, so that check alone
    // cannot be used as a stand-in for this field.
    pub(crate) path_was_implicit: bool,
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if let Some(reason) = gpu_native_fallback_reason(&params) {
        if params.verbose {
            eprintln!("[gpu-native] falling back to Python sidecar: {reason}");
        }
        return handle_gpu_sidecar_search(params);
    }

    handle_gpu_native_search(params)
}

#[cfg(not(feature = "cuda"))]
pub(crate) fn handle_gpu_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if explicit_gpu_sidecar_is_available() {
        return handle_gpu_sidecar_search(params);
    }

    handle_gpu_unavailable_cpu_fallback(
        params,
        "native GPU unavailable in this binary; no CUDA-native front door is available",
    )
}

/// N3 (task #131 F3): mirror `index_flag_violations`'s established `--color` rule. The native CPU
/// engine (`NativeSearchConfig`, which has no `color` field at all) is a plain-text emitter that
/// never writes ANSI escapes, so `--color never`/`--color auto` restate what it already does and
/// are honorable no-ops; only `--color always` (or any unrecognized value) demands coloring this
/// engine cannot produce. Keeping this identical to the index path avoids two divergent `--color`
/// contracts for the same plain-text emitter.
pub(crate) fn gpu_color_is_unhonorable(color: Option<&str>) -> bool {
    matches!(color, Some(mode) if mode != "never" && mode != "auto")
}

/// Task #131 F3 (Backend Fail-Closed Contract): `NativeSearchConfig` -- the engine backing every
/// CPU fallback in `handle_gpu_unavailable_cpu_fallback` -- has no field for any of these three
/// (see its definition in `native_search.rs`), unlike `replace`/`only_matching` which it DOES
/// carry (threaded via `native_search_config_for_gpu_params`). Silently building a
/// `NativeSearchConfig` from a `GpuSearchParams` that has one of these three set would run the
/// search successfully while dropping the flag (exit 0, wrong output). Returns the flag's CLI
/// spelling so the caller can redirect to the rg passthrough (which DOES carry all three, see
/// `RipgrepSearchArgs`) or refuse outright -- never silently ignore it. `--color never`/`auto` are
/// deliberately excluded as honorable no-ops (see `gpu_color_is_unhonorable`).
pub(crate) fn gpu_cpu_fallback_unhonorable_flag(
    params: &GpuSearchParams<'_>,
) -> Option<&'static str> {
    if params.max_filesize.is_some() {
        Some("--max-filesize")
    } else if gpu_color_is_unhonorable(params.color.as_deref()) {
        Some("--color")
    } else if params.no_ignore_vcs {
        Some("--no-ignore-vcs")
    } else {
        None
    }
}

// `exit_gpu_cpu_fallback_flag_unhonorable` lives in `tensor_grep_rs::search_error`.

/// Builds a `RipgrepSearchArgs` from a `GpuSearchParams` for the fail-closed CPU-fallback redirect
/// (task #131 F3). Every field this function does NOT set is one `GpuSearchParams` itself never
/// carries -- by construction, any flag with that property already routed the request away from
/// the GPU branch entirely before `GpuSearchParams` was built (`search_requires_ripgrep_passthrough`
/// / the disjoint PositionalCli/SearchArgs surfaces), so defaulting them here is not a second
/// silent drop.
pub(crate) fn ripgrep_args_for_gpu_params(params: &GpuSearchParams<'_>) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        patterns: params.patterns.to_vec(),
        paths: if params.path_was_implicit {
            Vec::new()
        } else {
            vec![params.path.to_string()]
        },
        path_was_implicit: params.path_was_implicit,
        ignore_case: params.ignore_case,
        smart_case: params.smart_case,
        fixed_strings: params.fixed_strings,
        invert_match: params.invert_match,
        count: params.count,
        line_number: params.line_number,
        no_line_number: !params.line_number,
        only_matching: params.only_matching,
        max_count: params.max_count,
        // M1 (gate must-fix): GpuSearchParams carries the single collapsed `context`
        // (`search_effective_context(&args)` at the tg-search sites). Forward it here so a
        // `--gpu-device-ids N -C 3 --color always` search redirected to rg does not silently drop
        // `-C 3` -- the native CPU fallback honored it via
        // `native_search_config_for_gpu_params` (before/after_context = params.context.unwrap_or(0)),
        // so dropping it in this rg redirect would trade a color-drop for a context-drop = the same
        // silent-wrong-output class this PR closes. Leaving before_context/after_context at their
        // `None` default mirrors that symmetric native behavior (GpuSearchParams has no separate
        // before/after; the collapsed value already folds `-A`/`-B` via search_effective_context).
        context: params.context,
        word_regexp: params.word_regexp,
        globs: params.globs.clone(),
        no_ignore: params.no_ignore,
        hidden: params.hidden,
        max_depth: params.max_depth,
        text: params.text,
        color: params.color.clone(),
        replace: params.replace.clone(),
        no_ignore_vcs: params.no_ignore_vcs,
        max_filesize: params.max_filesize.clone(),
        ..RipgrepSearchArgs::default()
    }
}

pub(crate) fn handle_gpu_unavailable_cpu_fallback(
    params: GpuSearchParams<'_>,
    warning: &str,
) -> anyhow::Result<()> {
    eprintln!(
        "warning: {warning}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
    );
    let rg_available = ripgrep_is_available();
    let decision =
        RoutingDecision::native_cpu_gpu_fallback(rg_available, params.json || params.ndjson);

    // Task #131 F3: `--max-filesize`/`--color`/`--no-ignore-vcs` cannot be expressed by the
    // native CPU engine this fallback would otherwise silently run. Redirect to rg (when its
    // output shape is usable here) or refuse outright -- never drop the flag.
    if let Some(flag_name) = gpu_cpu_fallback_unhonorable_flag(&params) {
        if !decision.allow_rg_fallback {
            search_error::exit_gpu_cpu_fallback_flag_unhonorable(
                flag_name,
                rg_available,
                params.json,
                params.ndjson,
            );
        }
        if params.verbose {
            emit_verbose_metadata(RoutingDecision::ripgrep());
        }
        let rg_args = ripgrep_args_for_gpu_params(&params);
        let exit_code = execute_ripgrep_search(&rg_args)?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    let pattern = params.patterns.first().map_or(params.query, String::as_str);
    let cpu_config = native_search_config_for_gpu_params(&params, pattern, decision);
    if cpu_config.verbose {
        emit_verbose_metadata(decision);
    }
    if params.patterns.len() > 1 {
        let (matches, incomplete_paths, binary_match_files) =
            collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
        return emit_multi_pattern_native_results(
            NativeSearchOutputOptions {
                decision,
                query: params.query,
                path: params.path,
                requested_gpu_device_ids: params.gpu_device_ids,
                json: params.json,
                ndjson: params.ndjson,
                count: params.count,
                line_number: params.line_number,
                path_was_implicit: params.path_was_implicit,
            },
            matches,
            incomplete_paths,
            binary_match_files,
        );
    }
    run_native_search_with_optional_rg_fallback(cpu_config, None)
}

#[cfg(not(feature = "cuda"))]
pub(crate) fn explicit_gpu_sidecar_is_available() -> bool {
    if env::var_os("TG_SIDECAR_SCRIPT").is_some() {
        return true;
    }
    env::var_os("TG_SIDECAR_PYTHON")
        .map(PathBuf::from)
        .is_some_and(|path| path.exists())
}

// GPU Phase-0 gate-nit #172 NIT-4 / MF-1: `GpuRouteFailureKind`, `GpuRouteFailure`,
// `sanitize_cuda_detail`, and `classify_gpu_route_failure` below are gated `any(feature = "cuda",
// test)` rather than plain `feature = "cuda"` so `cargo test` (default features, no cuda) can
// compile and RUN the 3 `classify_gpu_route_failure_*` tests in `mod tests` above -- previously
// those tests were ALSO `#[cfg(feature = "cuda")]`-gated, so a default `cargo test` silently
// never executed them at all. A bare un-gate of just the tests would not compile (the classifier
// and its types would still be absent by default); a bare un-gate of the classifier alone would
// leave it with zero callers in the default build (its production callers stay cuda-gated) and
// fail `cargo clippy -- -D warnings` on `dead_code`. Gating the definitions themselves on
// `any(feature = "cuda", test)` solves both: present whenever cuda is enabled (unchanged
// production behavior) OR whenever `cfg(test)` is set (so the tests below have something to call
// and are not themselves dead code), absent in the default clippy/release build (no dead_code).
#[cfg(any(feature = "cuda", test))]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GpuRouteFailureKind {
    Unavailable,
    Fatal,
}

#[cfg(any(feature = "cuda", test))]
pub(crate) struct GpuRouteFailure {
    pub(crate) kind: GpuRouteFailureKind,
    pub(crate) message: String,
}

#[cfg(feature = "cuda")]
pub(crate) fn gpu_native_fallback_reason(params: &GpuSearchParams<'_>) -> Option<&'static str> {
    if gpu_params_require_case_insensitive_matching(params) {
        Some("case-insensitive searches are not yet supported by native GPU routing")
    } else if params.text {
        Some("binary-as-text searches are not yet supported by native GPU routing")
    } else if params
        .patterns
        .iter()
        .any(|pattern| pattern_contains_line_terminator(pattern))
    {
        Some("line-terminator patterns require CPU or sidecar routing")
    } else if params.invert_match {
        Some("invert-match searches are not yet supported by native GPU routing")
    } else if params.context.is_some() {
        Some("context line searches are not yet supported by native GPU routing")
    } else if params.max_count.is_some() {
        Some("max-count searches are not yet supported by native GPU routing")
    } else if params.word_regexp {
        Some("word-boundary searches are not yet supported by native GPU routing")
    } else if !params.fixed_strings && patterns_require_regex_engine(params.patterns) {
        Some("regex patterns still require the Python GPU sidecar")
    } else if params.replace.is_some() {
        // Task #131 F3: `GpuSearchParams` used to have no `replace` field at all, so this check
        // was structurally impossible before -- any `--replace` combined with `--gpu-device-ids`
        // silently reached native-GPU routing (or its CPU fallback) with the replacement dropped.
        Some("--replace searches are not yet supported by native GPU routing")
    } else if params.only_matching {
        Some("--only-matching searches are not yet supported by native GPU routing")
    } else if params.max_filesize.is_some() {
        Some("--max-filesize is not yet supported by native GPU routing")
    } else if params.color.is_some() {
        Some("--color is not yet supported by native GPU routing")
    } else if params.no_ignore_vcs {
        Some("--no-ignore-vcs is not yet supported by native GPU routing")
    } else {
        None
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn pattern_contains_line_terminator(pattern: &str) -> bool {
    pattern
        .as_bytes()
        .iter()
        .any(|byte| matches!(byte, b'\n' | b'\r'))
}

#[cfg(feature = "cuda")]
pub(crate) fn gpu_params_require_case_insensitive_matching(params: &GpuSearchParams<'_>) -> bool {
    if params.ignore_case {
        return true;
    }
    params.smart_case
        && params
            .patterns
            .iter()
            .any(|pattern| smart_case_pattern_is_case_insensitive(pattern))
}

#[cfg(feature = "cuda")]
pub(crate) fn patterns_require_regex_engine(patterns: &[String]) -> bool {
    patterns
        .iter()
        .any(|pattern| pattern_requires_regex_engine(pattern))
}

#[cfg(feature = "cuda")]
pub(crate) fn pattern_requires_regex_engine(pattern: &str) -> bool {
    let mut escaped = false;
    for ch in pattern.chars() {
        if escaped {
            return true;
        }
        match ch {
            '\\' => escaped = true,
            '.' | '^' | '$' | '*' | '+' | '?' | '(' | ')' | '[' | ']' | '{' | '}' | '|' => {
                return true;
            }
            _ => {}
        }
    }
    escaped
}

#[cfg(feature = "cuda")]
pub(crate) fn simulated_gpu_route_failure() -> Option<GpuRouteFailure> {
    let value = env::var("TG_TEST_CUDA_BEHAVIOR").ok()?;
    let trimmed = value.trim();
    if trimmed.eq_ignore_ascii_case("no-devices") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: "CUDA is unavailable: no usable GPU devices were found".to_string(),
        });
    }
    if let Some(reason) = trimmed.strip_prefix("init-failure:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!(
                "CUDA initialization failed: {}",
                sanitize_cuda_detail(reason)
            ),
        });
    }
    if let Some(reason) = trimmed.strip_prefix("nvrtc-failure:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("CUDA kernel compilation failed: {}", reason.trim()),
        });
    }
    if trimmed.eq_ignore_ascii_case("timeout") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "GPU operation timed out after 30s".to_string(),
        });
    }
    if trimmed.eq_ignore_ascii_case("oom") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "CUDA out of memory while allocating the requested GPU buffer".to_string(),
        });
    }
    if let Some(request) = trimmed.strip_prefix("oom:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("CUDA out of memory while allocating {}", request.trim()),
        });
    }
    if let Some(duration) = trimmed.strip_prefix("timeout:") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: format!("GPU operation timed out after {}", duration.trim()),
        });
    }
    if trimmed.eq_ignore_ascii_case("device-in-use") {
        return Some(GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: "CUDA initialization failed: the selected GPU is currently in use".to_string(),
        });
    }
    None
}

#[cfg(any(feature = "cuda", test))]
pub(crate) fn sanitize_cuda_detail(raw: &str) -> String {
    let compact = raw.replace(['\r', '\n'], " ");
    let lower = compact.to_ascii_lowercase();
    if lower.contains("driver") && lower.contains("version") {
        return "driver version is too old".to_string();
    }
    if lower.contains("busy") || lower.contains("in use") {
        return "the selected GPU is currently in use".to_string();
    }
    if compact.contains("CUDA_ERROR") || compact.contains("DriverError") {
        return "the CUDA runtime reported an initialization error".to_string();
    }
    compact
        .trim()
        .trim_matches(|ch| ch == ':' || ch == '.')
        .to_string()
}

#[cfg(any(feature = "cuda", test))]
pub(crate) fn classify_gpu_route_failure(raw_message: &str) -> GpuRouteFailure {
    if raw_message.starts_with("CUDA is unavailable:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: raw_message.to_string(),
        };
    }
    // GPU-P0-3 (#171): `validate_requested_cuda_device_ids` raises this exact prefix for an
    // out-of-range --gpu-device-ids request. It is a Fatal (user-input) reason in its own right
    // -- catching it here, before the generic "CUDA initialization failed:" arm and the
    // lowercase-substring fallback below, keeps the catch-all from relabeling it as a driver/
    // hardware initialization problem it never was.
    if raw_message.starts_with("invalid CUDA device id") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA initialization failed:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA kernel compilation failed:") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("CUDA out of memory") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }
    if raw_message.starts_with("GPU operation timed out") {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: raw_message.to_string(),
        };
    }

    let lower = raw_message.to_ascii_lowercase();
    if lower.contains("no usable gpu devices")
        || lower.contains("no cuda devices")
        || lower.contains("no device")
        || lower.contains("cuda is unavailable")
    {
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Unavailable,
            message: "CUDA is unavailable: no usable GPU devices were found".to_string(),
        };
    }
    if lower.contains("out of memory") || lower.contains("cuda_error_out_of_memory") {
        let detail = raw_message.trim();
        return GpuRouteFailure {
            kind: GpuRouteFailureKind::Fatal,
            message: if detail.is_empty() {
                "CUDA out of memory while allocating the requested GPU buffer".to_string()
            } else {
                format!("CUDA out of memory: {detail}")
            },
        };
    }

    GpuRouteFailure {
        kind: GpuRouteFailureKind::Fatal,
        message: format!(
            "CUDA initialization failed: {}",
            sanitize_cuda_detail(raw_message)
        ),
    }
}

// GPU-P0 gate-nit #172 NIT-3: `classify_gpu_route_failure`'s `Fatal` kind is deliberately coarse
// (kind + human message only) -- both emission sites below used to collapse EVERY Fatal straight
// to one native error kind ("gpu_fatal"), including the out-of-range --gpu-device-ids arm above
// (already given its own message-level branch in `classify_gpu_route_failure` so it is never
// relabeled as an init failure). The doctor/agent-capsule Python layer maps native error kinds to
// a status; "gpu_fatal" reads as "GPU unavailable", so a typo'd device id misreported as a
// capability gap instead of a user-input error. This is a thin, pure, directly-testable string
// check on the ALREADY-CLASSIFIED Fatal message -- deliberately NOT a 3rd `GpuRouteFailureKind`
// variant, which would force every `match failure.kind` call site's Fatal arm to add a case for a
// distinction only the WIRE error-kind string needs to make; the two enum variants remain the
// coarse "should we CPU-fallback or hard-fail" signal.
#[cfg(any(feature = "cuda", test))]
pub(crate) fn gpu_fatal_native_error_kind(message: &str) -> &'static str {
    if message.starts_with("invalid CUDA device id") {
        "gpu_invalid_device_id"
    } else {
        "gpu_fatal"
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn gpu_native_config_from_internal_args(
    args: &GpuNativeStatsArgs,
) -> GpuNativeSearchConfig {
    GpuNativeSearchConfig {
        patterns: args.patterns.clone(),
        paths: vec![args.path.clone()],
        no_ignore: args.no_ignore,
        glob: args.globs.clone(),
        hidden: false,
        max_depth: None,
        max_batch_bytes: args.max_batch_bytes,
        // `GpuNativeStatsArgs::path` is a required `#[arg(long)]` (no default_value) -- this
        // diagnostic command always has an explicit, deliberately-scoped PATH, never an implicit
        // one, so the #109 ceiling must never fire here regardless of tree size.
        path_was_implicit: false,
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn gpu_native_config_from_graph_args(args: &GpuCudaGraphArgs) -> GpuNativeSearchConfig {
    GpuNativeSearchConfig {
        patterns: args.patterns.clone(),
        paths: vec![args.path.clone()],
        no_ignore: args.no_ignore,
        glob: args.globs.clone(),
        hidden: false,
        max_depth: None,
        max_batch_bytes: args.max_batch_bytes,
        // `GpuCudaGraphArgs::path` is a required `#[arg(long)]` (no default_value) -- this
        // benchmark command always has an explicit, deliberately-scoped PATH, never an implicit
        // one, so the #109 ceiling must never fire here regardless of tree size.
        path_was_implicit: false,
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_native_stats_command(args: GpuNativeStatsArgs) -> anyhow::Result<()> {
    let mut stats = gpu_native_search_paths_multi(
        &gpu_native_config_from_internal_args(&args),
        &args.gpu_device_ids,
    )?;
    if args.summary_only {
        stats.matches.clear();
    }
    println!("{}", serde_json::to_string_pretty(&stats)?);
    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_transfer_benchmark_command(
    args: GpuTransferBenchArgs,
) -> anyhow::Result<()> {
    let benchmark = match args.memory_kind {
        GpuTransferMemoryKind::Pinned => benchmark_pinned_transfer_throughput(
            args.device_id,
            args.total_bytes,
            args.batch_bytes,
        )?,
        GpuTransferMemoryKind::Pageable => benchmark_pageable_transfer_throughput(
            args.device_id,
            args.total_bytes,
            args.batch_bytes,
        )?,
    };
    println!("{}", serde_json::to_string_pretty(&benchmark)?);
    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_cuda_graph_benchmark_command(
    args: GpuCudaGraphArgs,
) -> anyhow::Result<()> {
    let benchmark = benchmark_cuda_graph_search_paths(
        &gpu_native_config_from_graph_args(&args),
        args.device_id,
    )?;
    println!("{}", serde_json::to_string_pretty(&benchmark)?);
    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_oom_probe_command(args: GpuOomProbeArgs) -> anyhow::Result<()> {
    match probe_device_allocation(args.device_id, args.bytes) {
        Ok(()) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&serde_json::json!({
                    "status": "PASS",
                    "device_id": args.device_id,
                    "bytes": args.bytes,
                }))?
            );
            Ok(())
        }
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            eprintln!("{}", failure.message);
            std::process::exit(2);
        }
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn execute_gpu_native_route(
    params: &GpuSearchParams<'_>,
    decision: RoutingDecision,
    device_ids: &[i32],
) -> anyhow::Result<()> {
    if let Some(simulated) = simulated_gpu_route_failure() {
        anyhow::bail!(simulated.message);
    }

    if params.verbose {
        emit_verbose_metadata(decision);
    }

    let config = GpuNativeSearchConfig {
        patterns: params.patterns.to_vec(),
        paths: vec![PathBuf::from(params.path)],
        no_ignore: params.no_ignore,
        glob: params.globs.clone(),
        hidden: params.hidden,
        max_depth: params.max_depth,
        max_batch_bytes: None,
        // Audit #109 fix: this field did not exist on `GpuNativeSearchConfig` at all, so the
        // GPU-native engine had no equivalent of the #105 native-CPU implicit-walk ceiling --
        // `params.path_was_implicit` was already threaded into the CPU-fallback redirects
        // (`ripgrep_args_for_gpu_params`, `native_search_config_for_gpu_params`) but silently
        // dropped here, the one construction site that feeds the actual cuda kernel walk.
        path_was_implicit: params.path_was_implicit,
    };

    let stats = gpu_native_search_paths_multi(&config, device_ids)?;
    if params.verbose {
        emit_gpu_native_verbose(&stats);
    }

    if params.json {
        emit_gpu_native_json_results(decision, params, &stats)?;
    } else if params.ndjson {
        emit_ndjson_search_results(
            decision,
            params.query,
            params.path,
            params.gpu_device_ids,
            gpu_native_match_json_entries(&stats),
            // Task 316: this used to pass `None` with a comment claiming "this route observed no
            // walk of its own". That was FALSE -- `gpu_native_search_paths_multi` walks via
            // `collect_search_files` -> `collect_walked_files`; the count simply had nowhere to go.
            // It does now, and a comment asserting a false fact about its own route is exactly the
            // failure the gpu_native.rs note warned about. `None` still means "cannot report",
            // never "complete", so passing the real count is strictly more honest.
            Some(stats.walk_errors),
            params.path_was_implicit,
            None,
        )?;
    } else if params.count {
        emit_gpu_native_count_results(params, &stats)?;
    } else {
        emit_gpu_native_plain_results(params, &stats)?;
    }

    // Task 316, mirroring #818 on the multi-pattern route: an incomplete walk is checked BEFORE
    // the no-match branch, because a scan that could not read part of the tree must never report
    // a confident "0 matches" (exit 1) -- that reads as a genuine absence and is the exact
    // green-light-to-delete-live-code this campaign exists to prevent. Same shared predicate as
    // the envelope above, so disclosure and exit code cannot be derived independently.
    if walk_was_incomplete(Some(stats.walk_errors)) {
        std::process::exit(2);
    }

    if stats.total_matches == 0 {
        std::process::exit(1);
    }

    Ok(())
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_auto_gpu_search(
    params: GpuSearchParams<'_>,
    cpu_fallback_config: NativeSearchConfig,
    rg_fallback: Option<RipgrepSearchArgs>,
) -> anyhow::Result<()> {
    let auto_device_ids = [0];
    match execute_gpu_native_route(
        &params,
        RoutingDecision::native_gpu_auto(),
        &auto_device_ids,
    ) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!(
                        "warning: {}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof",
                        failure.message
                    );
                    if cpu_fallback_config.verbose {
                        emit_verbose_metadata(RoutingDecision::native_cpu_gpu_fallback(
                            ripgrep_is_available(),
                            cpu_fallback_config.json || cpu_fallback_config.ndjson,
                        ));
                    }
                    if params.patterns.len() > 1 {
                        let (matches, incomplete_paths, binary_match_files) =
                            collect_native_multi_pattern_matches(
                                params.patterns,
                                cpu_fallback_config,
                            )?;
                        return emit_multi_pattern_native_results(
                            NativeSearchOutputOptions {
                                decision: RoutingDecision::native_cpu_gpu_fallback(
                                    ripgrep_is_available(),
                                    params.json || params.ndjson,
                                ),
                                query: params.query,
                                path: params.path,
                                requested_gpu_device_ids: params.gpu_device_ids,
                                json: params.json,
                                ndjson: params.ndjson,
                                count: params.count,
                                line_number: params.line_number,
                                path_was_implicit: params.path_was_implicit,
                            },
                            matches,
                            incomplete_paths,
                            binary_match_files,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_fallback_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        gpu_fatal_native_error_kind(&failure.message),
                        failure.message,
                    );
                }
            }
        }
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn handle_gpu_native_search(params: GpuSearchParams<'_>) -> anyhow::Result<()> {
    if params.gpu_device_ids.is_empty() {
        return handle_gpu_sidecar_search(params);
    }

    match execute_gpu_native_route(
        &params,
        RoutingDecision::native_gpu_explicit(),
        params.gpu_device_ids,
    ) {
        Ok(()) => Ok(()),
        Err(err) => {
            let failure = classify_gpu_route_failure(&err.to_string());
            match failure.kind {
                GpuRouteFailureKind::Unavailable => {
                    eprintln!(
                        "warning: {}; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof",
                        failure.message
                    );
                    let rg_available = ripgrep_is_available();
                    let fallback_decision = RoutingDecision::native_cpu_gpu_fallback(
                        rg_available,
                        params.json || params.ndjson,
                    );
                    let cpu_config = native_search_config_for_gpu_params(
                        &params,
                        &params.patterns[0],
                        fallback_decision,
                    );
                    let rg_fallback =
                        fallback_decision
                            .allow_rg_fallback
                            .then(|| RipgrepSearchArgs {
                                files: false,
                                json: false,
                                ignore_case: params.ignore_case,
                                fixed_strings: params.fixed_strings,
                                no_fixed_strings: false,
                                invert_match: params.invert_match,
                                no_invert_match: false,
                                count: params.count,
                                count_matches: false,
                                line_number: params.line_number,
                                no_line_number: false,
                                column: false,
                                only_matching: false,
                                context: params.context,
                                before_context: None,
                                after_context: None,
                                max_count: params.max_count,
                                word_regexp: params.word_regexp,
                                smart_case: params.smart_case,
                                globs: params.globs.clone(),
                                ignore: false,
                                no_ignore: params.no_ignore,
                                no_ignore_dot: false,
                                no_ignore_exclude: false,
                                no_ignore_files: false,
                                no_ignore_global: false,
                                no_ignore_parent: false,
                                hidden: params.hidden,
                                require_git: false,
                                no_hidden: false,
                                follow: false,
                                text: params.text,
                                files_with_matches: false,
                                files_without_match: false,
                                file_types: Vec::new(),
                                color: None,
                                path_separator: None,
                                replace: None,
                                vimgrep: false,
                                passthru: false,
                                no_config: false,
                                sort: None,
                                sort_reverse: None,
                                sort_files: false,
                                max_depth: params.max_depth,
                                null: false,
                                null_data: false,
                                multiline: false,
                                no_multiline: false,
                                multiline_dotall: false,
                                no_multiline_dotall: false,
                                patterns: params.patterns.to_vec(),
                                paths: vec![params.path.to_string()],
                                // Audit #105 fix: this was hardcoded `false` under the incorrect
                                // assumption that "GPU search requires exactly one path root"
                                // (`request.paths.len() != 1` rejected upstream when
                                // `--gpu-device-ids` is set) implies the path is always explicit.
                                // It does not -- an implicit/defaulted root is itself a single
                                // `["."]` path, so `paths.len() != 1` never fires for it, and this
                                // rg fallback would have silently walked an implicit huge root
                                // unbounded. Now threaded from the real signal.
                                path_was_implicit: params.path_was_implicit,
                                no_ignore_vcs: false,
                                pcre2: false,
                                no_pcre2: false,
                                pcre2_unicode: false,
                                no_pcre2_unicode: false,
                                no_crlf: false,
                                no_encoding: false,
                                no_mmap: false,
                                no_pre: false,
                                no_search_zip: false,
                                auto_hybrid_regex: false,
                                no_auto_hybrid_regex: false,
                                unicode: false,
                                no_text: false,
                                no_binary: false,
                                no_follow: false,
                                no_glob_case_insensitive: false,
                                no_ignore_file_case_insensitive: false,
                                ignore_dot: false,
                                ignore_exclude: false,
                                ignore_files: false,
                                ignore_global: false,
                                ignore_messages: false,
                                ignore_parent: false,
                                ignore_vcs: false,
                                no_one_file_system: false,
                                no_block_buffered: false,
                                no_byte_offset: false,
                                no_column: false,
                                no_context_separator: false,
                                no_include_zero: false,
                                no_line_buffered: false,
                                no_max_columns_preview: false,
                                no_trim: false,
                                no_json: false,
                                messages: false,
                                no_stats: false,
                                max_filesize: None,
                            });
                    if cpu_config.verbose {
                        emit_verbose_metadata(fallback_decision);
                    }
                    if params.patterns.len() > 1 {
                        let (matches, incomplete_paths, binary_match_files) =
                            collect_native_multi_pattern_matches(params.patterns, cpu_config)?;
                        return emit_multi_pattern_native_results(
                            NativeSearchOutputOptions {
                                decision: fallback_decision,
                                query: params.query,
                                path: params.path,
                                requested_gpu_device_ids: params.gpu_device_ids,
                                json: params.json,
                                ndjson: params.ndjson,
                                count: params.count,
                                line_number: params.line_number,
                                path_was_implicit: params.path_was_implicit,
                            },
                            matches,
                            incomplete_paths,
                            binary_match_files,
                        );
                    }
                    run_native_search_with_optional_rg_fallback(cpu_config, rg_fallback)
                }
                GpuRouteFailureKind::Fatal => {
                    exit_structured_search_error_if_needed(
                        params.json,
                        params.ndjson,
                        gpu_fatal_native_error_kind(&failure.message),
                        failure.message,
                    );
                }
            }
        }
    }
}

/// Builds the JSON payload sent to the Python GPU sidecar. Extracted to a pure function (task
/// #131 F3) so a unit test can assert field completeness without spawning the sidecar process --
/// this used to omit `replace`/`only_matching`/`max_filesize`/`color`/`no_ignore_vcs`/
/// `line_number` entirely, because `GpuSearchParams` had no fields to read them from.
pub(crate) fn gpu_sidecar_search_payload(params: &GpuSearchParams<'_>) -> serde_json::Value {
    serde_json::json!({
        "pattern": params.patterns.first().cloned().unwrap_or_default(),
        "patterns": params.patterns,
        "path": params.path,
        "ignore_case": params.ignore_case,
        "smart_case": params.smart_case,
        "fixed_strings": params.fixed_strings,
        "invert_match": params.invert_match,
        "count": params.count,
        "context": params.context,
        "max_count": params.max_count,
        "word_regexp": params.word_regexp,
        "globs": params.globs,
        "hidden": params.hidden,
        "max_depth": params.max_depth,
        "text": params.text,
        "no_ignore": params.no_ignore,
        "gpu_device_ids": params.gpu_device_ids,
        "json": params.json || params.ndjson,
        "line_number": params.line_number,
        "replace": params.replace,
        "only_matching": params.only_matching,
        "max_filesize": params.max_filesize,
        "color": params.color,
        "no_ignore_vcs": params.no_ignore_vcs,
    })
}

pub(crate) fn handle_gpu_sidecar_search(params: GpuSearchParams) -> anyhow::Result<()> {
    if params.verbose {
        emit_verbose_metadata(RoutingDecision::gpu_sidecar());
    }

    let payload = gpu_sidecar_search_payload(&params);

    match execute_sidecar_command("gpu_search", vec![], Some(payload)) {
        Ok(result) => {
            if result.exit_code != 0 {
                if let Some(reason) = search_error::classify_gpu_sidecar_unavailable(
                    &result.stderr,
                    "Python sidecar exited",
                ) {
                    let warning = format!("native GPU unavailable: {reason}");
                    return handle_gpu_unavailable_cpu_fallback(params, &warning);
                }
                if result.stdout.trim().is_empty() {
                    search_error::exit_gpu_sidecar_error_if_structured(
                        params.json,
                        params.ndjson,
                        &result.stderr,
                        Some(result.exit_code),
                    );
                }
                if !result.stdout.is_empty() {
                    print!("{}", result.stdout);
                }
                if !result.stderr.is_empty() {
                    eprint!("{}", result.stderr);
                }
                std::process::exit(result.exit_code.max(1));
            }
            if result.stdout.trim().is_empty() {
                let msg = if result.stderr.trim().is_empty() {
                    "GPU sidecar returned empty output"
                } else {
                    &result.stderr
                };
                search_error::exit_gpu_sidecar_error_if_structured(
                    params.json,
                    params.ndjson,
                    msg,
                    None,
                );
            }
            if !result.stdout.is_empty() {
                if params.ndjson {
                    // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): `entry`
                    // was JSON-deserialized from the Python sidecar's own stdout, which is
                    // itself a UTF-8-only wire format -- a non-UTF-8 byte could never have
                    // survived the sidecar's own JSON encoding to reach this point.
                    let matches = parse_gpu_sidecar_search_payload(&result.stdout)?
                        .matches
                        .into_iter()
                        .map(|entry| {
                            let (text, bytes, raw) = guaranteed_utf8_match_fields(entry.text);
                            SearchMatchJson {
                                file: entry.file,
                                line: entry.line_number,
                                text,
                                bytes,
                                raw,
                                range: None,
                                meta_variables: None,
                                pattern_id: entry.pattern_id,
                                pattern_text: entry.pattern_text,
                            }
                        })
                        .collect();
                    emit_ndjson_search_results(
                        RoutingDecision::gpu_sidecar(),
                        params.query,
                        params.path,
                        params.gpu_device_ids,
                        matches,
                        // Task 276: this route observed no walk of its own, so it cannot
                        // report a count. `None` means "cannot report", NEVER "complete".
                        None,
                        params.path_was_implicit,
                        None,
                    )?;
                } else if params.json {
                    let normalized = normalize_gpu_sidecar_json(
                        &result.stdout,
                        params.gpu_device_ids,
                        params.path_was_implicit,
                    )?;
                    println!("{}", serde_json::to_string_pretty(&normalized)?);
                } else {
                    print!("{}", result.stdout);
                }
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            Ok(())
        }
        Err(err) => {
            if let Some(reason) =
                search_error::classify_gpu_sidecar_unavailable(&err.stderr, &err.message)
            {
                let warning = format!("native GPU unavailable: {reason}");
                return handle_gpu_unavailable_cpu_fallback(params, &warning);
            }
            search_error::exit_gpu_sidecar_error_if_structured(
                params.json,
                params.ndjson,
                &format!("{} {}", err.stderr.trim(), err.message.trim()),
                Some(err.exit_code),
            );
            exit_with_sidecar_error(err)
        }
    }
}

// `classify_gpu_sidecar_unavailable` lives in `tensor_grep_rs::search_error`.

// #92: classify's --stdin/--text share the sidecar's pre-existing `payload["content"]` path
// (sidecar.py:_classify_payload already prefers payload content over the positional file
// argument) -- no new IPC protocol, just wiring these two flags into the existing Optional
// payload parameter that execute_sidecar_command has always accepted.
pub(crate) fn handle_classify_command(args: ClassifyArgs) -> anyhow::Result<()> {
    if args.stdin_flag && args.text.is_some() {
        anyhow::bail!("tg classify --stdin cannot be combined with --text");
    }
    if args.stdin_flag && args.file_path.is_some() {
        anyhow::bail!("tg classify --stdin cannot be combined with a file path argument");
    }
    if args.text.is_some() && args.file_path.is_some() {
        anyhow::bail!("tg classify --text cannot be combined with a file path argument");
    }
    if args.json && args.format.as_deref().is_some_and(|fmt| fmt != "json") {
        eprintln!("Error: tg classify --json cannot be combined with --format");
        std::process::exit(2);
    }
    let format = args
        .format
        .unwrap_or_else(|| if args.json { "json" } else { "text" }.to_string());
    let sidecar_args = vec![
        "--format".to_string(),
        format,
        "--max-lines".to_string(),
        args.max_lines.to_string(),
    ];

    if args.stdin_flag {
        // Read to EOF; an empty/closed pipe yields an empty string rather than hanging, and
        // the sidecar's existing empty-content branch degrades cleanly (exit 1 with a message
        // instead of a silent hang or crash) -- see sidecar.py's `if not lines:` branch.
        let mut stdin_bytes = Vec::new();
        io::stdin().read_to_end(&mut stdin_bytes)?;
        let content = String::from_utf8_lossy(&stdin_bytes).into_owned();
        let payload = serde_json::json!({ "content": content });
        return handle_sidecar_command("classify", sidecar_args, Some(payload));
    }

    if let Some(text) = args.text {
        let payload = serde_json::json!({ "content": text });
        return handle_sidecar_command("classify", sidecar_args, Some(payload));
    }

    let file_path = args.file_path.ok_or_else(|| {
        anyhow::anyhow!("classify requires a file path, or --stdin, or --text <literal>")
    })?;
    if !Path::new(&file_path).exists() {
        anyhow::bail!(
            "classify expects a file path; use --text for a literal string or --stdin to read from stdin. Received: {}",
            file_path
        );
    }
    let mut full_args = sidecar_args;
    full_args.push("--".to_string());
    full_args.push(file_path);
    handle_sidecar_command("classify", full_args, None)
}

pub(crate) fn handle_sidecar_command(
    command: &str,
    args: Vec<String>,
    payload: Option<serde_json::Value>,
) -> anyhow::Result<()> {
    match execute_sidecar_command(command, args, payload) {
        Ok(result) => {
            let _ = result.sidecar_pid;
            if !result.stdout.is_empty() {
                print!("{}", result.stdout);
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            if result.exit_code != 0 {
                std::process::exit(result.exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

pub(crate) fn handle_python_passthrough(command: &str, args: Vec<String>) -> anyhow::Result<()> {
    match execute_python_passthrough_command(command, args) {
        Ok(exit_code) => {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

pub(crate) fn handle_python_passthrough_with_stdin(
    command: &str,
    args: Vec<String>,
    stdin_bytes: Vec<u8>,
) -> anyhow::Result<()> {
    match execute_python_passthrough_command_with_stdin(command, args, stdin_bytes) {
        Ok(exit_code) => {
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        Err(err) => exit_with_sidecar_error(err),
    }
}

pub(crate) fn exit_with_sidecar_error(err: SidecarError) -> anyhow::Result<()> {
    if !err.stderr.is_empty() {
        eprint!("{}", err.stderr);
    }
    eprintln!("{}", err.message);
    std::process::exit(err.exit_code.max(1));
}
