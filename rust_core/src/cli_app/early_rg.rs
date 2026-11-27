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

/// Fail closed (exit 2, the "backend unavailable / incomplete" convention `handle_calibrate_command`
/// already uses) instead of letting a passthrough-required rg invocation bubble an `Err` through `?`
/// to `main()`'s default `Result` termination, which exits 1 -- indistinguishable from a genuine
/// "no match" (audit #81 #7). Also used to refuse a `--pcre2` request when no rg is present rather
/// than silently swapping to the native regex engine, which does not support PCRE2 syntax (#9).
pub(crate) fn require_ripgrep_or_exit(rg_available: bool, context: &str) {
    if !rg_available {
        eprintln!(
            "error: {context} requires the ripgrep (`rg`) backend, but rg is unavailable. \
             Install `rg`, set TG_RG_PATH, or place a bundled ripgrep binary next to `tg`."
        );
        std::process::exit(2);
    }
}

pub(crate) fn is_top_level_pcre2_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 2 && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("--pcre2-version")
}

pub(crate) fn is_top_level_type_list_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 2 && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("--type-list")
}

pub(crate) fn is_search_pcre2_version_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && raw_args.get(2).and_then(|arg| arg.to_str()) == Some("--pcre2-version")
}

pub(crate) fn is_search_type_list_invocation(raw_args: &[OsString]) -> bool {
    raw_args.len() == 3
        && raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search")
        && raw_args.get(2).and_then(|arg| arg.to_str()) == Some("--type-list")
}
pub(crate) fn parse_public_help_passthrough(raw_args: &[OsString]) -> Option<(&str, Vec<String>)> {
    if raw_args.len() == 1 {
        return Some(("--help", Vec::new()));
    }

    let first = raw_args.get(1)?.to_str()?;
    match (
        first,
        raw_args.get(2).and_then(|arg| arg.to_str()),
        raw_args.len(),
    ) {
        ("--help" | "-h", None, 2) => Some((first, Vec::new())),
        ("search", Some("--help" | "-h"), 3) => {
            Some(("search", vec![raw_args[2].to_string_lossy().into_owned()]))
        }
        ("scan" | "test", Some("--help" | "-h"), 3) => {
            Some((first, vec![raw_args[2].to_string_lossy().into_owned()]))
        }
        _ => None,
    }
}

pub(crate) fn try_public_help_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    let (command, args) = match parse_public_help_passthrough(raw_args) {
        Some(invocation) => invocation,
        None => return Ok(None),
    };

    match execute_python_passthrough_command_captured(command, args) {
        Ok(result) if result.exit_code == 0 => {
            if !result.stdout.is_empty() {
                print!("{}", result.stdout);
            }
            if !result.stderr.is_empty() {
                eprint!("{}", result.stderr);
            }
            Ok(Some(0))
        }
        Ok(_) => Ok(None),
        Err(_) => Ok(None),
    }
}

pub(crate) fn try_early_ripgrep_passthrough(raw_args: &[OsString]) -> anyhow::Result<Option<i32>> {
    if !env_flag_enabled(TG_RUST_EARLY_RG_ENV) {
        return Ok(None);
    }
    if !ripgrep_is_available() {
        return Ok(None);
    }

    if raw_args
        .get(1)
        .map(|arg| arg.to_string_lossy() != "search")
        .unwrap_or(true)
    {
        return Ok(None);
    }

    let (rg_args, explicit_rg_format) = match parse_early_ripgrep_args_with_metadata(raw_args) {
        Some(parsed) => parsed,
        None => return Ok(None),
    };
    if !should_use_early_ripgrep_fast_path(&rg_args) {
        return Ok(None);
    }
    refuse_structured_pcre2(rg_args.pcre2, rg_args.json, false, explicit_rg_format);
    let generated_dirs = if ripgrep_args_need_broad_generated_guard(&rg_args) {
        generated_scan_dir_names(&rg_args.paths, rg_args.files)
    } else if rg_args.max_depth.is_none()
        && rg_args.globs.is_empty()
        && rg_args.file_types.is_empty()
    {
        generated_scan_dir_names(&rg_args.paths, false)
    } else {
        Vec::new()
    };
    if !generated_dirs.is_empty() {
        let msg = format_broad_generated_scan_error(&generated_dirs);
        exit_structured_search_error_if_needed(
            rg_args.json,
            false,
            "broad_generated_scan_refused",
            &msg,
        );
    }

    let exit_code = match execute_ripgrep_search(&rg_args) {
        Ok(exit_code) => exit_code,
        Err(err) => {
            handle_search_runtime_error(rg_args.json, false, explicit_rg_format, err)?;
            return Ok(None);
        }
    };
    Ok(Some(exit_code))
}

