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

pub(crate) fn run_search_path_with_origin(args: &RunArgs) -> (&str, bool) {
    let explicit = if args.pattern_option.is_some() {
        args.positional.first()
    } else {
        args.positional.get(1)
    };
    (
        explicit.map(String::as_str).unwrap_or("."),
        explicit.is_none(),
    )
}

pub(crate) fn run_batch_path(args: &RunArgs) -> anyhow::Result<&str> {
    if args.positional.len() > 1 {
        anyhow::bail!("tg run --batch-rewrite accepts exactly one PATH argument")
    }

    Ok(args.positional.first().map(String::as_str).unwrap_or("."))
}

pub(crate) fn run_pattern(args: &RunArgs) -> anyhow::Result<&str> {
    if let Some(pattern) = args.pattern_option.as_deref() {
        if args.positional.len() > 1 {
            anyhow::bail!("tg run --pattern accepts at most one positional PATH argument");
        }
        return Ok(pattern);
    }
    match args.positional.first().map(String::as_str) {
        Some(pattern) => Ok(pattern),
        None => anyhow::bail!(
            "tg run requires --pattern <PATTERN> or positional PATTERN unless --batch-rewrite <config.json> is provided"
        ),
    }
}

pub(crate) fn build_search_line_starts(source: &str) -> Vec<usize> {
    let mut line_starts = vec![0];
    for (index, byte) in source.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            line_starts.push(index + 1);
        }
    }
    line_starts
}

pub(crate) fn zero_based_position_for_byte(
    line_starts: &[usize],
    byte_offset: usize,
) -> SearchPositionJson {
    let line_index = line_starts
        .partition_point(|start| *start <= byte_offset)
        .saturating_sub(1);
    SearchPositionJson {
        line: line_index,
        column: byte_offset - line_starts[line_index],
    }
}

pub(crate) fn search_range_json(
    line_starts: &[usize],
    byte_range: &std::ops::Range<usize>,
) -> SearchRangeJson {
    SearchRangeJson {
        byte_offset: SearchByteOffsetJson {
            start: byte_range.start,
            end: byte_range.end,
        },
        start: zero_based_position_for_byte(line_starts, byte_range.start),
        end: zero_based_position_for_byte(line_starts, byte_range.end),
    }
}

pub(crate) fn search_meta_variables_json(
    meta_variables: &AstMetaVariables,
    line_starts: &[usize],
) -> Option<SearchMetaVariablesJson> {
    if meta_variables.single.is_empty() && meta_variables.multi.is_empty() {
        return None;
    }

    let single = meta_variables
        .single
        .iter()
        .map(|(name, capture)| {
            (
                name.clone(),
                SearchMetaVariableJson {
                    text: capture.text.clone(),
                    range: search_range_json(line_starts, &capture.byte_range),
                },
            )
        })
        .collect();
    let multi = meta_variables
        .multi
        .iter()
        .map(|(name, captures)| {
            (
                name.clone(),
                captures
                    .iter()
                    .map(|capture| SearchMetaVariableJson {
                        text: capture.text.clone(),
                        range: search_range_json(line_starts, &capture.byte_range),
                    })
                    .collect(),
            )
        })
        .collect();

    Some(SearchMetaVariablesJson { single, multi })
}

pub(crate) fn ast_match_to_search_json(
    matched: &AstMatch,
    source_contexts: &mut BTreeMap<PathBuf, AstSourceContext>,
) -> anyhow::Result<SearchMatchJson> {
    if !source_contexts.contains_key(&matched.file) {
        let source = std::fs::read_to_string(&matched.file).with_context(|| {
            format!("failed to read AST source file {}", matched.file.display())
        })?;
        source_contexts.insert(
            matched.file.clone(),
            AstSourceContext {
                line_starts: build_search_line_starts(&source),
            },
        );
    }

    let context = source_contexts
        .get(&matched.file)
        .expect("AST source context should be present");
    // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): the source file was just
    // read via `std::fs::read_to_string` above, which itself fails closed (returns `Err`, never
    // reaches this point) on invalid UTF-8 -- an AST match can never carry non-UTF-8 bytes.
    let (text, bytes, raw) = guaranteed_utf8_match_fields(matched.matched_text.clone());
    Ok(SearchMatchJson {
        file: matched.file.to_string_lossy().into_owned(),
        line: matched.line,
        text,
        bytes,
        raw,
        range: Some(search_range_json(
            &context.line_starts,
            &matched.candidate.byte_range,
        )),
        meta_variables: search_meta_variables_json(&matched.meta_variables, &context.line_starts),
        pattern_id: None,
        pattern_text: None,
    })
}

