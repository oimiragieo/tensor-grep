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

/// Admission-rate telemetry, default-OFF. Answers "how often is this route ACTUALLY taken?", which
/// nothing else in the repo can: an independent review found 0 of 10 benchmark scenarios, 0 of 4
/// dogfood calls, and the entire MCP surface (which always builds `--json`) ineligible, so the
/// benchmark-regression gate can observe neither this route's benefit nor a future regression in it.
///
/// One JSON Lines record per eligibility evaluation, appended, carrying the stage and every clause,
/// so a consumer can report both the admit rate and a histogram of WHICH clause refused. Enable with
/// `TG_ROUTE_TELEMETRY=1`; the file defaults to the OS temp dir (never the workspace -- see the
/// file-placement rules) and is overridable with `TG_ROUTE_TELEMETRY_PATH`.
/// `scripts/summarize_route_telemetry.py` aggregates it.
pub(crate) const TG_ROUTE_TELEMETRY_ENV: &str = "TG_ROUTE_TELEMETRY";
pub(crate) const TG_ROUTE_TELEMETRY_PATH_ENV: &str = "TG_ROUTE_TELEMETRY_PATH";

pub(crate) fn route_telemetry_enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| env_flag_enabled(TG_ROUTE_TELEMETRY_ENV))
}

pub(crate) fn route_telemetry_path() -> PathBuf {
    env::var_os(TG_ROUTE_TELEMETRY_PATH_ENV)
        .map(PathBuf::from)
        .unwrap_or_else(|| env::temp_dir().join("tg-route-telemetry.jsonl"))
}

/// BEST-EFFORT and fail-silent by design: telemetry must never change what a search returns, so
/// every I/O error here is discarded rather than propagated. Gated behind a cached env read so a
/// disabled run pays one `OnceLock` load.
pub(crate) fn record_plain_text_route_telemetry(stage: &str, request: &PlainTextNativeRequest) {
    if !route_telemetry_enabled() {
        return;
    }
    let record = serde_json::json!({
        "stage": stage,
        "admitted": native_can_serve_plain_text(request),
        "cheap_checks_pass": plain_text_native_cheap_checks_pass(request),
        "only_allowed_flags": request.only_allowed_flags,
        "structured_output": request.structured_output,
        "explicit_format": request.explicit_format,
        "stdout_is_terminal": request.stdout_is_terminal,
        "rg_config_env_present": request.rg_config_env_present,
        "path_was_implicit": request.path_was_implicit,
        "pattern_count": request.pattern_count,
        "pattern_is_empty": request.pattern_is_empty,
        "path_count": request.path_count,
        "single_path_is_regular_file": request.single_path_is_regular_file,
        "single_path_is_stdin_sentinel": request.single_path_is_stdin_sentinel,
        "pattern_is_native_renderable": request.pattern_is_native_renderable,
        "single_path_renders_identically": request.single_path_renders_identically,
    });
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(route_telemetry_path())
        .and_then(|mut file| writeln!(file, "{record}"));
}

/// Raw-argv adapter for `native_can_serve_plain_text`, used by the pre-clap default search front
/// door (`parse_default_search_frontdoor_args`). Works on tokens rather than a parsed struct
/// because that front door runs BEFORE clap.
///
/// AGREEMENT CONTRACT: this adapter and `plain_text_native_request_for_search` (the clap-side
/// computation) must return the SAME verdict for the same argv, and
/// `plain_text_native_adapters_agree_on_every_shape` drives both to prove it. That is not
/// cosmetic: the front door is only ONE of the paths into `route_search` -- anything
/// `parse_early_ripgrep_args` rejects (a combined short like `-in`, a `-w`/`-F` request, an
/// unknown token) reaches clap and `route_search` anyway, so the raw-argv list is NOT the
/// admission gate. `search_args_allow_plain_text_native` is. Mirroring clap's argv handling here
/// (combined short clusters, `-e`/`--regexp` patterns) is what keeps the two in step.
///
/// `search_args` is the normalized `["tg", "search", ...]` form, so operand scanning starts at 2.
pub(crate) fn frontdoor_search_is_native_plain_text_eligible(search_args: &[OsString]) -> bool {
    frontdoor_search_is_native_plain_text_eligible_with_terminal(search_args, stdout_is_terminal())
}

