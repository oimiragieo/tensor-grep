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

pub(crate) fn stdin_should_search_implicit_path() -> bool {
    grep_cli::is_readable_stdin()
}

pub(crate) fn first_missing_search_path(paths: &[String]) -> Option<String> {
    paths
        .iter()
        .find(|path| path.as_str() != "-" && !Path::new(path).exists())
        .cloned()
}

pub(crate) fn exit_json_search_input_error_if_needed(
    json: bool,
    ndjson: bool,
    patterns: &[String],
    paths: &[String],
) {
    if !json && !ndjson {
        return;
    }
    if patterns.iter().any(|pattern| pattern.is_empty()) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            "empty_pattern",
            "PATTERN must not be empty.",
        );
    }
    if let Some(missing_path) = first_missing_search_path(paths) {
        exit_structured_search_error_if_needed(
            json,
            ndjson,
            "path_not_found",
            format!("search path does not exist: {missing_path}"),
        );
    }
}

pub(crate) fn resolve_search_request(args: &SearchArgs) -> anyhow::Result<ResolvedSearchRequest> {
    resolve_search_request_with_stdin(args, stdin_should_search_implicit_path())
}

pub(crate) fn resolve_search_request_with_stdin(
    args: &SearchArgs,
    stdin_searches_implicit_path: bool,
) -> anyhow::Result<ResolvedSearchRequest> {
    let mut patterns = args.regexp.clone();
    let mut path_was_implicit = false;
    let paths = if args.regexp.is_empty() {
        if let Some(pattern) = args.pattern.as_ref() {
            patterns.push(pattern.clone());
        }
        if args.path.is_empty() {
            path_was_implicit = true;
            if stdin_searches_implicit_path {
                Vec::new()
            } else {
                vec![".".to_string()]
            }
        } else {
            args.path.clone()
        }
    } else {
        let mut paths = Vec::new();
        if let Some(path) = args.pattern.as_ref() {
            paths.push(path.clone());
        }
        paths.extend(args.path.clone());
        if paths.is_empty() {
            path_was_implicit = true;
            if stdin_searches_implicit_path {
                Vec::new()
            } else {
                vec![".".to_string()]
            }
        } else {
            paths
        }
    };

    if patterns.is_empty() {
        anyhow::bail!("search requires a pattern or at least one -e/--regexp pattern");
    }

    Ok(ResolvedSearchRequest {
        patterns,
        paths,
        path_was_implicit,
    })
}