pub(crate) fn validate_run_args(args: &RunArgs) -> anyhow::Result<()> {
    if args.batch_rewrite.is_some() && args.pattern_option.is_some() {
        anyhow::bail!("tg run --batch-rewrite uses the positional argument as PATH and does not accept --pattern");
    }
    if args.stdin_flag && run_has_path_arg(args) {
        anyhow::bail!("tg run --stdin cannot be combined with a PATH argument");
    }
    if args.stdin_flag && args.files_with_matches {
        anyhow::bail!("tg run --stdin cannot be combined with --files-with-matches");
    }
    if args.files_with_matches && args.json {
        anyhow::bail!("tg run --files-with-matches is a read-only text output mode");
    }
    if ast_run_requires_python_passthrough(args)
        && args.pattern_option.is_none()
        && args.positional.len() == 1
        && Path::new(&args.positional[0]).exists()
    {
        anyhow::bail!(
            "tg run ast-grep semantic options require --pattern <PATTERN> before PATH; positional arguments without --pattern are treated as PATTERN"
        );
    }
    if ast_run_requires_python_passthrough(args) && run_has_mutating_options(args) {
        anyhow::bail!(
            "ast-grep semantic run options are read-only in tg run; use ast-grep directly for semantic rewrites"
        );
    }
    if args.files_with_matches
        && (args.rewrite.is_some()
            || args.batch_rewrite.is_some()
            || args.apply
            || args.diff
            || args.verify
            || args.checkpoint
            || args.audit_manifest.is_some()
            || args.audit_signing_key.is_some()
            || !args.apply_edit_ids.is_empty()
            || !args.reject_edit_ids.is_empty()
            || args.lint_cmd.is_some()
            || args.test_cmd.is_some())
    {
        anyhow::bail!("tg run --files-with-matches is a read-only search output mode");
    }
    if (args.lint_cmd.is_some() || args.test_cmd.is_some()) && !args.apply {
        anyhow::bail!("--lint-cmd and --test-cmd require --apply");
    }
    if args.checkpoint && !args.apply {
        anyhow::bail!("--checkpoint requires --apply");
    }
    Ok(())
}

pub(crate) fn ast_run_requires_python_passthrough(args: &RunArgs) -> bool {
    args.selector.is_some()
        || args.strictness.is_some()
        || args.stdin_flag
        || !args.globs.is_empty()
}

pub(crate) fn run_has_path_arg(args: &RunArgs) -> bool {
    if args.pattern_option.is_some() {
        return !args.positional.is_empty();
    }
    args.positional.len() > 1
}

pub(crate) fn run_has_mutating_options(args: &RunArgs) -> bool {
    args.rewrite.is_some()
        || args.batch_rewrite.is_some()
        || args.apply
        || args.update_all
        || args.diff
        || args.verify
        || args.checkpoint
        || args.audit_manifest.is_some()
        || args.audit_signing_key.is_some()
        || !args.apply_edit_ids.is_empty()
        || !args.reject_edit_ids.is_empty()
        || args.lint_cmd.is_some()
        || args.test_cmd.is_some()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RewriteApplyMode {
    PlanThenApply,
    OneShotFastPath,
}

pub(crate) fn can_use_one_shot_apply_fast_path(args: &RunArgs) -> bool {
    args.rewrite.is_some()
        && args.batch_rewrite.is_none()
        && args.apply
        && !args.diff
        && !args.json
        && !args.verify
        && args.lint_cmd.is_none()
        && args.test_cmd.is_none()
        && !args.checkpoint
        && args.audit_manifest.is_none()
        && args.audit_signing_key.is_none()
        && args.apply_edit_ids.is_empty()
        && args.reject_edit_ids.is_empty()
}

pub(crate) fn select_rewrite_apply_mode(args: &RunArgs) -> RewriteApplyMode {
    if can_use_one_shot_apply_fast_path(args) {
        RewriteApplyMode::OneShotFastPath
    } else {
        RewriteApplyMode::PlanThenApply
    }
}

#[cfg(windows)]
pub(crate) fn checkpoint_timestamp_string() -> String {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().to_string())
        .unwrap_or_else(|_| "0".to_string())
}