pub(crate) fn try_default_search_frontdoor_passthrough(
    raw_args: &[OsString],
) -> anyhow::Result<Option<i32>> {
    if !ripgrep_is_available() {
        return Ok(None);
    }
    let (rg_args, explicit_rg_format) =
        match parse_default_search_frontdoor_args_with_metadata(raw_args) {
            Some(parsed) => parsed,
            None => return Ok(None),
        };
    refuse_structured_pcre2(rg_args.pcre2, rg_args.json, false, explicit_rg_format);

    let exit_code = match execute_ripgrep_search(&rg_args) {
        Ok(exit_code) => exit_code,
        Err(err) => {
            handle_search_runtime_error(rg_args.json, false, explicit_rg_format, err)?;
            return Ok(None);
        }
    };
    Ok(Some(exit_code))
}

pub(crate) fn try_early_positional_ripgrep_passthrough(
    raw_args: &[OsString],
) -> anyhow::Result<Option<i32>> {
    if !env_flag_enabled(TG_RUST_EARLY_POSITIONAL_RG_ENV) {
        return Ok(None);
    }
    if !ripgrep_is_available() {
        return Ok(None);
    }

    if !should_use_positional_cli(raw_args) {
        return Ok(None);
    }

    let rg_args = match parse_early_positional_ripgrep_args(raw_args) {
        Some(args) => args,
        None => return Ok(None),
    };
    let positional = PositionalCli::try_parse_from(raw_args).ok();
    let json = positional.as_ref().is_some_and(|cli| cli.json);
    let ndjson = positional.as_ref().is_some_and(|cli| cli.ndjson);
    refuse_structured_pcre2(rg_args.pcre2, json, ndjson, false);

    let exit_code = match execute_ripgrep_search(&rg_args) {
        Ok(exit_code) => exit_code,
        Err(err) => {
            handle_search_runtime_error(json, ndjson, false, err)?;
            return Ok(None);
        }
    };
    Ok(Some(exit_code))
}