/// Terminal-state-injected core of `frontdoor_search_is_native_plain_text_eligible`, mirroring the
/// `resolve_search_request` / `resolve_search_request_with_stdin` split so tests never depend on
/// whether the harness captured stdout.
pub(crate) fn frontdoor_search_is_native_plain_text_eligible_with_terminal(
    search_args: &[OsString],
    stdout_is_terminal: bool,
) -> bool {
    let tokens = search_args
        .iter()
        .skip(2)
        .map(|token| token.to_string_lossy().to_string())
        .collect::<Vec<_>>();

    let mut explicit_patterns: Vec<String> = Vec::new();
    let mut positionals: Vec<String> = Vec::new();
    let mut ignore_case = false;
    let mut fixed_strings = false;
    let mut word_boundary = false;
    let mut end_of_flags = false;
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if end_of_flags {
            positionals.push(token.clone());
        } else if token == "--" {
            // clap's end-of-options sentinel: everything after it is a positional.
            end_of_flags = true;
        } else if token == "-e" || token == "--regexp" {
            index += 1;
            match tokens.get(index) {
                Some(value) => explicit_patterns.push(value.clone()),
                None => return false,
            }
        } else if let Some(value) = token.strip_prefix("--regexp=") {
            explicit_patterns.push(value.to_string());
        } else if token.starts_with('-') && token != "-" {
            if !plain_text_native_flag_token_is_allowed(token) {
                return false;
            }
            if let Some(long) = token.strip_prefix("--") {
                // A long form must be understood HERE, not merely allow-listed, because its value
                // feeds the matcher. Refuse an unrecognized one so a future addition to
                // `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` cannot silently reach `build_matcher` with the
                // wrong flags -- the fail-closed direction.
                match long {
                    "ignore-case" => ignore_case = true,
                    "fixed-strings" => fixed_strings = true,
                    "word-regexp" => word_boundary = true,
                    "line-number" | "verbose" => {}
                    _ => return false,
                }
            } else {
                // A combined short cluster; the guard above already proved every letter admitted.
                for letter in token.chars().skip(1) {
                    match letter {
                        'i' => ignore_case = true,
                        'F' => fixed_strings = true,
                        'w' => word_boundary = true,
                        _ => {}
                    }
                }
            }
        } else {
            positionals.push(token.clone());
        }
        index += 1;
    }

    // Mirrors `resolve_search_request_with_stdin`: with no `-e`, the FIRST positional is the
    // pattern and the rest are paths; with `-e`, every positional is a path.
    let (patterns, paths) = if explicit_patterns.is_empty() {
        match positionals.split_first() {
            Some((pattern, paths)) => (vec![pattern.clone()], paths.to_vec()),
            None => (Vec::new(), Vec::new()),
        }
    } else {
        (explicit_patterns, positionals)
    };

    let single_path = (paths.len() == 1).then(|| Path::new(&paths[0]));
    let facts = PlainTextNativeRequest {
        pattern_count: patterns.len(),
        pattern_is_empty: patterns.iter().any(String::is_empty),
        pattern_is_native_renderable: false,
        path_count: paths.len(),
        path_was_implicit: paths.is_empty(),
        single_path_is_regular_file: single_path.is_some_and(Path::is_file),
        single_path_is_stdin_sentinel: paths.iter().any(|path| path == "-"),
        single_path_renders_identically: false,
        structured_output: false,
        explicit_format: false,
        stdout_is_terminal,
        // Overwritten by `finish_plain_text_native_request`, the single owner of these clauses.
        rg_config_env_present: false,
        only_allowed_flags: true,
    };
    let facts = finish_plain_text_native_request(
        facts,
        "frontdoor",
        patterns.first().map(String::as_str),
        ignore_case,
        fixed_strings,
        word_boundary,
        single_path,
    );
    native_can_serve_plain_text(&facts)
}