/// Format a UNIX epoch (seconds) as an ISO-8601 / RFC-3339 UTC instant, e.g.
/// `2026-06-10T12:34:56Z`. The audit manifest `created_at` MUST use this form so the
/// Python verifier (`_parse_timestamp` -> `datetime.fromisoformat`) and `audit-history`
/// time-ordering accept it; a bare epoch string parses to `None` and breaks chronological
/// sorting (audit M5). Uses Howard Hinnant's `civil_from_days` algorithm so we depend only
/// on `std` (no `chrono`/`time` crate, which would force a dependency bump + rebuild).
pub(crate) fn epoch_seconds_to_iso8601_utc(epoch_secs: u64) -> String {
    let days = (epoch_secs / 86_400) as i64;
    let secs_of_day = epoch_secs % 86_400;
    let hour = secs_of_day / 3_600;
    let minute = (secs_of_day % 3_600) / 60;
    let second = secs_of_day % 60;

    // civil_from_days: convert days since 1970-01-01 to (year, month, day).
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let day = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let month = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let year = if month <= 2 { year + 1 } else { year };

    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

/// `created_at` for the rewrite audit manifest, as an ISO-8601 UTC string (audit C1/M5).
pub(crate) fn audit_manifest_timestamp_string() -> String {
    let epoch_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    epoch_seconds_to_iso8601_utc(epoch_secs)
}

#[cfg(windows)]
pub(crate) fn checkpoint_storage_dir(root: &Path) -> PathBuf {
    root.join(".tensor-grep").join("checkpoints")
}

#[cfg(windows)]
#[allow(dead_code)]
pub(crate) fn checkpoint_snapshot_dir(root: &Path, checkpoint_id: &str) -> PathBuf {
    checkpoint_storage_dir(root)
        .join(checkpoint_id)
        .join("snapshot")
}

#[cfg(windows)]
pub(crate) fn checkpoint_index_path(root: &Path) -> PathBuf {
    checkpoint_storage_dir(root).join("index.json")
}

#[cfg(windows)]
#[derive(Debug, Clone)]
pub(crate) struct CheckpointScope {
    pub(crate) root: PathBuf,
    pub(crate) mode: String,
    pub(crate) original_path: PathBuf,
    pub(crate) target_relative: Option<String>,
}

#[cfg(windows)]
impl CheckpointScope {
    pub(crate) fn scope_kind(&self) -> &'static str {
        if self.target_relative.is_some() {
            "file"
        } else {
            "tree"
        }
    }
}

#[cfg(windows)]
pub(crate) fn checkpoint_rel_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(windows)]
pub(crate) fn checkpoint_display_path(path: &Path) -> String {
    let text = path.to_string_lossy();
    text.strip_prefix(r"\\?\").unwrap_or(&text).to_string()
}

#[cfg(windows)]
pub(crate) fn checkpoint_absolute_path(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            env::current_dir()
                .unwrap_or_else(|_| PathBuf::from("."))
                .join(path)
        }
    })
}