pub(crate) fn search_format_python_passthrough_args(raw_args: &[OsString]) -> Option<Vec<String>> {
    let search_args = normalize_top_level_search_args(raw_args)?;
    let cmd = CommandCli::command();
    let matches = cmd.clone().try_get_matches_from(&search_args).ok();
    let args = extract_search_tail_with_matches(
        &search_args,
        matches.as_ref(),
        cmd.find_subcommand("search"),
    );
    // External audit #138/#140: `--index` selects the Rust-native trigram-index engine
    // (route_search -> handle_index_search -> index_flag_violations), which the Python sidecar
    // does not implement at all. Every check below exists to route some OTHER flag to Python --
    // none of them know about --index, so a request combining --index with any single one of
    // them (e.g. `tg search --index --no-hidden ...`) used to be forwarded here wholesale,
    // `--index` token and all, BEFORE clap ever parsed --index. Python has no `--index` option,
    // so that either crashes ("Error: No such option: --index") or -- if some future Python
    // passthrough ever tolerates unknown flags -- leaks the bare token into the constructed rg
    // argv. Short-circuit ahead of every passthrough check so an explicit --index always falls
    // through (returns None here) to clap/route_search/index_flag_violations below, which
    // already fail closed on any flag the index path cannot honor -- regardless of which other
    // (individually Python-passthrough-eligible) flags ride along with it. This does not change
    // routing for non-index invocations: the checks below are unreached only when --index is
    // literally present.
    if args.iter().any(|arg| arg == "--index") {
        return None;
    }
    if search_args_contain_any_flag(&args, SEARCH_PYTHON_PASSTHROUGH_FLAGS) {
        return Some(args);
    }
    let has_files = args.iter().any(|arg| arg == "--files");
    if args.iter().any(|arg| arg == "--ast")
        || (!has_files && args.iter().any(|arg| arg == "--allow-broad-generated-scan"))
    {
        return Some(args);
    }
    let structured_output = args
        .iter()
        .any(|arg| matches!(arg.as_str(), "--json" | "--ndjson"));
    if structured_output
        && args.iter().any(|arg| {
            matches!(
                arg.as_str(),
                "--passthru"
                    | "--passthrough"
                    | "--auto-hybrid-regex"
                    | "--no-ignore-dot"
                    | "--no-ignore-exclude"
                    | "--no-ignore-files"
                    | "--no-ignore-global"
                    | "--no-ignore-parent"
                    | "--no-config"
            )
        })
    {
        return Some(args);
    }
    if structured_output
        && args.iter().any(|arg| {
            matches!(
                arg.as_str(),
                "-U" | "--multiline" | "--multiline-dotall" | "--null-data"
            )
        })
    {
        return Some(args);
    }
    let mut index = 0usize;
    while index < args.len() {
        let token = &args[index];
        if token == "--format" {
            index += 1;
            if args.get(index).map(String::as_str) != Some("rg") {
                return Some(args);
            }
        } else if let Some((_, value)) = token.split_once('=') {
            if token.starts_with("--format=") && value != "rg" {
                return Some(args);
            }
        }
        index += 1;
    }
    None
}

/// Render-only flags the aggregate plain-`--json` path cannot honor. Mirrors
/// `_PLAIN_JSON_INCOMPATIBLE_RENDER_FLAGS` / `_JSON_INCOMPATIBLE_RENDER_FLAGS` in the
/// Python CLI/launcher (canonical spelling first in each group).
pub(crate) const JSON_INCOMPATIBLE_RENDER_FLAGS: &[&[&str]] = &[
    &["--passthru", "--passthrough"],
    &["--heading", "--no-heading"],
    &["--trim", "--no-trim"],
    &["-b", "--byte-offset", "--no-byte-offset"],
    &["-M", "--max-columns"],
    &["--max-columns-preview", "--no-max-columns-preview"],
    &["--context-separator", "--no-context-separator"],
    &["--field-context-separator"],
    &["--field-match-separator"],
    &["-p", "--pretty"],
];

/// Return the canonical spellings of render-only flags the user combined with plain
/// `--json` (not `--format rg`). Such a combination must be rejected by the NATIVE binary
/// directly — never delegated to the Python sidecar — because delegating to a stale/older
/// tensor-grep Python (one lacking the launcher guard) deadlocks and can fork-bomb the
/// native<->python re-exec chain (audit C3). Mirrors the Python guard so the native front
/// door is self-sufficient regardless of which Python it resolves.
pub(crate) fn json_aggregate_render_flag_conflicts(raw_args: &[OsString]) -> Vec<String> {
    let Some(search_args) = normalize_top_level_search_args(raw_args) else {
        return Vec::new();
    };
    let args = extract_search_tail_with_matches(&search_args, None, None);
    if !args.iter().any(|arg| arg == "--json") {
        return Vec::new();
    }
    // `--format rg` emits ripgrep JSON Lines, which carry render metadata — allowed.
    // Stop at the `--` end-of-options token (mirroring the conflict loop below): a
    // literal `--format rg` smuggled AFTER `--` is a search pattern, not the flag, and
    // must not suppress a genuine render-flag conflict that precedes `--` (audit MED).
    let mut index = 0usize;
    while index < args.len() {
        if args[index] == "--" {
            break;
        }
        if args[index] == "--format" {
            if args.get(index + 1).map(String::as_str) == Some("rg") {
                return Vec::new();
            }
        } else if args[index] == "--format=rg" {
            return Vec::new();
        }
        index += 1;
    }
    let mut flagged: Vec<String> = Vec::new();
    for arg in &args {
        if arg == "--" {
            break;
        }
        let base = arg.split('=').next().unwrap_or(arg);
        for group in JSON_INCOMPATIBLE_RENDER_FLAGS {
            let canonical = group[0].to_string();
            if group.contains(&base) && !flagged.contains(&canonical) {
                flagged.push(canonical);
            }
        }
    }
    flagged
}