/// Audit fix #1 (index capability validator, 2026-07-11): per-field classification of every
/// `SearchArgs` flag against the trigram index engine (`run_index_query` / `TrigramIndex`).
/// Three policy classes:
///   - `Honor`: the index path already correctly implements this flag (or the flag is one of
///     the query-defining fields -- `pattern`/`regexp`/`path` -- whose cardinality is enforced
///     separately, via `request.patterns.len() != 1` below and the `request.paths.len() != 1`
///     bail in `handle_index_search`).
///   - `PassthroughSafe`: the flag is a semantic no-op on this path -- it only restates a
///     default that already holds here (e.g. `--unicode`, `--no-hidden`, `--ignore`), or it only
///     changes behavior once ripgrep itself is invoked (e.g. `--auto-hybrid-regex`), which the
///     index path never does.
///   - `Refuse`: the flag changes the result set or output shape in a way the index cannot (yet)
///     reproduce. Silently dropping it would return wrong-but-plausible results with exit 0, so
///     the explicit `--index` path must fail closed and warm auto-routing must reroute past the
///     index instead (see the two call sites below and in `handle_index_search`).
///
/// Supersedes the original 6-flag ad-hoc deny-list (H1a, audit #79/#10/#14): that list was
/// correct as far as it went, but `run_index_query` only ever consulted
/// pattern/ignore_case/smart_case/fixed_strings/json/ndjson/count -- every OTHER flag (`--hidden`,
/// `--sort`, `--max-depth`, `-t`, `-o`, `-r`, `--max-filesize`, the `--no-ignore-*` family, ...)
/// was silently dropped once it reached this function instead of being honored or refused. (In
/// practice most of those flags are only non-json/non-ndjson-reachable via
/// `search_prefers_ripgrep_passthrough`'s early rg-passthrough branch in
/// `handle_ripgrep_search`, which diverts them to `rg` before `route_search` ever runs; combined
/// with `--json`/`--ndjson` that branch is skipped and they reach here directly -- see the H1e
/// smart-case tests below for the same reachability shape.)
///
/// The destructure below names EVERY `SearchArgs` field with no `..` rest pattern: adding a new
/// field to `SearchArgs` fails this function's compilation until it is explicitly classified
/// here (the compile-time ratchet). `INDEX_FLAG_POLICY` (test-only, defined just below) is a
/// second, independent listing used as a *runtime* backstop -- see
/// `index_flag_policy_table_is_exhaustive_over_search_args_clap_ids` in the test module -- so an
/// edit that adds a field here (satisfying the compiler) without ALSO updating that table still
/// fails a test instead of silently drifting out of sync.
pub(crate) fn index_flag_violations(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> Vec<&'static str> {
    let mut violations = Vec::new();

    let SearchArgs {
        ignore_case: _,      // Honor: threaded into TrigramIndex::search.
        fixed_strings: _,    // Honor: threaded into TrigramIndex::search.
        no_fixed_strings: _, // PassthroughSafe: restates the `fixed_strings` default (false).
        invert_match,
        no_invert_match: _, // PassthroughSafe: restates the `invert_match` default (false).
        count: _,           // Honor: aggregate len(unique_line_matches).
        count_matches,
        line_number: _, // Honor: threaded as `line_number && !no_line_number` (fold-in b).
        no_line_number: _, // Honor: see line_number.
        column,
        no_column: _, // PassthroughSafe: the index path never emits column offsets.
        replace,
        format,
        sort,
        sort_reverse,
        sort_files,
        null,
        null_data,
        multiline,
        multiline_dotall,
        context: _,        // Refuse: covered by search_has_context() below (existing H1a).
        after_context: _,  // Refuse: see context.
        before_context: _, // Refuse: see context.
        max_count,
        max_depth,
        word_regexp,
        smart_case: _, // Honor: H1e, resolved per-pattern inside run_index_query.
        globs,
        no_ignore: _, // Honor: threaded as build/staleness mode (H1d).
        ignore: _,    // PassthroughSafe: restates the `no_ignore` default (respect ignore files).
        no_ignore_dot,
        no_ignore_exclude,
        no_ignore_files,
        no_ignore_global,
        no_ignore_parent,
        hidden,
        no_hidden: _, // PassthroughSafe: the index build walker hardcodes hidden-file exclusion
        // (`WalkBuilder::hidden(true)` in index.rs) regardless of query flags, so
        // this restates what already happens.
        follow,
        text,
        files_with_matches,
        files_without_match,
        file_type,
        index: _, // Honor: the field that selects this engine; not a compat flag itself.
        force_cpu,
        gpu_device_ids,
        color,
        path_separator,
        only_matching,
        vimgrep,
        passthru,
        json: _,    // Honor.
        ndjson: _,  // Honor.
        verbose: _, // Honor: emit_verbose_metadata is called from run_index_query.
        regexp: _,  // Honor: cardinality enforced via request.patterns.len() below.
        pattern: _, // Honor: the query itself.
        path: _,    // Honor: cardinality enforced by handle_index_search's paths.len()!=1 bail.
        pcre2,
        auto_hybrid_regex: _, // PassthroughSafe: only affects behavior once rg is actually invoked.
        unicode: _,           // PassthroughSafe: restates the Unicode-mode default (on).
        pcre2_unicode: _,     // PassthroughSafe: alias of `unicode`; same reasoning.
        max_filesize,
        no_ignore_vcs,
        require_git,
        messages: _, // PassthroughSafe: restates the default; index has no diagnostic-message mode.
        no_config: _, // PassthroughSafe: the index path never reads an rg config file.
        pcre2_version: _, // PassthroughSafe: early-exit flag (handle_ripgrep_search top), unreachable here.
        type_list: _,     // PassthroughSafe: early-exit flag, unreachable here.
        version: _,       // PassthroughSafe: early-exit flag, unreachable here.
        files,
        allow_broad_generated_scan: _,
    } = args;

    if *files {
        violations.push("--files");
    }

    if *invert_match {
        violations.push("-v/--invert-match");
    }
    if search_has_context(args) {
        violations.push("-C/-A/-B (context)");
    }
    if max_count.is_some() {
        violations.push("-m/--max-count");
    }
    if *word_regexp {
        violations.push("-w/--word-regexp");
    }
    if !globs.is_empty() {
        violations.push("-g/--glob");
    }
    if request.patterns.len() != 1 {
        violations.push("multiple patterns (-e)");
    }
    if *count_matches {
        violations.push("--count-matches");
    }
    if *column {
        violations.push("--column");
    }
    if replace.is_some() {
        violations.push("-r/--replace");
    }
    if format.is_some() {
        violations.push("--format");
    }
    if sort.is_some() {
        violations.push("--sort");
    }
    if sort_reverse.is_some() {
        violations.push("--sortr");
    }
    if *sort_files {
        violations.push("--sort-files");
    }
    if *null {
        violations.push("-0/--null");
    }
    if *null_data {
        violations.push("--null-data");
    }
    if *multiline {
        violations.push("-U/--multiline");
    }
    if *multiline_dotall {
        violations.push("--multiline-dotall");
    }
    if max_depth.is_some() {
        violations.push("-d/--max-depth");
    }
    if *no_ignore_dot {
        violations.push("--no-ignore-dot");
    }
    if *no_ignore_exclude {
        violations.push("--no-ignore-exclude");
    }
    if *no_ignore_files {
        violations.push("--no-ignore-files");
    }
    if *no_ignore_global {
        violations.push("--no-ignore-global");
    }
    if *no_ignore_parent {
        violations.push("--no-ignore-parent");
    }
    if *hidden {
        violations.push("-./--hidden");
    }
    if *follow {
        violations.push("-L/--follow");
    }
    if *text {
        violations.push("-a/--text");
    }
    if *files_with_matches {
        violations.push("-l/--files-with-matches");
    }
    if *files_without_match {
        violations.push("--files-without-match");
    }
    if !file_type.is_empty() {
        violations.push("-t/--type");
    }
    if *force_cpu {
        // fold-in (c): --index and --cpu request contradictory engines; route_search currently
        // checks explicit_index before force_cpu, so without this --cpu would be silently
        // dropped rather than honored or refused.
        violations.push("--cpu/--force-cpu");
    }
    if !gpu_device_ids.is_empty() {
        // fold-in (c): same contradiction as force_cpu, for explicit --gpu-device-ids.
        violations.push("--gpu-device-ids");
    }
    if let Some(mode) = color {
        // `--color never`/`--color auto` restate a no-op default (the index's plain-text
        // emitter never writes ANSI escapes either way); only an explicit `always` (or any other
        // unrecognized value) asks for something this path cannot produce.
        if mode.as_str() != "never" && mode.as_str() != "auto" {
            violations.push("--color");
        }
    }
    if path_separator.is_some() {
        violations.push("--path-separator");
    }
    if *only_matching {
        violations.push("-o/--only-matching");
    }
    if *vimgrep {
        violations.push("--vimgrep");
    }
    if *passthru {
        violations.push("--passthru");
    }
    if *pcre2 {
        // fold-in (c): defense in depth only. route_search already sends --pcre2 to
        // ripgrep_pcre2() ahead of explicit_index when rg is available, and
        // handle_ripgrep_search's unconditional `require_ripgrep_or_exit(rg_available,
        // "--pcre2")` guard already fails closed before either is reached when rg is NOT
        // available -- so this arm should be unreachable in practice today. Kept so a future
        // routing-order change fails closed instead of silently running PCRE2 syntax through
        // the index's non-PCRE2 regex engine.
        violations.push("-P/--pcre2");
    }
    if max_filesize.is_some() {
        violations.push("--max-filesize");
    }
    if *no_ignore_vcs {
        violations.push("--no-ignore-vcs");
    }
    if *require_git {
        violations.push("--require-git");
    }

    violations
}