#[cfg(windows)]
pub(crate) fn detect_checkpoint_scope(path: &str) -> CheckpointScope {
    let candidate = Path::new(path);
    let resolved = checkpoint_absolute_path(candidate);
    if resolved.is_file() || (!resolved.exists() && resolved.extension().is_some()) {
        let root = resolved
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
        let target_relative = resolved
            .file_name()
            .map(PathBuf::from)
            .map(|relative| checkpoint_rel_path(&relative));
        return CheckpointScope {
            root,
            mode: "filesystem-snapshot".to_string(),
            original_path: resolved,
            target_relative,
        };
    }

    let probe_root = if resolved.is_dir() {
        resolved.clone()
    } else {
        resolved
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."))
    };

    match Command::new("git")
        .args([
            "-C",
            &probe_root.to_string_lossy(),
            "rev-parse",
            "--show-toplevel",
        ])
        .output()
    {
        Ok(output) if output.status.success() => {
            let git_root = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if git_root.is_empty() {
                CheckpointScope {
                    root: probe_root,
                    mode: "filesystem-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            } else if Path::new(&git_root) == resolved.as_path() {
                CheckpointScope {
                    root: PathBuf::from(git_root),
                    mode: "git-worktree-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            } else {
                CheckpointScope {
                    root: probe_root,
                    mode: "filesystem-snapshot".to_string(),
                    original_path: resolved,
                    target_relative: None,
                }
            }
        }
        _ => CheckpointScope {
            root: probe_root,
            mode: "filesystem-snapshot".to_string(),
            original_path: resolved,
            target_relative: None,
        },
    }
}

#[cfg(windows)]
pub(crate) fn collect_git_checkpoint_entries(
    root: &Path,
) -> anyhow::Result<BTreeMap<String, bool>> {
    let tracked = Command::new("git")
        .args(["-C", &root.to_string_lossy(), "ls-files", "-z"])
        .output()
        .context("failed to enumerate git tracked files for checkpoint")?;
    anyhow::ensure!(
        tracked.status.success(),
        "git ls-files failed while building checkpoint"
    );
    let untracked = Command::new("git")
        .args([
            "-C",
            &root.to_string_lossy(),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ])
        .output()
        .context("failed to enumerate git untracked files for checkpoint")?;
    anyhow::ensure!(
        untracked.status.success(),
        "git ls-files --others failed while building checkpoint"
    );

    let mut entries = BTreeMap::new();
    for raw in tracked
        .stdout
        .split(|byte| *byte == 0)
        .chain(untracked.stdout.split(|byte| *byte == 0))
    {
        if raw.is_empty() {
            continue;
        }
        let rel = String::from_utf8_lossy(raw).to_string();
        if rel.split('/').any(|component| component == ".tensor-grep") {
            continue;
        }
        entries.insert(rel.clone(), root.join(&rel).exists());
    }
    Ok(entries)
}

#[cfg(windows)]
pub(crate) fn should_skip_checkpoint_dir(name: &str) -> bool {
    matches!(
        name,
        ".git"
            | ".hg"
            | ".svn"
            | ".venv"
            | "node_modules"
            | "__pycache__"
            | ".pytest_cache"
            | ".mypy_cache"
            | ".ruff_cache"
            | ".tensor-grep"
    )
}

#[cfg(windows)]
pub(crate) fn collect_filesystem_checkpoint_entries(
    root: &Path,
) -> anyhow::Result<BTreeMap<String, bool>> {
    let mut entries = BTreeMap::new();
    for result in walkdir::WalkDir::new(root) {
        let entry = result.context("failed to walk checkpoint filesystem tree")?;
        if entry.file_type().is_dir() {
            continue;
        }
        let relative = entry
            .path()
            .strip_prefix(root)
            .context("checkpoint path escaped snapshot root")?;
        if relative
            .components()
            .any(|component| should_skip_checkpoint_dir(&component.as_os_str().to_string_lossy()))
        {
            continue;
        }
        entries.insert(relative.to_string_lossy().replace('\\', "/"), true);
    }
    Ok(entries)
}

#[cfg(windows)]
pub(crate) fn collect_checkpoint_entries(
    scope: &CheckpointScope,
) -> anyhow::Result<BTreeMap<String, bool>> {
    if let Some(relative) = &scope.target_relative {
        let mut entries = BTreeMap::new();
        entries.insert(relative.clone(), scope.root.join(relative).exists());
        Ok(entries)
    } else if scope.mode == "git-worktree-snapshot" {
        collect_git_checkpoint_entries(&scope.root)
    } else {
        collect_filesystem_checkpoint_entries(&scope.root)
    }
}

/// Copies one checkpoint source entry into the snapshot, mirroring
/// `checkpoint_store.py::create_checkpoint`'s `shutil.copy2(source, destination,
/// follow_symlinks=False)` (checkpoint_store.py:853-855, audit HIGH -- symlink disclosure).
/// `std::fs::copy` FOLLOWS symlinks, so a plain copy here would read a source symlink's
/// (possibly out-of-root) TARGET content and bake it into the snapshot -- an out-of-root
/// disclosure (audit #178). Recreate the symlink AS a symlink instead of following it; fall
/// back to a normal file copy for every non-symlink entry.
///
/// Round-trip contract (verified against `checkpoint_store.py::undo_checkpoint`, the only
/// restore path -- `tg checkpoint` list/undo is a full Python passthrough, see
/// `Commands::Checkpoint` below). Storing the link AS a link (not its target's bytes) is what
/// keeps an out-of-root symlink FAIL-CLOSED rather than a disclosure on undo -- but it does NOT
/// make every symlink restorable, and this comment must not claim a full round-trip. undo's
/// read-only pre-flight `_resolve_within_root` (checkpoint_store.py:124-139, called at
/// :1232-1235) RESOLVES each stored snapshot symlink and, before touching any working-tree
/// file, REFUSES it (ValueError) when the resolved target escapes the snapshot root; the later
/// missing-source probe (:1246-1257) raises CheckpointCorruptError for a dangling target. So an
/// out-of-root OR dangling symlink checkpoint is refused fail-closed on undo -- no disclosure,
/// no data loss, working tree left intact, but NOT restorable. Only an in-root symlink whose
/// resolved target exists inside the snapshot is actually restored. That fail-closed refusal is
/// the correct, safe behavior; the alternative (following the link here) would materialize
/// out-of-root content into the snapshot and then into the tree on undo.
pub(crate) fn create_checkpoint(path: &str) -> anyhow::Result<CheckpointCreateSummary> {
    native_checkpoint_publication::create_checkpoint(path)
}

pub(crate) fn load_batch_rewrite_config(config_path: &Path) -> anyhow::Result<BatchRewriteConfig> {
    let contents = std::fs::read_to_string(config_path).with_context(|| {
        format!(
            "failed to read batch rewrite config {}",
            config_path.display()
        )
    })?;
    let value: serde_json::Value = serde_json::from_str(&contents).with_context(|| {
        format!(
            "failed to parse batch rewrite config {}",
            config_path.display()
        )
    })?;
    parse_batch_rewrite_config_value(&value)
}

pub(crate) fn parse_batch_rewrite_config_value(
    value: &serde_json::Value,
) -> anyhow::Result<BatchRewriteConfig> {
    let object = value.as_object().ok_or_else(|| {
        // audit M4: name the required shape instead of the cryptic `$` JSON-pointer root.
        anyhow::anyhow!(
            "--batch-rewrite config must be a JSON object like \
             {{\"rewrites\": [{{\"pattern\": ..., \"replacement\": ..., \"lang\": ...}}], \"verify\": false}}"
        )
    })?;

    for key in object.keys() {
        if key != "rewrites" && key != "verify" {
            anyhow::bail!("invalid batch rewrite config field `{key}`: unknown field");
        }
    }

    let rewrites_value = object.get("rewrites").ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `rewrites`: missing required field")
    })?;
    let rewrites_array = rewrites_value.as_array().ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `rewrites`: expected array")
    })?;
    if rewrites_array.is_empty() {
        anyhow::bail!(
            "invalid batch rewrite config field `rewrites`: expected at least one rewrite rule"
        );
    }

    let verify = match object.get("verify") {
        Some(serde_json::Value::Bool(value)) => *value,
        Some(_) => anyhow::bail!("invalid batch rewrite config field `verify`: expected boolean"),
        None => false,
    };

    let mut rewrites = Vec::with_capacity(rewrites_array.len());
    for (index, rule_value) in rewrites_array.iter().enumerate() {
        let field_prefix = format!("rewrites[{index}]");
        let rule_object = rule_value.as_object().ok_or_else(|| {
            anyhow::anyhow!("invalid batch rewrite config field `{field_prefix}`: expected object")
        })?;

        for key in rule_object.keys() {
            if key != "pattern" && key != "replacement" && key != "lang" {
                anyhow::bail!(
                    "invalid batch rewrite config field `{field_prefix}.{key}`: unknown field"
                );
            }
        }

        let pattern = read_batch_rewrite_string_field(rule_object, &field_prefix, "pattern")?;
        let replacement =
            read_batch_rewrite_string_field(rule_object, &field_prefix, "replacement")?;
        let lang = read_batch_rewrite_string_field(rule_object, &field_prefix, "lang")?;

        rewrites.push(BatchRewriteRule {
            pattern,
            replacement,
            lang,
        });
    }

    Ok(BatchRewriteConfig { rewrites, verify })
}