pub(crate) fn normalize_top_level_search_args(raw_args: &[OsString]) -> Option<Vec<OsString>> {
    if raw_args.get(1).and_then(|arg| arg.to_str()) == Some("search") {
        return Some(raw_args.to_vec());
    }
    if !raw_args_contain_any_flag(raw_args, SEARCH_OPTION_FIRST_FLAGS)
        && !raw_args_contain_any_flag(raw_args, SEARCH_PYTHON_PASSTHROUGH_FLAGS)
    {
        return None;
    }
    if raw_args
        .get(1)
        .and_then(|arg| arg.to_str())
        .map(is_known_python_command)
        .unwrap_or(false)
    {
        return None;
    }

    let mut search_args = Vec::with_capacity(raw_args.len() + 1);
    search_args.push(raw_args.first()?.clone());
    search_args.push(OsString::from("search"));
    search_args.extend(raw_args.iter().skip(1).cloned());
    Some(search_args)
}

pub(crate) fn should_use_early_ripgrep_fast_path(args: &RipgrepSearchArgs) -> bool {
    !args.word_regexp && !args.fixed_strings
}

/// Whether stdout is attached to a terminal. Load-bearing for
/// `native_can_serve_plain_text`: `execute_ripgrep_search` spawns `rg` with `Stdio::inherit()`,
/// so on a terminal `rg` renders its grouped/heading layout with color while the native engine
/// always renders flat uncolored `path:line:text`. Terminals therefore keep the subprocess.
pub(crate) fn stdout_is_terminal() -> bool {
    use std::io::IsTerminal;

    std::io::stdout().is_terminal()
}

/// ripgrep's config-file environment surface. `rg` reads this on startup, and
/// `execute_ripgrep_search` neither clears the environment nor passes `--no-config` (that is sent
/// only when the USER asks for it), so a plain-text search routed to `rg` today applies whatever
/// this points at. See `native_can_serve_plain_text` refusal note (8).
pub(crate) const RIPGREP_CONFIG_PATH_ENV: &str = "RIPGREP_CONFIG_PATH";

/// THE single computation of the environment clause. Called from exactly one place
/// (`finish_plain_text_native_request`), so the two eligibility adapters cannot drift on it.
pub(crate) fn rg_config_env_present() -> bool {
    rg_config_env_is_active(env::var_os(RIPGREP_CONFIG_PATH_ENV).as_deref())
}

/// Decision half of `rg_config_env_present`, split out so its semantics are unit-testable without
/// mutating the process environment (which would race every other test that computes eligibility).
/// `rg` IGNORES an empty value, so "set" alone is not the condition -- it must be set AND non-empty.
pub(crate) fn rg_config_env_is_active(value: Option<&OsStr>) -> bool {
    value.is_some_and(|value| !value.is_empty())
}