/// Test-only, independent-of-the-destructure classification table -- see
/// `index_flag_violations`'s doc comment for why this exists alongside the compile-time
/// exhaustive destructure. Keyed by clap arg id, which for a `#[derive(Args)]` struct field is
/// the Rust field name itself (not the `long = "..."` CLI spelling).
#[cfg(test)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum IndexFlagPolicy {
    /// The index path already correctly implements this flag.
    Honor,
    /// The flag is a semantic no-op on the index path (restates a default, or only matters once
    /// ripgrep itself runs). `color` is listed here even though `--color always` is refused at
    /// runtime by value -- see `index_flag_violations`.
    PassthroughSafe,
    /// The flag changes the result set or output shape; `--index` must fail closed / reroute.
    Refuse,
}

#[cfg(test)]
pub(crate) const INDEX_FLAG_POLICY: &[(&str, IndexFlagPolicy)] = &[
    ("ignore_case", IndexFlagPolicy::Honor),
    ("fixed_strings", IndexFlagPolicy::Honor),
    ("no_fixed_strings", IndexFlagPolicy::PassthroughSafe),
    ("invert_match", IndexFlagPolicy::Refuse),
    ("no_invert_match", IndexFlagPolicy::PassthroughSafe),
    ("count", IndexFlagPolicy::Honor),
    ("count_matches", IndexFlagPolicy::Refuse),
    ("line_number", IndexFlagPolicy::Honor),
    ("no_line_number", IndexFlagPolicy::Honor),
    ("column", IndexFlagPolicy::Refuse),
    ("no_column", IndexFlagPolicy::PassthroughSafe),
    ("replace", IndexFlagPolicy::Refuse),
    ("format", IndexFlagPolicy::Refuse),
    ("sort", IndexFlagPolicy::Refuse),
    ("sort_reverse", IndexFlagPolicy::Refuse),
    ("sort_files", IndexFlagPolicy::Refuse),
    ("null", IndexFlagPolicy::Refuse),
    ("null_data", IndexFlagPolicy::Refuse),
    ("multiline", IndexFlagPolicy::Refuse),
    ("multiline_dotall", IndexFlagPolicy::Refuse),
    ("context", IndexFlagPolicy::Refuse),
    ("after_context", IndexFlagPolicy::Refuse),
    ("before_context", IndexFlagPolicy::Refuse),
    ("max_count", IndexFlagPolicy::Refuse),
    ("max_depth", IndexFlagPolicy::Refuse),
    ("word_regexp", IndexFlagPolicy::Refuse),
    ("smart_case", IndexFlagPolicy::Honor),
    ("globs", IndexFlagPolicy::Refuse),
    ("no_ignore", IndexFlagPolicy::Honor),
    ("ignore", IndexFlagPolicy::PassthroughSafe),
    ("no_ignore_dot", IndexFlagPolicy::Refuse),
    ("no_ignore_exclude", IndexFlagPolicy::Refuse),
    ("no_ignore_files", IndexFlagPolicy::Refuse),
    ("no_ignore_global", IndexFlagPolicy::Refuse),
    ("no_ignore_parent", IndexFlagPolicy::Refuse),
    ("hidden", IndexFlagPolicy::Refuse),
    ("no_hidden", IndexFlagPolicy::PassthroughSafe),
    ("follow", IndexFlagPolicy::Refuse),
    ("text", IndexFlagPolicy::Refuse),
    ("files_with_matches", IndexFlagPolicy::Refuse),
    ("files_without_match", IndexFlagPolicy::Refuse),
    ("file_type", IndexFlagPolicy::Refuse),
    ("index", IndexFlagPolicy::Honor),
    ("force_cpu", IndexFlagPolicy::Refuse),
    ("gpu_device_ids", IndexFlagPolicy::Refuse),
    ("color", IndexFlagPolicy::PassthroughSafe),
    ("path_separator", IndexFlagPolicy::Refuse),
    ("only_matching", IndexFlagPolicy::Refuse),
    ("vimgrep", IndexFlagPolicy::Refuse),
    ("passthru", IndexFlagPolicy::Refuse),
    ("json", IndexFlagPolicy::Honor),
    ("ndjson", IndexFlagPolicy::Honor),
    ("verbose", IndexFlagPolicy::Honor),
    ("regexp", IndexFlagPolicy::Honor),
    ("pattern", IndexFlagPolicy::Honor),
    ("path", IndexFlagPolicy::Honor),
    ("pcre2", IndexFlagPolicy::Refuse),
    ("auto_hybrid_regex", IndexFlagPolicy::PassthroughSafe),
    ("unicode", IndexFlagPolicy::PassthroughSafe),
    ("pcre2_unicode", IndexFlagPolicy::PassthroughSafe),
    ("max_filesize", IndexFlagPolicy::Refuse),
    ("no_ignore_vcs", IndexFlagPolicy::Refuse),
    ("require_git", IndexFlagPolicy::Refuse),
    ("messages", IndexFlagPolicy::PassthroughSafe),
    ("no_config", IndexFlagPolicy::PassthroughSafe),
    ("pcre2_version", IndexFlagPolicy::PassthroughSafe),
    ("type_list", IndexFlagPolicy::PassthroughSafe),
    ("version", IndexFlagPolicy::PassthroughSafe),
    ("files", IndexFlagPolicy::Refuse),
    (
        "allow_broad_generated_scan",
        IndexFlagPolicy::PassthroughSafe,
    ),
];