pub(crate) fn read_batch_rewrite_string_field(
    object: &serde_json::Map<String, serde_json::Value>,
    field_prefix: &str,
    field_name: &str,
) -> anyhow::Result<String> {
    let field_path = format!("{field_prefix}.{field_name}");
    let value = object.get(field_name).ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `{field_path}`: missing required field")
    })?;
    let string_value = value.as_str().ok_or_else(|| {
        anyhow::anyhow!("invalid batch rewrite config field `{field_path}`: expected string")
    })?;
    if string_value.is_empty() {
        anyhow::bail!(
            "invalid batch rewrite config field `{field_path}`: expected non-empty string"
        );
    }
    Ok(string_value.to_string())
}

pub(crate) fn validate_edit_id_selector(ids: &[String], flag_name: &str) -> anyhow::Result<()> {
    let mut seen = std::collections::BTreeSet::new();
    for id in ids {
        if id.is_empty() {
            anyhow::bail!("{flag_name} requires non-empty edit ids");
        }
        if !seen.insert(id.clone()) {
            anyhow::bail!("duplicate edit id `{id}` provided via {flag_name}");
        }
    }
    Ok(())
}

pub(crate) fn filter_rewrite_edits(
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
    args: &RunArgs,
) -> anyhow::Result<Vec<tensor_grep_rs::backend_ast::RewriteEdit>> {
    validate_edit_id_selector(&args.apply_edit_ids, "--apply-edit-ids")?;
    validate_edit_id_selector(&args.reject_edit_ids, "--reject-edit-ids")?;

    let known_ids: std::collections::BTreeSet<&str> =
        edits.iter().map(|edit| edit.id.as_str()).collect();
    for id in &args.apply_edit_ids {
        if !known_ids.contains(id.as_str()) {
            anyhow::bail!("unknown edit id `{id}` provided via --apply-edit-ids");
        }
    }
    for id in &args.reject_edit_ids {
        if !known_ids.contains(id.as_str()) {
            anyhow::bail!("unknown edit id `{id}` provided via --reject-edit-ids");
        }
    }

    if !args.apply_edit_ids.is_empty() {
        let allowed: std::collections::BTreeSet<&str> =
            args.apply_edit_ids.iter().map(String::as_str).collect();
        return Ok(edits
            .iter()
            .filter(|edit| allowed.contains(edit.id.as_str()))
            .cloned()
            .collect());
    }

    if !args.reject_edit_ids.is_empty() {
        let rejected: std::collections::BTreeSet<&str> =
            args.reject_edit_ids.iter().map(String::as_str).collect();
        return Ok(edits
            .iter()
            .filter(|edit| !rejected.contains(edit.id.as_str()))
            .cloned()
            .collect());
    }

    Ok(edits.to_vec())
}