/// Size cap for `plain_text_native_file_renders_identically`: a file above this is REFUSED rather
/// than probed.
///
/// MEASURED, not assumed (median of 21-25 runs; "gain" is the subprocess round trip saved, "probe"
/// is the full-content read this route adds):
///
/// | file size | gain | probe | net | probe as % of gain |
/// |---|---|---|---|---|
/// | 4 KB | +10.6ms | 0.18ms | +10.4ms | 2% |
/// | 200 KB | +13.6ms | 0.25ms | +13.4ms | 2% |
/// | 1 MB | +9.7ms | 1.57ms | +8.1ms | 16% |
/// | 4 MB | +10.1ms | 4.35ms | +5.7ms | 43% |
/// | 8 MB | +11.1ms | 8.68ms | +2.5ms | 78% |
/// | 8 MB, match-dense | **-2.2ms** | 8.68ms | **-10.9ms** | REGRESSION |
///
/// An earlier revision of this comment claimed the probe "can never cost more than it saves". That
/// was FALSE at the top of the range: at 8 MB the probe ate 78% of the win, and on a match-dense
/// 8 MB file the native engine is itself SLOWER than `rg` (gain goes negative), so the probe turned
/// a small loss into a 10.9ms regression. The cap is the only thing standing between this route and
/// that tail.
///
/// 512 KiB is chosen deliberately conservatively inside the 256 KB-1 MB band the measurements
/// support: the probe costs ~2% of the gain there, essentially every source file fits, and it keeps
/// a wide margin from the size at which the ENGINE (not the probe) starts losing to `rg` on
/// match-dense input. Files above the cap keep spawning `rg` -- no regression tail, at the price of
/// giving up a win on large files that was already mostly eaten by the probe.
pub(crate) const PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES: u64 = 512 * 1024;

/// FULL-CONTENT probe for `PlainTextNativeRequest::single_path_renders_identically`.
///
/// Three independently-verified emitter divergences make a file unsafe for the native plain-text
/// route, and none of them can be decided from the request alone -- they are properties of the
/// DATA (see `native_can_serve_plain_text` refusal note 5 for the measured byte diffs):
///   - any `\r` byte: the native plain sink strips a trailing `\r` (`trim_end_matches(['\n','\r'])`)
///     because nothing on this path sets a CRLF line terminator, so a CRLF file loses it while
///     `rg` keeps it;
///   - invalid UTF-8: the native plain sink is `grep_searcher::sinks::Lossy`, which substitutes
///     U+FFFD where `rg` writes raw bytes -- silent corruption;
///   - any NUL byte: the native binary-match notice spells `"/0"` where `rg` spells `"\0"`, and
///     that native spelling is a governed snapshot contract.
///
/// The check is deliberately WHOLE-FILE, not a prefix probe: an invalid byte at 1 MB diverges
/// exactly as hard as one at byte 0, so a prefix window cannot bound the risk. Fails CLOSED
/// (returns false = keep spawning `rg`) on any I/O error, oversize file, or non-file path.
///
/// MEMOIZED for the process lifetime. Both eligibility adapters run on an admitted request -- the
/// pre-clap front door decides whether to decline, then `handle_ripgrep_search` re-derives the same
/// verdict -- so without this cache an admitted request would read the file 3x (two probes plus the
/// search) where `rg` reads it once. With it: 2x. Removing the last extra read would mean threading
/// pre-read bytes into `run_native_search`, i.e. changing the engine shared with
/// `--json`/`--ndjson`/`--cpu` -- the wrong blast radius for this PR, and worth ~0.25ms at 200 KB
/// (about 2% of the win).
///
/// The cache is keyed by path. If the file is mutated between the probe and the search, a STALE
/// ADMISSION lets the native engine render content the probe would have refused -- CRLF whose
/// trailing `\r` is silently dropped, or invalid UTF-8 turned into U+FFFD. That is NOT the same
/// class as the ordinary read race `rg` already has (rg reads once and renders what it read); it
/// is a correctness window this route introduces. Severity is genuinely low -- single-shot
/// process, microseconds wide -- but it is a different failure than "you saw slightly older
/// bytes", and calling it a pre-existing race would understate it.
pub(crate) fn plain_text_native_file_renders_identically(path: &Path) -> bool {
    let cache = PLAIN_TEXT_NATIVE_PROBE_CACHE.get_or_init(|| Mutex::new(BTreeMap::new()));
    if let Ok(probed) = cache.lock() {
        if let Some(verdict) = probed.get(path) {
            return *verdict;
        }
    }

    let verdict = plain_text_native_probe_file(path);
    if let Ok(mut probed) = cache.lock() {
        probed.insert(path.to_path_buf(), verdict);
    }
    verdict
}