/// Detects warm-index routing state, loading the persisted index AT MOST ONCE (audit #138 item
/// #3): the previous version only returned `IndexRoutingState`, so the immediate warm-routing
/// caller (below) and `handle_index_search` each independently re-loaded + re-deserialized the
/// SAME `.tg_index` file for a single search invocation. Returning the loaded index lets the
/// caller reuse it directly instead of reading the file a second time. This is a pure READ -- it
/// never acquires the write lock (`IndexLockGuard` is only taken around a `save()`, never here);
/// see `save_index_locked` for where persistence is actually gated.
///
/// The returned `Option<TrigramIndex>` is `Some` only when a load actually succeeded; it is
/// always `None` when the guard clauses short-circuit (no `.tg_index` yet, explicit `--index`,
/// or an incompatible flag combination) or when the load itself failed (a corrupt/legacy index),
/// mirroring the `IndexRoutingState` this replaces field-for-field.
pub(crate) fn detect_warm_index_state(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> (IndexRoutingState, Option<LoadedIndex>) {
    if args.index
        || request.paths.len() != 1
        || request.patterns.len() != 1
        || request.patterns[0].len() < 3
        || !index_flag_violations(args, request).is_empty()
    {
        return (IndexRoutingState::default(), None);
    }

    let index_path = tensor_grep_rs::index_cache::resolve_read_index_path(request.primary_path());
    if !tensor_grep_rs::index_cache::confined_regular_file_exists(&index_path) {
        return (IndexRoutingState::default(), None);
    }

    match LoadedIndex::load_for_search(&index_path) {
        Ok(index) => {
            // M17 (audit-m17): the ROOT check runs FIRST, before any staleness work -- a
            // `.tg_index` reached from a DIFFERENT tree than it was built for must never be
            // warm-served (copied index, renamed tree, symlink alias). `is_stale` then also
            // covers the ordinary same-root staleness cases (H1d no_ignore mode flip,
            // per-file mtime/size, new files). A mismatch is disclosed via the same verbose
            // "[index]" channel as every other rebuild decision; routing then declines the
            // index (serving the query correctly through the fallback engine) and any actual
            // rebuild happens in `handle_index_search`, which repeats the check at its own
            // load sites.
            let root_reason = index.root_servability_reason(Path::new(request.primary_path()));
            if args.verbose {
                if let Some(reason) = &root_reason {
                    eprintln!("[index] refusing to serve cached index: {reason}");
                }
            }
            let is_stale = root_reason.is_some() || index.is_stale(args.no_ignore);
            (
                IndexRoutingState {
                    exists: true,
                    is_stale,
                    pattern_compatible: true,
                },
                Some(index),
            )
        }
        Err(_) => (
            IndexRoutingState {
                exists: true,
                is_stale: true,
                pattern_compatible: true,
            },
            None,
        ),
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn count_search_corpus_bytes(
    paths: &[PathBuf],
    no_ignore: bool,
    globs: &[String],
) -> anyhow::Result<u64> {
    let mut total_bytes = 0u64;
    let mut roots = Vec::new();

    for path in paths {
        if path.is_file() {
            total_bytes = total_bytes.saturating_add(fs::metadata(path)?.len());
        } else {
            roots.push(path.clone());
        }
    }

    if roots.is_empty() {
        return Ok(total_bytes);
    }

    let mut builder = WalkBuilder::new(&roots[0]);
    for root in roots.iter().skip(1) {
        builder.add(root);
    }

    if no_ignore {
        builder.ignore(false);
        builder.git_ignore(false);
        builder.git_global(false);
        builder.git_exclude(false);
        builder.parents(false);
    } else {
        for root in &roots {
            for ignore_name in [".ignore", ".gitignore", ".rgignore"] {
                let ignore_path = root.join(ignore_name);
                if ignore_path.is_file() {
                    builder.add_ignore(ignore_path);
                }
            }
        }
    }

    if !globs.is_empty() {
        let mut overrides = OverrideBuilder::new(&roots[0]);
        for glob in globs {
            overrides
                .add(glob)
                .with_context(|| format!("failed to add glob override '{glob}'"))?;
        }
        builder.overrides(
            overrides
                .build()
                .context("failed to build glob override matcher")?,
        );
    }

    for entry in builder.build() {
        let entry = entry?;
        if entry
            .file_type()
            .map(|kind| kind.is_file())
            .unwrap_or(false)
        {
            total_bytes = total_bytes.saturating_add(entry.metadata()?.len());
        }
    }

    Ok(total_bytes)
}

#[cfg(feature = "cuda")]
pub(crate) fn load_search_routing_calibration(
    search_root: &Path,
) -> Option<SearchRoutingCalibration> {
    let now = tensor_grep_rs::crossover::current_timestamp();
    match tensor_grep_rs::crossover::load_fresh_crossover_config(Some(search_root), now) {
        Ok(Some((_, config))) => Some(SearchRoutingCalibration {
            threshold_bytes: config.corpus_size_breakpoint_bytes,
            gpu_positive: config.recommendation != "cpu_always",
        }),
        Ok(None) | Err(_) => None,
    }
}

#[cfg(feature = "cuda")]
pub(crate) fn auto_gpu_available_for_routing() -> bool {
    if env::var("TG_TEST_CUDA_BEHAVIOR")
        .ok()
        .map(|value| value.trim().eq_ignore_ascii_case("no-devices"))
        .unwrap_or(false)
    {
        return false;
    }

    enumerate_cuda_devices()
        .map(|devices| !devices.is_empty())
        .unwrap_or(false)
}

pub(crate) fn positional_ripgrep_args(
    cli: &PositionalCli,
    pattern: &str,
    paths: &[String],
) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        files: false,
        json: false,
        ignore_case: cli.ignore_case,
        fixed_strings: cli.fixed_strings,
        no_fixed_strings: false,
        invert_match: cli.invert_match,
        no_invert_match: false,
        count: cli.count,
        count_matches: false,
        line_number: cli.line_number && !cli.no_line_number,
        no_line_number: cli.no_line_number,
        column: false,
        only_matching: cli.only_matching,
        context: None,
        before_context: None,
        after_context: None,
        max_count: cli.max_count,
        word_regexp: cli.word_regexp,
        smart_case: false,
        globs: Vec::new(),
        ignore: cli.ignore,
        no_ignore: cli.no_ignore,
        no_ignore_dot: false,
        no_ignore_exclude: false,
        no_ignore_files: false,
        no_ignore_global: false,
        no_ignore_parent: false,
        no_ignore_vcs: cli.no_ignore_vcs,
        require_git: cli.require_git,
        hidden: false,
        no_hidden: cli.no_hidden,
        follow: false,
        text: false,
        files_with_matches: false,
        files_without_match: false,
        file_types: Vec::new(),
        color: cli.color.clone(),
        path_separator: cli.path_separator.clone(),
        replace: cli.replace.clone(),
        vimgrep: cli.vimgrep,
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
        patterns: vec![pattern.to_string()],
        paths: paths.to_vec(),
        // `cli.path` is the RAW user-supplied PATH positionals before `implicit_search_paths`
        // substitutes stdin/"." -- empty means the caller gave no explicit path.
        path_was_implicit: cli.path.is_empty(),
        pcre2: cli.pcre2,
        no_pcre2: false,
        pcre2_unicode: cli.pcre2_unicode,
        no_pcre2_unicode: false,
        no_crlf: false,
        no_encoding: false,
        no_mmap: false,
        no_pre: false,
        no_search_zip: false,
        auto_hybrid_regex: cli.auto_hybrid_regex,
        no_auto_hybrid_regex: false,
        unicode: cli.unicode,
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
        messages: cli.messages,
        no_stats: false,
        max_filesize: cli.max_filesize.clone(),
    }
}

pub(crate) fn command_ripgrep_args(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
) -> RipgrepSearchArgs {
    RipgrepSearchArgs {
        files: false,
        json: args.json && args.format.as_deref() == Some("rg"),
        ignore_case: args.ignore_case,
        fixed_strings: args.fixed_strings,
        no_fixed_strings: false,
        invert_match: args.invert_match,
        no_invert_match: false,
        count: args.count,
        count_matches: args.count_matches,
        line_number: args.line_number && !args.no_line_number,
        no_line_number: args.no_line_number,
        column: args.column && !args.no_column,
        only_matching: args.only_matching,
        context: args.context,
        before_context: args.before_context,
        after_context: args.after_context,
        max_count: args.max_count,
        word_regexp: args.word_regexp,
        smart_case: args.smart_case,
        globs: args.globs.clone(),
        ignore: args.ignore,
        no_ignore: args.no_ignore,
        no_ignore_dot: args.no_ignore_dot,
        no_ignore_exclude: args.no_ignore_exclude,
        no_ignore_files: args.no_ignore_files,
        no_ignore_global: args.no_ignore_global,
        no_ignore_parent: args.no_ignore_parent,
        no_ignore_vcs: args.no_ignore_vcs,
        require_git: args.require_git,
        hidden: args.hidden,
        no_hidden: args.no_hidden,
        follow: args.follow,
        text: args.text,
        files_with_matches: args.files_with_matches,
        files_without_match: args.files_without_match,
        file_types: args.file_type.clone(),
        color: args.color.clone(),
        path_separator: args.path_separator.clone(),
        replace: args.replace.clone(),
        vimgrep: args.vimgrep,
        passthru: args.passthru,
        no_config: args.no_config,
        sort: args.sort.clone(),
        sort_reverse: args.sort_reverse.clone(),
        sort_files: args.sort_files,
        max_depth: args.max_depth,
        null: args.null,
        null_data: args.null_data,
        multiline: args.multiline,
        no_multiline: false,
        multiline_dotall: args.multiline_dotall,
        no_multiline_dotall: false,
        patterns: request.patterns.clone(),
        paths: if request.path_was_implicit {
            Vec::new()
        } else {
            request.paths.clone()
        },
        path_was_implicit: request.path_was_implicit,
        pcre2: args.pcre2,
        no_pcre2: false,
        pcre2_unicode: args.pcre2_unicode,
        no_pcre2_unicode: false,
        no_crlf: false,
        no_encoding: false,
        no_mmap: false,
        no_pre: false,
        no_search_zip: false,
        auto_hybrid_regex: args.auto_hybrid_regex,
        no_auto_hybrid_regex: false,
        unicode: args.unicode,
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
        no_column: args.no_column,
        no_context_separator: false,
        no_include_zero: false,
        no_line_buffered: false,
        no_max_columns_preview: false,
        no_trim: false,
        no_json: false,
        messages: args.messages,
        no_stats: false,
        max_filesize: args.max_filesize.clone(),
    }
}

pub(crate) fn search_requires_ripgrep_passthrough(args: &SearchArgs) -> bool {
    (args.json && args.format.as_deref() == Some("rg"))
        || (!args.json
            && !args.ndjson
            && (args.count_matches
                || args.column
                || args.no_column
                || args.smart_case
                || args.hidden
                || args.follow
                || args.text
                || args.passthru
                || args.no_config
                || args.auto_hybrid_regex
                || args.pcre2_unicode
                || args.ignore
                || args.messages
                || args.require_git
                || args.no_hidden
                || args.path_separator.is_some()
                || args.vimgrep
                || args.no_ignore_dot
                || args.no_ignore_exclude
                || args.no_ignore_files
                || args.no_ignore_global
                || args.no_ignore_parent
                || args.files_with_matches
                || args.files_without_match
                || args.sort.is_some()
                || args.sort_reverse.is_some()
                || args.sort_files
                || args.max_depth.is_some()
                || args.null
                || args.null_data
                || args.multiline
                || args.multiline_dotall
                || !args.file_type.is_empty()))
}

/// P5·H2 (Backend Fail-Closed Contract): `search_requires_ripgrep_passthrough`'s whole hard-flag
/// list is gated behind `!args.json && !args.ndjson`, so on the STRUCTURED routes
/// (`--json`/`--ndjson`) the count/files flags fall through to the native engine -- and neither
/// `NativeSearchConfig` nor `GpuSearchParams` has a field for them
/// (`native_search_config_for_command` maps `count`/`only_matching`, not these), so they were
/// SILENTLY DROPPED (exit 0, wrong output; verified live on the shipped native front door:
/// `tg search ... --json --count-matches` prints a match list, not occurrence counts). This
/// predicate returns every such flag whose only ride to the native engine on this request would
/// be that drop -- the HARD-REFUSAL set. `--format rg --json` is DELIBERATELY excluded: that is
/// an rg PASSTHROUGH (`search_requires_ripgrep_passthrough` returns true), and
/// `command_ripgrep_args` threads all three flags into rg's own argv, so that route is honored,
/// not dropped -- refusing it would break working behavior.
/// File-set flags that native structured output cannot preserve are included in the same refusal.
pub(crate) fn native_structured_dropped_search_flags(args: &SearchArgs) -> Vec<&'static str> {
    let native_structured = args.ndjson || (args.json && args.format.as_deref() != Some("rg"));
    if !native_structured {
        return Vec::new();
    }
    let mut dropped = Vec::new();
    if args.count_matches {
        dropped.push("--count-matches");
    }
    if args.files_with_matches {
        dropped.push("--files-with-matches");
    }
    if args.files_without_match {
        dropped.push("--files-without-match");
    }
    if !args.file_type.is_empty() {
        dropped.push("--type");
    }
    if args.follow {
        dropped.push("--follow");
    }
    if args.max_filesize.is_some() {
        dropped.push("--max-filesize");
    }
    dropped
}