pub(crate) fn filter_rewrite_plan(
    plan: &tensor_grep_rs::backend_ast::RewritePlan,
    args: &RunArgs,
) -> anyhow::Result<tensor_grep_rs::backend_ast::RewritePlan> {
    let edits = filter_rewrite_edits(&plan.edits, args)?;
    let mut filtered = plan.clone();
    filtered.total_edits = edits.len();
    filtered.edits = edits;
    Ok(filtered)
}

pub(crate) fn filter_batch_rewrite_plan(
    plan: &BatchRewritePlan,
    args: &RunArgs,
) -> anyhow::Result<BatchRewritePlan> {
    let edits = filter_rewrite_edits(&plan.edits, args)?;
    let mut filtered = plan.clone();
    filtered.total_edits = edits.len();
    filtered.edits = edits;
    Ok(filtered)
}

/// Split a validation command TEMPLATE into argv tokens, honoring "double" and 'single' quotes.
/// A validation command is spawned directly (never through `sh -c` / `cmd /C`), so shell
/// metacharacters in a token are literal data, not operators — there is nothing to escape or reject.
pub(crate) fn split_validation_command_argv(command: &str) -> Vec<String> {
    let mut argv = Vec::new();
    let mut current = String::new();
    let mut started = false; // distinguishes an empty quoted token "" from "no token here"
    let mut in_quotes = false;
    let mut quote_char = '\0';

    for character in command.chars() {
        if matches!(character, '"' | '\'') {
            if in_quotes && character == quote_char {
                in_quotes = false;
                quote_char = '\0';
                continue;
            }
            if !in_quotes {
                in_quotes = true;
                quote_char = character;
                started = true;
                continue;
            }
        }
        if character.is_whitespace() && !in_quotes {
            if started || !current.is_empty() {
                argv.push(std::mem::take(&mut current));
                started = false;
            }
            continue;
        }
        started = true;
        current.push(character);
    }
    if in_quotes {
        // Unterminated quote: refuse to guess token boundaries. Returning an empty argv routes to
        // the clear "empty or unbalanced quotes" error in run_validation_command rather than
        // spawning a mis-split program.
        return Vec::new();
    }
    if started || !current.is_empty() {
        argv.push(current);
    }
    argv
}

/// Build the argv used to EXECUTE a validation command. The TEMPLATE is split into argv first, then
/// the raw file path is substituted into the `$file` / `{file}` placeholder token(s). Because the
/// path lands in a single argv element and the command is spawned directly (no shell), a file whose
/// name contains shell metacharacters cannot inject commands. SECURITY: this replaces the previous
/// model that string-substituted the path into a `sh -c` / `cmd /S /C` command line.
pub(crate) fn validation_command_argv(template: &str, file_path: Option<&str>) -> Vec<String> {
    let mut argv = split_validation_command_argv(template);
    if let Some(path) = file_path {
        for token in &mut argv {
            if token.contains("$file") || token.contains("{file}") {
                *token = token.replace("$file", path).replace("{file}", path);
            }
        }
    }
    argv
}