/// Parsed-`SearchArgs` adapter for `native_can_serve_plain_text`, used by `handle_ripgrep_search`.
///
/// The destructure below names EVERY `SearchArgs` field with no `..` rest pattern, mirroring
/// `index_flag_violations`' compile-time ratchet: adding a field to `SearchArgs` fails this
/// function's compilation until a human classifies it. A new flag can therefore never become
/// silently "allowed" on the native plain-text route -- the fail-closed direction is to add it to
/// the excluded list below, which preserves today's `rg` behavior for it.
pub(crate) fn search_args_allow_plain_text_native(args: &SearchArgs) -> bool {
    let SearchArgs {
        // ADMITTED -- see PLAIN_TEXT_NATIVE_ALLOWED_FLAGS for the per-flag proof.
        ignore_case: _,
        fixed_strings: _,
        word_regexp: _,
        line_number: _,
        verbose: _,
        // QUERY-DEFINING -- cardinality is enforced by the predicate's pattern_count/path_count.
        regexp: _,
        pattern: _,
        path: _,
        // EXCLUDED -- every one of these must be at its default for the request to stay eligible.
        no_fixed_strings,
        invert_match,
        no_invert_match,
        count,
        count_matches,
        no_line_number,
        column,
        no_column,
        replace,
        format,
        sort,
        sort_reverse,
        sort_files,
        null,
        null_data,
        multiline,
        multiline_dotall,
        context,
        after_context,
        before_context,
        max_count,
        max_depth,
        smart_case,
        globs,
        no_ignore,
        ignore,
        no_ignore_dot,
        no_ignore_exclude,
        no_ignore_files,
        no_ignore_global,
        no_ignore_parent,
        hidden,
        no_hidden,
        follow,
        text,
        files_with_matches,
        files_without_match,
        file_type,
        index,
        force_cpu,
        gpu_device_ids,
        color,
        path_separator,
        only_matching,
        vimgrep,
        passthru,
        json,
        ndjson,
        pcre2,
        auto_hybrid_regex,
        unicode,
        pcre2_unicode,
        max_filesize,
        no_ignore_vcs,
        require_git,
        messages,
        no_config,
        pcre2_version,
        type_list,
        version,
        files,
        allow_broad_generated_scan,
    } = args;

    !*no_fixed_strings
        && !*files
        && !*allow_broad_generated_scan
        && !*invert_match
        && !*no_invert_match
        && !*count
        && !*count_matches
        && !*no_line_number
        && !*column
        && !*no_column
        && replace.is_none()
        && format.is_none()
        && sort.is_none()
        && sort_reverse.is_none()
        && !*sort_files
        && !*null
        && !*null_data
        && !*multiline
        && !*multiline_dotall
        && context.is_none()
        && after_context.is_none()
        && before_context.is_none()
        && max_count.is_none()
        && max_depth.is_none()
        && !*smart_case
        && globs.is_empty()
        && !*no_ignore
        && !*ignore
        && !*no_ignore_dot
        && !*no_ignore_exclude
        && !*no_ignore_files
        && !*no_ignore_global
        && !*no_ignore_parent
        && !*hidden
        && !*no_hidden
        && !*follow
        && !*text
        && !*files_with_matches
        && !*files_without_match
        && file_type.is_empty()
        && !*index
        && !*force_cpu
        && gpu_device_ids.is_empty()
        && color.is_none()
        && path_separator.is_none()
        && !*only_matching
        && !*vimgrep
        && !*passthru
        && !*json
        && !*ndjson
        && !*pcre2
        && !*auto_hybrid_regex
        && !*unicode
        && !*pcre2_unicode
        && max_filesize.is_none()
        && !*no_ignore_vcs
        && !*require_git
        && !*messages
        && !*no_config
        && !*pcre2_version
        && !*type_list
        && !*version
}

pub(crate) fn ripgrep_args_need_broad_generated_guard(args: &RipgrepSearchArgs) -> bool {
    let has_scan_bound =
        args.max_depth.is_some() || !args.globs.is_empty() || !args.file_types.is_empty();
    !has_scan_bound && (args.no_ignore || args.no_ignore_files || args.no_ignore_vcs)
}

pub(crate) fn search_args_have_generated_scan_bound(args: &SearchArgs) -> bool {
    args.max_depth.is_some() || !args.globs.is_empty() || !args.file_type.is_empty()
}

pub(crate) fn search_args_need_broad_generated_guard(args: &SearchArgs) -> bool {
    !search_args_have_generated_scan_bound(args)
        && (args.no_ignore || args.no_ignore_files || args.no_ignore_vcs)
}

// Broad generated-scan helpers moved to search_files.rs.