/// P5·H2 positional twin. `run_positional_cli` has NO `search_requires_ripgrep_passthrough`-
/// equivalent gate at all, so `--count-matches` reaches the native engine through the `--json`/
/// `--ndjson` structured doors AND, unconditionally (no json/ndjson needed), the explicit
/// `--gpu-device-ids` door -- and `native_search_config_for_positional` maps `count`, not
/// `count_matches`. A NON--json/--ndjson/--gpu-device-ids positional `--count-matches` routes to
/// `BackendSelection::Ripgrep` instead; it is NOT refused here -- the Python front door excludes
/// it from native dispatch (bootstrap fast-path unsupported set + `count_matches` in
/// `_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS`), so the published `tg PAT --count-matches`
/// path is served by Python->rg and stays honored (the already-fine case locked by
/// `test_cli_bootstrap.py::test_rust_first_count_matches_refuses_via_native_self_guard`).
pub(crate) fn positional_native_dropped_search_flags(cli: &PositionalCli) -> Vec<&'static str> {
    if !cli.count_matches {
        return Vec::new();
    }
    if cli.json || cli.ndjson || !cli.gpu_device_ids.is_empty() {
        vec!["--count-matches"]
    } else {
        Vec::new()
    }
}

/// Fail closed (exit 2, mirrors `exit_gpu_cpu_fallback_flag_unhonorable`) when a request the
/// native engine is about to serve carries a flag that engine silently drops. Names every dropped
/// flag and the remedy. The `structured` half makes the remedy text honest: only a
/// `--json`/`--ndjson` route can be redirected to `tg search ... --format rg --json` (rg's raw
/// passthrough output, described as raw paths/counts -- not "JSON Lines", which this native
/// refusal never emits); a bare `--gpu-device-ids` door gets the drop-the-flag/route-to-rg
/// remedy. Positional users have no `--format` flag, so the structured remedy always points at
/// the `tg search` command (which does have one) or at dropping the flags -- both work for both
/// front doors.
pub(crate) fn exit_native_structured_flag_dropped(flag_names: &[&'static str], structured: bool) {
    let flags = flag_names.join(", ");
    if structured {
        eprintln!(
            "error: {flags} is a raw path/count output mode that native structured \
             --json/--ndjson search output cannot express and would be silently dropped; \
             refusing rather than silently ignoring it. Drop --json/--ndjson (or {flags}), or \
             rerun as `tg search ... --format rg --json` to request ripgrep's raw passthrough \
             output, which carries {flags}."
        );
    } else {
        eprintln!(
            "error: {flags} is not supported by native GPU/CPU search output and would be \
             silently dropped; refusing rather than silently ignoring it. Drop {flags}, or drop \
             --gpu-device-ids/--json/--ndjson so the search is routed to the ripgrep backend, \
             which carries {flags}."
        );
    }
    std::process::exit(2);
}

/// Native-routing entrypoint gate for the `tg search` command (P5·H2). Pure: returns `Some(flags)`
/// when this request carries flags the native structured engine would silently drop (the caller
/// maps it to `exit_native_structured_flag_dropped`; keeping it pure makes it unit-testable
/// without the `exit(2)` killing the test binary -- see
/// `native_structured_refusal_validator_returns_refusal_set`). The `--format rg --json` passthrough
/// and the trigram index (which has its own `IndexFlagPolicy::Refuse` for these flags) never reach
/// it.
pub(crate) fn validate_search_native_structured_refusals(
    args: &SearchArgs,
) -> Option<Vec<&'static str>> {
    let dropped = native_structured_dropped_search_flags(args);
    if dropped.is_empty() {
        None
    } else {
        Some(dropped)
    }
}