pub(crate) fn validation_working_dir(path: &str) -> PathBuf {
    let path = Path::new(path);
    if path.is_dir() {
        return path.to_path_buf();
    }
    if path.is_file() {
        return path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
    }
    path.parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Resolve the --lint-cmd/--test-cmd subprocess timeout: --validation-timeout-ms flag takes
/// precedence over the TG_VALIDATION_TIMEOUT_MS environment variable, which takes precedence over
/// DEFAULT_VALIDATION_TIMEOUT_MS (mirrors the TG_RESIDENT_AST env-var-fallback convention).
pub(crate) fn validation_timeout_ms(args: &RunArgs) -> u64 {
    resolve_validation_timeout_ms(
        args.validation_timeout_ms,
        std::env::var(TG_VALIDATION_TIMEOUT_MS_ENV).ok(),
    )
}

/// Env lookup is threaded in as a plain `Option<String>` (rather than read directly with
/// `std::env::var` inside this function) so precedence can be unit-tested deterministically --
/// mutating a real process-wide env var from parallel `cargo test` threads would be racy.
pub(crate) fn resolve_validation_timeout_ms(flag: Option<u64>, env_value: Option<String>) -> u64 {
    if let Some(explicit) = flag {
        return explicit;
    }
    if let Some(raw) = env_value {
        if let Ok(parsed) = raw.trim().parse::<u64>() {
            return parsed;
        }
    }
    DEFAULT_VALIDATION_TIMEOUT_MS
}

pub(crate) fn run_validation_command(
    kind: &'static str,
    template: &str,
    file_path: Option<&str>,
    display_command: &str,
    working_dir: &Path,
    timeout_ms: u64,
) -> ValidationCommandResult {
    // Validate the template can run as a direct program invocation BEFORE substituting the file
    // path: an empty/blank program, unbalanced quotes, or the $file placeholder in program position
    // would otherwise spawn the wrong thing (e.g. an attacker-named file as the program).
    let tokens = split_validation_command_argv(template);
    if tokens.first().is_none_or(|token| token.is_empty()) {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command is empty or has unbalanced quotes".to_string(),
        };
    }
    if file_path.is_some()
        && tokens.len() == 1
        && (tokens[0].contains("$file") || tokens[0].contains("{file}"))
    {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command must name a program before the $file/{file} placeholder"
                .to_string(),
        };
    }

    let argv = validation_command_argv(template, file_path);
    let Some((program, args)) = argv.split_first() else {
        return ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: "validation command is empty".to_string(),
        };
    };

    // A deadlocked/interactive/infinite-looping validation command must never hang `tg run
    // --apply` forever (the #400 hang class, applied to the validation path -- audit #10). We
    // spawn with piped stdout/stderr and bound the wait with `process_control`'s
    // `controlled_with_output`, which drains the pipes WHILE timing out. Do NOT replace this with
    // a hand-rolled `spawn` + `wait_timeout` + `wait_with_output`: if the child fills the OS pipe
    // buffer before exiting, it blocks on write() until someone reads, but `wait_timeout` never
    // reads the pipes -- the parent and child deadlock before the timeout can fire (rust-lang#45572).
    // SENTINEL RETIREMENT (2026-08-01 backlog campaign, mirrors apply_policy.py): no `--`
    // separator here either -- `program`/`args` are an operator-authored complete validation
    // command, not our flags plus an untrusted positional, so the CWE-88 argv-sentinel census
    // does not apply to this construction site. Adversarial gate note (corrected 2026-08-01: the
    // prior comment overclaimed): `validation_template_file_path` (below) does NOT always
    // absolutize the substituted `$file`/`{file}` token -- when `std::env::current_dir()` fails,
    // its fallback joins the candidate against `PathBuf::from(".")`, which stays relative. The
    // invariant that actually holds on BOTH arms is narrower but still sufficient: the result is
    // either genuinely absolute or dot-PREFIXED (`./...`), and neither shape can ever lead with
    // `-`, so the sibling Python bug (a repo file named e.g. `-cevil.ini` parsing as a flag,
    // fixed in `apply_policy.py::_policy_file_arg`) cannot recur here either way. Do NOT
    // "simplify" this by substituting a bare relative path with no dot-prefix guarantee -- the
    // dot-prefix-or-absolute guarantee, not absolutization per se, is load-bearing.
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(working_dir)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());

    let child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return ValidationCommandResult {
                kind,
                command: display_command.to_string(),
                success: false,
                exit_code: None,
                stdout: String::new(),
                stderr: format!(
                    "failed to spawn validation command in {}: {error}",
                    working_dir.display()
                ),
            };
        }
    };

    match child
        .controlled_with_output()
        .time_limit(Duration::from_millis(timeout_ms))
        .terminate_for_timeout()
        .wait()
    {
        Ok(Some(output)) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: output.status.success(),
            exit_code: output.status.code().map(|code| code as i32),
            stdout: String::from_utf8_lossy(&output.stdout).to_string(),
            stderr: String::from_utf8_lossy(&output.stderr).to_string(),
        },
        // `terminate_for_timeout()` makes `wait()` return `Ok(None)` when the time limit expires:
        // the crate has already terminated (and reaped) the child, so there is no zombie left
        // behind. Report this as a FAILED result (never a panic) so the caller's on_failure
        // rollback path runs, exactly like any other failed validation command.
        Ok(None) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: format!("validation command exceeded {timeout_ms}ms timeout"),
        },
        Err(error) => ValidationCommandResult {
            kind,
            command: display_command.to_string(),
            success: false,
            exit_code: None,
            stdout: String::new(),
            stderr: format!(
                "failed to wait for validation command in {}: {error}",
                working_dir.display()
            ),
        },
    }
}