pub(crate) static PLAIN_TEXT_NATIVE_PROBE_CACHE: OnceLock<Mutex<BTreeMap<PathBuf, bool>>> =
    OnceLock::new();

pub(crate) fn plain_text_native_probe_file(path: &Path) -> bool {
    let Ok(metadata) = std::fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() || metadata.len() > PLAIN_TEXT_NATIVE_MAX_PROBE_BYTES {
        return false;
    }
    let Ok(bytes) = std::fs::read(path) else {
        return false;
    };
    // The bytes read MUST equal the size the OS reported. Measured on Linux (WSL):
    // `/proc/self/status` and `/proc/version` are S_ISREG with `st_size` 0 yet return 1460 and 166
    // bytes of clean UTF-8 -- so they passed every other clause and were ADMITTED. A file whose
    // reported size is a lie is precisely the shape where this probe cannot describe what the
    // SEARCH will later read: the content is generated per-open, so the memoized verdict is not
    // stale by a microsecond, it is unrelated by construction. This also fail-closes an ordinary
    // file that changes size between the `metadata` call and the read. Free -- both numbers are
    // already in hand.
    if bytes.len() as u64 != metadata.len() {
        return false;
    }
    if bytes.contains(&0u8) || bytes.contains(&b'\r') {
        return false;
    }
    std::str::from_utf8(&bytes).is_ok()
}

/// Refusal note (7): is this PATTERN safe for the native route?
///
/// Two independent gates, both fail-closed:
///
/// 1. TEXT SCAN for a line terminator or NUL, literal OR escaped. `rg` rejects these outright
///    (rc=2 plus a diagnostic); the native matcher accepts them and succeeds with zero matches
///    (rc=1, empty stderr), which is an exit-code REGRESSION an agent branching on 2-vs-1 would
///    misread as "no matches". The scan is deliberately over-broad -- it also refuses `\x..` and
///    `\u..` escapes wholesale rather than decoding them, and refuses `\n`-looking text even under
///    `-F` where it is a harmless literal -- because over-refusal costs only a `rg` spawn while
///    under-refusal costs correctness.
/// 2. COMPILE CHECK through `native_search::native_search_pattern_compiles`, which builds the
///    matcher with the very same `build_matcher` the search will use. A pattern that fails to
///    compile still exits 2, but only after the rg-fallback net prints a `warning: native CPU
///    search failed...` line `rg` never emits.
pub(crate) fn plain_text_native_pattern_is_renderable(
    pattern: &str,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
) -> bool {
    let bytes = pattern.as_bytes();
    if bytes
        .iter()
        .any(|&byte| matches!(byte, b'\n' | b'\r' | b'\0'))
    {
        return false;
    }

    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] != b'\\' {
            index += 1;
            continue;
        }
        if let Some(&escaped) = bytes.get(index + 1) {
            if matches!(escaped, b'n' | b'r' | b'0' | b'x' | b'u') {
                return false;
            }
        }
        // Skip the escaped byte too, so `\\n` (an escaped backslash followed by a plain `n`) is
        // not misread as the `\n` escape.
        index += 2;
    }

    // `smart_case` is never admitted, so it is always false on this route.
    //
    // TODO(perf): this builds a matcher and throws it away, and `run_native_search` then rebuilds
    // an identical one. Microseconds for an ordinary pattern, but it is duplicated work on the very
    // hot path this route exists to speed up, and a pathological pattern pays the regex-size-limit
    // walk twice. Threading the compiled matcher through to the search would remove both, at the
    // cost of widening the shared engine's entry signature -- deliberately out of this PR's blast
    // radius, and worth doing when the engine next changes shape.
    native_search_pattern_compiles(pattern, ignore_case, false, fixed_strings, word_boundary)
}