/// Native-routing entrypoint gate for the positional CLI (P5·H2), same pure contract. The exit
/// wrapper is applied at the call site so both the `--json`/`--ndjson` doors and the
/// unconditional `--gpu-device-ids` door refuse identically.
pub(crate) fn validate_positional_native_structured_refusals(
    cli: &PositionalCli,
) -> Option<Vec<&'static str>> {
    let dropped = positional_native_dropped_search_flags(cli);
    if dropped.is_empty() {
        None
    } else {
        Some(dropped)
    }
}

/// P5·H2 extension (audit/h2 follow-up): the rg-passthrough route carries the count/files flags
/// (`search_requires_ripgrep_passthrough`'s hard-flag list; `command_ripgrep_args` threads them
/// into rg's argv) but `RipgrepSearchArgs` has NO `--gpu-device-ids` field (zero gpu refs), so a
/// gpu + count/files request silently drops the explicit GPU request (wrong output, exit 0). The
/// front-door rewrite makes this a SEARCH-FORM class, not just the positional one:
/// `SEARCH_OPTION_FIRST_FLAGS` includes `--count-matches`, so `tg PAT . --gpu-device-ids 0
/// --count-matches` normalizes into `tg search PAT . --gpu-device-ids 0 --count-matches` and
/// never reaches `validate_positional_native_structured_refusals`. This predicate returns every
/// count/files flag whose rg-passthrough would drop the GPU request; empty when no explicit GPU
/// ids are present (pure `--count-matches`/`-l` keep their HONORED rg passthrough).
/// P5·H2 "never silently drop" (Backend Fail-Closed Contract). Pure so the call site can map it
/// to the exit wrapper while in-process tests drive it directly.
pub(crate) fn rg_passthrough_gpu_dropped_search_flags(args: &SearchArgs) -> Vec<&'static str> {
    if args.gpu_device_ids.is_empty() {
        return Vec::new();
    }
    let mut dropped = Vec::new();
    if args.count_matches {
        dropped.push("--count-matches");
    }
    if args.files_with_matches {
        dropped.push("--files-with-matches");
    }
    if args.files_without_match {
        dropped.push("--files-without-match");
    }
    dropped
}