// Bug #88/#480/#100: `IMPLICIT_SEARCH_WALK_FILE_CEILING`, `implicit_search_walk_exceeds_ceiling`,
// and `format_unbounded_implicit_search_walk_error` used to live here. They are now HOISTED into
// `rg_passthrough.rs` (a library module) so `execute_ripgrep_search` -- which lives there, not
// here -- can call the probe as its own first statement, closing a native-frontdoor bypass this
// binary-crate-local copy could not reach (this `tg` binary and the `tensor_grep_rs` library are
// separate crate compilations; the library cannot call back into a function defined only in this
// bin crate). See `rg_passthrough.rs` for the full history and the current implementation; this
// file's existing tests re-import the moved items via `use tensor_grep_rs::rg_passthrough::{...}`
// inside `mod tests` below.

pub(crate) fn parse_early_ripgrep_args_with_metadata(
    raw_args: &[OsString],
) -> Option<(RipgrepSearchArgs, bool)> {
    let mut args = RipgrepSearchArgs {
        files: false,
        json: false,
        ignore_case: false,
        fixed_strings: false,
        no_fixed_strings: false,
        invert_match: false,
        no_invert_match: false,
        count: false,
        count_matches: false,
        line_number: false,
        no_line_number: false,
        column: false,
        only_matching: false,
        context: None,
        after_context: None,
        before_context: None,
        max_count: None,
        word_regexp: false,
        smart_case: false,
        globs: Vec::new(),
        ignore: false,
        no_ignore: false,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        require_git: false,
        hidden: false,
        no_hidden: false,
        follow: false,
        text: false,
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
        max_depth: None,
        null: false,
        null_data: false,
        multiline: false,
        no_multiline: false,
        multiline_dotall: false,
        no_multiline_dotall: false,
        patterns: Vec::new(),
        paths: Vec::new(),
        // Placeholder -- overwritten below once we know whether the caller supplied an explicit
        // PATH positional (audit #100: this is THE FIX, see the `-e`-vs-positional branch below).
        path_was_implicit: false,
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
    };

    let mut positionals: Vec<String> = Vec::new();
    let mut explicit_rg_format = false;
    let tokens = raw_args
        .iter()
        .skip(2)
        .map(|arg| arg.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    let mut index = 0usize;
    while index < tokens.len() {
        let token = &tokens[index];
        match token.as_str() {
            "-i" | "--ignore-case" => args.ignore_case = true,
            "-F" | "--fixed-strings" => args.fixed_strings = true,
            "--no-fixed-strings" => args.no_fixed_strings = true,
            "-v" | "--invert-match" => args.invert_match = true,
            "--no-invert-match" => args.no_invert_match = true,
            "-c" | "--count" => args.count = true,
            "--count-matches" => args.count_matches = true,
            "--json" => args.json = true,
            "-n" | "--line-number" => {
                args.line_number = true;
                args.no_line_number = false;
            }
            "-N" | "--no-line-number" => {
                args.line_number = false;
                args.no_line_number = true;
            }
            "-o" | "--only-matching" => args.only_matching = true,
            "-w" | "--word-regexp" => args.word_regexp = true,
            "-0" | "--null" => args.null = true,
            "--null-data" => args.null_data = true,
            "-U" | "--multiline" => args.multiline = true,
            "--no-multiline" => args.no_multiline = true,
            "--multiline-dotall" => args.multiline_dotall = true,
            "--no-multiline-dotall" => args.no_multiline_dotall = true,
            "--ignore" => {
                args.ignore = true;
                args.no_ignore = false;
            }
            "--no-ignore" => {
                args.ignore = false;
                args.no_ignore = true;
            }
            "--no-ignore-dot" => args.no_ignore_dot = true,
            "--no-ignore-exclude" => args.no_ignore_exclude = true,
            "--no-ignore-files" => args.no_ignore_files = true,
            "--no-ignore-global" => args.no_ignore_global = true,
            "--no-ignore-parent" => args.no_ignore_parent = true,
            "--require-git" => args.require_git = true,
            "--no-config" => args.no_config = true,
            "--passthru" => args.passthru = true,
            "--passthrough" => args.passthru = true,
            "--auto-hybrid-regex" => args.auto_hybrid_regex = true,
            "--no-auto-hybrid-regex" => args.no_auto_hybrid_regex = true,
            "--pcre2-unicode" => {
                args.pcre2_unicode = true;
            }
            "--no-pcre2-unicode" => args.no_pcre2_unicode = true,
            "--no-crlf" => args.no_crlf = true,
            "--no-encoding" => args.no_encoding = true,
            "--no-mmap" => args.no_mmap = true,
            "--no-pcre2" => args.no_pcre2 = true,
            "--no-pre" => args.no_pre = true,
            "--no-search-zip" => args.no_search_zip = true,
            "--unicode" => args.unicode = true,
            "--no-text" => args.no_text = true,
            "--no-binary" => args.no_binary = true,
            "--no-follow" => args.no_follow = true,
            "--no-glob-case-insensitive" => args.no_glob_case_insensitive = true,
            "--no-ignore-file-case-insensitive" => {
                args.no_ignore_file_case_insensitive = true;
            }
            "--ignore-dot" => args.ignore_dot = true,
            "--ignore-exclude" => args.ignore_exclude = true,
            "--ignore-files" => args.ignore_files = true,
            "--ignore-global" => args.ignore_global = true,
            "--ignore-messages" => args.ignore_messages = true,
            "--ignore-parent" => args.ignore_parent = true,
            "--ignore-vcs" => args.ignore_vcs = true,
            "--no-one-file-system" => args.no_one_file_system = true,
            "--no-block-buffered" => args.no_block_buffered = true,
            "--no-byte-offset" => args.no_byte_offset = true,
            "--column" => {
                args.column = true;
                args.no_column = false;
            }
            "--no-column" => {
                args.column = false;
                args.no_column = true;
            }
            "--no-context-separator" => args.no_context_separator = true,
            "--no-include-zero" => args.no_include_zero = true,
            "--no-line-buffered" => args.no_line_buffered = true,
            "--no-max-columns-preview" => args.no_max_columns_preview = true,
            "--no-trim" => args.no_trim = true,
            "--no-json" => args.no_json = true,
            "--no-stats" => args.no_stats = true,
            "--messages" => args.messages = true,
            "-C" | "--context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.context = Some(value);
            }
            "-A" | "--after-context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.after_context = Some(value);
            }
            "-B" | "--before-context" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.before_context = Some(value);
            }
            "-m" | "--max-count" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.max_count = Some(value);
            }
            "-d" | "--max-depth" | "--maxdepth" => {
                index += 1;
                let value = tokens.get(index)?.parse::<usize>().ok()?;
                args.max_depth = Some(value);
            }
            _ if token.starts_with("--max-count=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_count = Some(value);
            }
            _ if token.starts_with("--max-depth=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_depth = Some(value);
            }
            _ if token.starts_with("--maxdepth=") => {
                let value = token
                    .split_once('=')
                    .and_then(|(_, value)| value.parse::<usize>().ok())?;
                args.max_depth = Some(value);
            }
            "--color" => {
                index += 1;
                args.color = Some(tokens.get(index)?.clone());
            }
            "--path-separator" => {
                index += 1;
                args.path_separator = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--path-separator=") => {
                args.path_separator =
                    Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--vimgrep" => args.vimgrep = true,
            "--no-hidden" => {
                args.hidden = false;
                args.no_hidden = true;
            }
            "--hidden" | "-." => {
                args.hidden = true;
                args.no_hidden = false;
            }
            "--format" => {
                index += 1;
                if tokens.get(index)? != "rg" {
                    return None;
                }
                explicit_rg_format = true;
            }
            _ if token.starts_with("--format=") => {
                if token.split_once('=').map(|(_, value)| value) != Some("rg") {
                    return None;
                }
                explicit_rg_format = true;
            }
            "--sort" => {
                index += 1;
                args.sort = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--sort=") => {
                args.sort = Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--sortr" => {
                index += 1;
                args.sort_reverse = Some(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--sortr=") => {
                args.sort_reverse =
                    Some(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "--sort-files" => args.sort_files = true,
            "--no-sort-files" => args.sort_files = false,
            "-e" | "--regexp" => {
                index += 1;
                args.patterns.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--regexp=") => {
                args.patterns
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "-g" | "--glob" => {
                index += 1;
                args.globs.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--glob=") => {
                args.globs
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            "-t" | "--type" => {
                index += 1;
                args.file_types.push(tokens.get(index)?.clone());
            }
            _ if token.starts_with("--type=") => {
                args.file_types
                    .push(token.split_once('=').map(|(_, value)| value.to_string())?);
            }
            // LOAD-BEARING (task #271): this is the actual fail-closed guarantee that an
            // unrecognized flag never silently reaches the native fast path -- every arm above
            // is a finite, explicit allowlist, so any `-`-prefixed token this function does not
            // otherwise understand (including a ripgrep combined-short-flag cluster like `-uu`,
            // `-uuu`, or `-iu` that the early parser does not recognize) falls through to here
            // and returns `None`, forcing the
            // caller back to the full Python CLI rather than being silently misparsed or
            // dropped. Do not narrow this arm without an equally fail-closed replacement.
            _ if token.starts_with('-') => return None,
            _ => positionals.push(token.clone()),
        }
        index += 1;
    }

    if args.patterns.is_empty() {
        if positionals.len() < 2 {
            return None;
        }
        // Positional-pattern form always requires >= 2 positionals (pattern + >= 1 path) to even
        // reach this branch, so the path is always explicit here.
        args.patterns.push(positionals[0].clone());
        args.paths = positionals[1..].to_vec();
        args.path_was_implicit = false;
    } else {
        // THE FIX (audit #100): `-e`/`--regexp` form. `positionals` here is whatever the user
        // supplied as trailing PATH arguments (the pattern came via `-e`, not a positional) --
        // record `path_was_implicit` from whether that list is empty BEFORE the `["."]` default
        // substitution below, mirroring `resolve_search_request_with_stdin` (the full-CLI
        // equivalent, main.rs `path_was_implicit = true` set inside its own `paths.is_empty()`
        // branch). This is the exact gap that let `tg search -e "TODO" --glob "*.py"` with no
        // PATH bypass the walk-ceiling probe entirely: `paths` silently became `["."]` with no
        // record that the root was implicit, so no caller could gate on it.
        args.paths = positionals;
        args.path_was_implicit = args.paths.is_empty();
        if args.path_was_implicit && !stdin_should_search_implicit_path() {
            args.paths.push(".".to_string());
        }
    }
    Some((args, explicit_rg_format))
}

pub(crate) fn parse_default_search_frontdoor_args_with_metadata(
    raw_args: &[OsString],
) -> Option<(RipgrepSearchArgs, bool)> {
    let search_args = normalize_top_level_search_args(raw_args)?;
    let (args, explicit_rg_format) = parse_early_ripgrep_args_with_metadata(&search_args)?;
    if args.json && !explicit_rg_format {
        return None;
    }
    if ripgrep_args_need_broad_generated_guard(&args) {
        return None;
    }
    // Perf: a provably-rg-identical plain-text search does NOT need the `rg` subprocess. Decline
    // the passthrough so the request falls through to clap -> `handle_ripgrep_search` ->
    // `route_search`, which re-derives the SAME predicate and selects the in-process native CPU
    // engine. Declining here is behavior-preserving by construction: every downstream front door
    // (`search_format_python_passthrough_args`, the positional CLI, clap) already handles this
    // shape today, and if the native engine errors, `allow_rg_fallback` still hands the request
    // to real `rg`. `--format rg` is exempted because it is an explicit demand for ripgrep's own
    // renderer.
    if !explicit_rg_format && frontdoor_search_is_native_plain_text_eligible(&search_args) {
        return None;
    }
    (explicit_rg_format || should_use_early_ripgrep_fast_path(&args))
        .then_some((args, explicit_rg_format))
}

pub(crate) fn parse_early_ripgrep_args(raw_args: &[OsString]) -> Option<RipgrepSearchArgs> {
    parse_early_ripgrep_args_with_metadata(raw_args).map(|(args, _)| args)
}

#[cfg(test)]
pub(crate) fn parse_default_search_frontdoor_args(
    raw_args: &[OsString],
) -> Option<RipgrepSearchArgs> {
    parse_default_search_frontdoor_args_with_metadata(raw_args).map(|(args, _)| args)
}

pub(crate) fn parse_early_positional_ripgrep_args(
    raw_args: &[OsString],
) -> Option<RipgrepSearchArgs> {
    let mut cli = PositionalCli::try_parse_from(raw_args).ok()?;
    if let Ok(m) = PositionalCli::command().try_get_matches_from(raw_args) {
        apply_last_ignore_wins(&m, &mut cli.no_ignore, &mut cli.ignore);
    }
    let pattern = cli.pattern.clone()?;
    let paths = implicit_search_paths(&cli.path, stdin_should_search_implicit_path());

    if cli.replace.is_some() || cli.force_cpu || !cli.gpu_device_ids.is_empty() {
        return None;
    }
    if cli.json || cli.ndjson || cli.verbose {
        return None;
    }

    Some(positional_ripgrep_args(&cli, &pattern, &paths))
}