pub(crate) fn validation_template_file_path(path: &str) -> String {
    let candidate = Path::new(path);
    let absolute = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(candidate)
    };
    absolute.to_string_lossy().to_string()
}

pub(crate) fn expand_validation_command_template(command: &str, path: &str) -> String {
    if !validation_command_uses_file_placeholder(command) {
        return command.to_string();
    }
    let file_path = validation_template_file_path(path);
    command
        .replace("$file", &file_path)
        .replace("{file}", &file_path)
}

pub(crate) fn validation_command_uses_file_placeholder(command: &str) -> bool {
    command.contains("$file") || command.contains("{file}")
}

pub(crate) fn validation_template_targets_for_command(
    command: &str,
    path: &str,
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> Vec<String> {
    if !validation_command_uses_file_placeholder(command) {
        return vec![path.to_string()];
    }

    let edited_files: Vec<String> = edits
        .iter()
        .map(|edit| edit.file.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(|file| file.to_string_lossy().to_string())
        .collect();
    if edited_files.is_empty() {
        vec![path.to_string()]
    } else {
        edited_files
    }
}

/// Cap the per-command validation target list to at most `max_targets` entries (audit #34: an
/// 800-edited-file `--batch-rewrite ... --test-cmd 'pytest {file}'` would otherwise fan out 800
/// serial subprocess spawns). `max_targets == 0` disables the cap (mirrors ClassifyArgs.max_lines's
/// "0 disables the cap" convention). Returns the possibly-truncated list, whether truncation
/// occurred, and the real pre-cap target count so the caller can report it (fail-closed VISIBLE:
/// a silently-dropped target must never look like a clean, complete validation pass).
pub(crate) fn cap_validation_targets(
    mut targets: Vec<String>,
    max_targets: usize,
) -> (Vec<String>, bool, usize) {
    let total = targets.len();
    if max_targets == 0 || total <= max_targets {
        return (targets, false, total);
    }
    targets.truncate(max_targets);
    (targets, true, total)
}

pub(crate) fn run_post_apply_validation(
    args: &RunArgs,
    path: &str,
    edits: &[tensor_grep_rs::backend_ast::RewriteEdit],
) -> Option<ValidationSummary> {
    let mut commands = Vec::new();
    let working_dir = validation_working_dir(path);
    let timeout_ms = validation_timeout_ms(args);
    let max_targets = args.max_validation_targets;
    let mut targets_truncated = false;
    let mut targets_total = 0usize;

    if let Some(command) = &args.lint_cmd {
        let (targets, truncated, total) = cap_validation_targets(
            validation_template_targets_for_command(command, path, edits),
            max_targets,
        );
        targets_truncated |= truncated;
        targets_total = targets_total.max(total);
        for target in targets {
            let expanded = expand_validation_command_template(command, &target);
            let file_path = validation_command_uses_file_placeholder(command)
                .then(|| validation_template_file_path(&target));
            commands.push(run_validation_command(
                "lint",
                command,
                file_path.as_deref(),
                &expanded,
                &working_dir,
                timeout_ms,
            ));
        }
    }
    if let Some(command) = &args.test_cmd {
        let (targets, truncated, total) = cap_validation_targets(
            validation_template_targets_for_command(command, path, edits),
            max_targets,
        );
        targets_truncated |= truncated;
        targets_total = targets_total.max(total);
        for target in targets {
            let expanded = expand_validation_command_template(command, &target);
            let file_path = validation_command_uses_file_placeholder(command)
                .then(|| validation_template_file_path(&target));
            commands.push(run_validation_command(
                "test",
                command,
                file_path.as_deref(),
                &expanded,
                &working_dir,
                timeout_ms,
            ));
        }
    }

    if commands.is_empty() {
        return None;
    }

    Some(ValidationSummary {
        success: commands.iter().all(|command| command.success),
        commands,
        validation_targets_truncated: targets_truncated,
        validation_targets_total: targets_total,
    })
}