/// Fail closed (exit 2) when the rg-passthrough route would silently drop an explicit
/// `--gpu-device-ids` request (`command_ripgrep_args` has no GPU field). Names the combined
/// flags and the remedy; message style mirrors `exit_native_structured_flag_dropped`'s
/// non-structured arm.
pub(crate) fn exit_gpu_dropped_on_rg_passthrough(flag_names: &[&'static str]) {
    let flags = flag_names.join(", ");
    eprintln!(
        "error: --gpu-device-ids combined with {flags} cannot be honored: the ripgrep passthrough \
         that carries {flags} has no GPU field, so the explicit GPU request would be silently \
         dropped; refusing rather than silently ignoring it. Drop --gpu-device-ids, or drop \
         {flags}."
    );
    std::process::exit(2);
}

pub(crate) fn search_prefers_ripgrep_passthrough(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    rg_available: bool,
) -> bool {
    if search_requires_ripgrep_passthrough(args) {
        return true;
    }
    if args.json
        || args.ndjson
        || args.index
        || args.force_cpu
        || !args.gpu_device_ids.is_empty()
        || detect_warm_index_state(args, request).0.exists
    {
        return false;
    }
    rg_available
        && (args.count
            || args.no_ignore
            || args.no_ignore_vcs
            || !args.globs.is_empty()
            || (args.fixed_strings && request.patterns.len() > 1))
}

pub(crate) fn search_has_context(args: &SearchArgs) -> bool {
    args.context.is_some() || args.before_context.is_some() || args.after_context.is_some()
}

pub(crate) fn search_before_context(args: &SearchArgs) -> usize {
    args.before_context.or(args.context).unwrap_or(0)
}

pub(crate) fn search_after_context(args: &SearchArgs) -> usize {
    args.after_context.or(args.context).unwrap_or(0)
}

pub(crate) fn search_effective_context(args: &SearchArgs) -> Option<usize> {
    args.context
        .or_else(|| match (args.before_context, args.after_context) {
            (Some(before), Some(after)) => Some(before.max(after)),
            (Some(before), None) => Some(before),
            (None, Some(after)) => Some(after),
            (None, None) => None,
        })
}

pub(crate) fn native_search_config_for_positional(
    cli: &PositionalCli,
    pattern: &str,
    paths: &[String],
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: paths.iter().map(PathBuf::from).collect(),
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: Vec::new(),
        ignore_case: cli.ignore_case,
        smart_case: false,
        fixed_strings: cli.fixed_strings,
        word_boundary: cli.word_regexp,
        invert_match: cli.invert_match,
        count: cli.count,
        max_count: cli.max_count.map(|value| value as u64),
        no_ignore: cli.no_ignore,
        json: cli.json,
        ndjson: cli.ndjson,
        verbose: cli.verbose,
        text: false,
        line_number: cli.line_number && !cli.no_line_number,
        only_matching: cli.only_matching,
        replace: cli.replace.clone(),
        // `cli.path` is the RAW user-supplied PATH positionals before `implicit_search_paths`
        // substitutes stdin/"." -- empty means the caller gave no explicit path (audit #105,
        // mirrors `positional_ripgrep_args`'s `path_was_implicit: cli.path.is_empty()`).
        path_was_implicit: cli.path.is_empty(),
        ..NativeSearchConfig::default()
    }
}