/// Builds the `PlainTextNativeRequest` for a resolved `tg search` invocation. THE production
/// computation -- `handle_ripgrep_search` calls it, and the adapter-agreement test drives it
/// rather than re-deriving anything, so a test can never pass against a mirror of the logic.
pub(crate) fn plain_text_native_request_for_search(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    stdout_is_terminal: bool,
) -> PlainTextNativeRequest {
    let single_path = (request.paths.len() == 1).then(|| Path::new(&request.paths[0]));
    let facts = PlainTextNativeRequest {
        pattern_count: request.patterns.len(),
        pattern_is_empty: request.patterns.iter().any(String::is_empty),
        pattern_is_native_renderable: false,
        path_count: request.paths.len(),
        path_was_implicit: request.path_was_implicit,
        single_path_is_regular_file: single_path.is_some_and(Path::is_file),
        single_path_is_stdin_sentinel: request.paths.iter().any(|path| path == "-"),
        single_path_renders_identically: false,
        structured_output: args.json || args.ndjson,
        explicit_format: args.format.is_some(),
        stdout_is_terminal,
        // Overwritten by `finish_plain_text_native_request`, the single owner of these clauses.
        rg_config_env_present: false,
        only_allowed_flags: search_args_allow_plain_text_native(args),
    };
    finish_plain_text_native_request(
        facts,
        "clap",
        request.patterns.first().map(String::as_str),
        args.ignore_case,
        args.fixed_strings,
        args.word_regexp,
        single_path,
    )
}

/// Fills the EXPENSIVE tier of a `PlainTextNativeRequest` -- and ONLY if the cheap tier already
/// passed. See `plain_text_native_cheap_checks_pass` for why this ordering is a latency contract:
/// an interactive single-file search, a `--json` run, and an `-A`/`-B`/`-C` run all reach the
/// clap-side adapter and would otherwise each burn a full file read on their way to `rg` anyway.
/// The two gates are themselves ordered cheapest-first: a regex compile costs microseconds, the
/// file probe costs a read.
///
/// ONE thing is filled ABOVE the cheap-tier gate, and deliberately so: `rg_config_env_present`.
/// It is not an exception to the contract, it IS part of the cheap tier -- a single `env::var_os`
/// with no I/O, no allocation beyond the value, and no filesystem access -- and
/// `plain_text_native_cheap_checks_pass` consults it, so it must be populated before that call or
/// the clause would read `false` and be a dead guard. It lives here rather than in the two
/// constructors purely so there is exactly one call path and the adapters cannot drift on it.
pub(crate) fn finish_plain_text_native_request(
    request: PlainTextNativeRequest,
    stage: &'static str,
    pattern: Option<&str>,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
    path: Option<&Path>,
) -> PlainTextNativeRequest {
    let finished = fill_plain_text_native_expensive_tier(
        request,
        pattern,
        ignore_case,
        fixed_strings,
        word_boundary,
        path,
    );
    record_plain_text_route_telemetry(stage, &finished);
    finished
}

/// The computation itself, split from `finish_plain_text_native_request` only so telemetry has a
/// single emission point covering every early-return path.
pub(crate) fn fill_plain_text_native_expensive_tier(
    mut request: PlainTextNativeRequest,
    pattern: Option<&str>,
    ignore_case: bool,
    fixed_strings: bool,
    word_boundary: bool,
    path: Option<&Path>,
) -> PlainTextNativeRequest {
    // The environment clause is filled HERE rather than by either constructor, so there is exactly
    // one call path to `rg_config_env_present()` and the two adapters cannot disagree about it.
    request.rg_config_env_present = rg_config_env_present();
    if !plain_text_native_cheap_checks_pass(&request) {
        return request;
    }
    let Some(pattern) = pattern else {
        return request;
    };
    request.pattern_is_native_renderable =
        plain_text_native_pattern_is_renderable(pattern, ignore_case, fixed_strings, word_boundary);
    if !request.pattern_is_native_renderable {
        return request;
    }
    if let Some(path) = path {
        request.single_path_renders_identically = plain_text_native_file_renders_identically(path);
    }
    request
}