pub(crate) fn native_search_config_for_command(
    args: &SearchArgs,
    pattern: &str,
    paths: &[String],
    path_was_implicit: bool,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: paths.iter().map(PathBuf::from).collect(),
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: Vec::new(),
        ignore_case: args.ignore_case,
        smart_case: args.smart_case,
        fixed_strings: args.fixed_strings,
        word_boundary: args.word_regexp,
        invert_match: args.invert_match,
        before_context: search_before_context(args),
        after_context: search_after_context(args),
        max_count: args.max_count.map(|value| value as u64),
        glob: args.globs.clone(),
        hidden: args.hidden,
        max_depth: args.max_depth,
        count: args.count,
        no_ignore: args.no_ignore,
        // Task #267: without this, `--json`/`--ndjson` (this function's `structured_output`
        // callers) silently dropped `--no-ignore-vcs` -- `build_walk_builder` had no field to
        // read it from -- while the identical flag on the non-structured-output route (real
        // `rg` via `command_ripgrep_args`/`root_ignore_file_args`) honored it correctly. An
        // output-format flag must never change the file set.
        no_ignore_vcs: args.no_ignore_vcs,
        json: args.json,
        ndjson: args.ndjson,
        verbose: args.verbose,
        text: args.text,
        line_number: args.line_number && !args.no_line_number,
        only_matching: args.only_matching,
        replace: args.replace.clone(),
        // Audit #105: threaded from `ResolvedSearchRequest::path_was_implicit` at every call
        // site (mirrors `command_ripgrep_args`'s `path_was_implicit: request.path_was_implicit`)
        // so this engine's own implicit-walk-ceiling gate (`native_search::
        // check_native_implicit_walk_ceiling`) can fire for `--json`/`--force-cpu`/single-pattern
        // `--fixed-strings`/rg-unavailable routing, none of which pass through
        // `execute_ripgrep_search`'s #100 gate.
        path_was_implicit,
        ..NativeSearchConfig::default()
    }
}

pub(crate) fn native_search_config_for_gpu_params(
    params: &GpuSearchParams<'_>,
    pattern: &str,
    decision: RoutingDecision,
) -> NativeSearchConfig {
    NativeSearchConfig {
        pattern: pattern.to_string(),
        paths: vec![PathBuf::from(params.path)],
        routing_backend: decision.routing_backend(),
        routing_reason: decision.reason,
        sidecar_used: decision.sidecar_used(),
        requested_gpu_device_ids: params.gpu_device_ids.to_vec(),
        ignore_case: params.ignore_case,
        smart_case: params.smart_case,
        fixed_strings: params.fixed_strings,
        word_boundary: params.word_regexp,
        invert_match: params.invert_match,
        before_context: params.context.unwrap_or(0),
        after_context: params.context.unwrap_or(0),
        max_count: params.max_count.map(|value| value as u64),
        glob: params.globs.clone(),
        hidden: params.hidden,
        max_depth: params.max_depth,
        count: params.count,
        no_ignore: params.no_ignore,
        // Task #267: same gap as `native_search_config_for_command` -- this is the
        // explicit-`--gpu-device-ids`-fallback-to-CPU route, which already carries
        // `params.no_ignore_vcs` (used by the GPU engine itself) but was never threading it
        // into the CPU-fallback `NativeSearchConfig`, silently dropping `--no-ignore-vcs` on
        // this route the same way.
        no_ignore_vcs: params.no_ignore_vcs,
        json: params.json,
        ndjson: params.ndjson,
        verbose: params.verbose,
        text: params.text,
        line_number: params.line_number,
        // Task #131 F3: `NativeSearchConfig` already carries `only_matching`/`replace` (see the
        // sibling `native_search_config_for_command`/`native_search_config_for_positional`, which
        // set them from `args`/`cli` directly) -- this mapper simply never copied them over, so
        // the GPU CPU-fallback route silently dropped `-o`/`--replace` even though the CPU engine
        // it delegates to is fully capable of honoring them.
        only_matching: params.only_matching,
        replace: params.replace.clone(),
        // Audit #105: threaded from `GpuSearchParams::path_was_implicit` (see that field's doc
        // comment -- this is the explicit-`--gpu-device-ids`-fallback-to-CPU route, which used to
        // have no way to know whether the PATH was implicit at all).
        path_was_implicit: params.path_was_implicit,
        ..NativeSearchConfig::default()
    }
}
