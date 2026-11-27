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

pub(crate) fn execute_native_search(config: NativeSearchConfig) -> anyhow::Result<SearchStats> {
    if let Ok(message) = env::var("TG_TEST_NATIVE_SEARCH_FORCE_ERROR") {
        anyhow::bail!(message);
    }

    run_native_search(config)
}

/// Mirrors `ResolvedSearchRequest::path_display`'s join-with-space convention (this module, used
/// by the SearchArgs/positional-CLI request types) so a refusal's `path` field matches what a
/// SUCCESSFUL result on the same invocation would have shown, rather than inventing a second
/// "path" convention just for this one envelope.
pub(crate) fn native_search_config_path_display(paths: &[PathBuf]) -> String {
    if paths.is_empty() {
        ".".to_string()
    } else {
        paths
            .iter()
            .map(|path| path.to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// Task #17 (2026-07-30): pure envelope builder, kept separate from the `println!` wrapper below
/// so it can be unit-tested without needing to capture process stdout or trigger
/// `std::process::exit`. Mirrors the Python CLI's `_emit_broad_scan_refusal` JSON payload
/// (`cli/main.py`) field for field -- `version`, `path`, `total_matches: 0`, `total_files: 0`,
/// `matches: []`, `truncated: true`, `result_incomplete: true`, `incomplete_reason`,
/// `incomplete_reason_class: "scan_limit"`, and an `error` object carrying `code:
/// "broad_scan_refused"`, `message`, `retryable: false`. Two front doors refusing the same thing
/// in two different shapes is exactly the drift AGENTS.md warns about, so this is not a new
/// vocabulary -- it is the existing one, reproduced.
pub(crate) fn broad_scan_refusal_json_envelope(path: &str, message: &str) -> serde_json::Value {
    serde_json::json!({
        "version": JSON_OUTPUT_VERSION,
        "path": path,
        "total_matches": 0,
        "total_files": 0,
        "matches": [],
        "truncated": true,
        "result_incomplete": true,
        "incomplete_reason": message,
        "incomplete_reason_class": "scan_limit",
        "error": {
            "code": "broad_scan_refused",
            "message": message,
            "retryable": false,
        },
    })
}

/// Task #17: emits the specific broad-scan envelope before the generic JSON runtime-error handler
/// can classify the same refusal as `backend_error`. Never prints to stderr; the caller preserves
/// the existing refusal line.
pub(crate) fn emit_broad_scan_refusal_json_if_needed(json: bool, path: &str, err: &anyhow::Error) {
    if !json {
        return;
    }
    let message = err.to_string();
    if !is_unbounded_implicit_search_walk_refusal(&message) {
        return;
    }
    println!("{}", broad_scan_refusal_json_envelope(path, &message));
}

/// Audit #105: `collect_native_multi_pattern_matches`'s two fallible native-search calls (the
/// AhoCorasick fast path and the per-pattern regex loop below) both funnel any `Err` through
/// this helper instead of a bare `?`. Every one of this function's 4 call sites (the single- and
/// multi-`-e` `tg search` routes, and the two GPU-explicit-`--gpu-device-ids` CPU-fallback
/// routes) would otherwise let an implicit-walk-ceiling refusal `Err` propagate all the way to
/// `main()`'s default `Result` termination, which exits 1 -- the "exit-1-vs-exit-2 no-match
/// ambiguity bug" (audit #81 #7) -- instead of the fast-bounded exit-2 refusal every other
/// native-CPU route already gets via `run_native_search_with_optional_rg_fallback`'s generic Err
/// handling. Deliberately mirrors `execute_ripgrep_search`'s OWN refusal (rg_passthrough.rs) on
/// stderr: the exact same plain `eprintln!` text, byte-for-byte, regardless of which internal
/// engine produced it -- that half of the symmetry is untouched and must stay untouched.
///
/// Task #17 (2026-07-30), reversing the second half of this comment's old claim: this used to end
/// "never a structured JSON error object, even under `--json`" -- coherent only while NEITHER
/// front door emitted one. #851 gave the Python CLI's own emitter (`_emit_broad_scan_refusal`,
/// cli/main.py) a machine-readable `error.code: "broad_scan_refused"` envelope on stdout for this
/// exact refusal family, which broke the symmetry this rationale protected: pip users got the
/// envelope, standalone-binary/Homebrew/winget users still got 0 stdout bytes under `--json`. This
/// helper (and its single-pattern sibling in `run_native_search_with_optional_rg_fallback`) now
/// ALSO emits that same envelope, field-for-field, via `emit_broad_scan_refusal_json_if_needed`,
/// while leaving the stderr line completely unchanged. Any OTHER native-search error (bad path,
/// bad pattern, ...) is returned completely unchanged; this must not alter exit-code or `--json`
/// behavior for pre-existing error kinds.
pub(crate) fn exit_on_native_multi_pattern_ceiling_refusal(
    err: anyhow::Error,
    json: bool,
    path: &str,
) -> anyhow::Error {
    if !is_unbounded_implicit_search_walk_refusal(&err.to_string()) {
        return err;
    }
    eprintln!("{err}");
    emit_broad_scan_refusal_json_if_needed(json, path, &err);
    std::process::exit(2);
}

pub(crate) struct NativeSearchOutputOptions<'a> {
    pub(crate) decision: RoutingDecision,
    pub(crate) query: &'a str,
    pub(crate) path: &'a str,
    pub(crate) requested_gpu_device_ids: &'a [i32],
    pub(crate) json: bool,
    pub(crate) ndjson: bool,
    pub(crate) count: bool,
    pub(crate) line_number: bool,
    /// Task #26. Threaded from `ResolvedSearchRequest::path_was_implicit` at every construction
    /// site -- the same signal the broad-scan probe already gates on, reused rather than
    /// re-derived, so the two cannot disagree about whether the caller chose the scope.
    pub(crate) path_was_implicit: bool,
}

pub(crate) fn emit_multi_pattern_native_results(
    options: NativeSearchOutputOptions<'_>,
    matches: Vec<SearchMatchJson>,
    incomplete_paths: Option<usize>,
    binary_matches: usize,
) -> anyhow::Result<()> {
    let has_matches = !matches.is_empty() || (options.json || options.ndjson) && binary_matches > 0;
    if options.json {
        emit_json_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
            incomplete_paths,
            options.path_was_implicit,
            (binary_matches > 0).then_some(binary_matches),
            None,
        )?;
    } else if options.ndjson {
        emit_ndjson_search_results(
            options.decision,
            options.query,
            options.path,
            options.requested_gpu_device_ids,
            matches,
            incomplete_paths,
            options.path_was_implicit,
            (binary_matches > 0).then_some(binary_matches),
        )?;
    } else if options.count {
        emit_count_search_matches(options.path, &matches)?;
    } else {
        emit_plain_search_matches_with_line_number(options.path, &matches, options.line_number)?;
    }

    // Task 276 task 6 (exit-code parity). Checked BEFORE the no-match branch, in the SAME order
    // the single-pattern native route already resolves these two questions in
    // `run_native_search_with_optional_rg_fallback`. "I could not finish looking" outranks "I
    // found nothing", because the second is only trustworthy if the first is false.
    //
    // Until this guard, every one of this function's four callers passed a REAL walk-error count
    // (each one reads it straight out of `collect_native_multi_pattern_matches`), so the envelope
    // said `result_incomplete: true` and the process then exited 0 -- or, on a zero-match
    // incomplete scan, fell into the branch below and exited 1, which reads as an authoritative
    // "no matches exist". That is the exact lie #276 exists to stop, in the twin of the route
    // whose comment says the ordering exists to prevent it.
    if walk_was_incomplete(incomplete_paths) {
        std::process::exit(2);
    }
    if !has_matches {
        std::process::exit(1);
    }

    Ok(())
}

/// Exit code for a consumer-closed pipe, measured against rg for THE SHAPE THIS ROUTE ADMITS.
///
/// The mechanism matters, because an earlier revision of this comment stated it wrongly ("1 is
/// specifically rg's broken-pipe code"). It is not. rg's actual broken-pipe path in `main()`
/// returns **0**: it walks `err.chain()` for `BrokenPipe` and exits successfully. The 1 observed
/// here is a different thing entirely -- for a SINGLE-FILE search rg breaks out of its loop before
/// recording a match, so `matched` stays false and it reports its ordinary "no match" code.
/// Receipts that separate the two: `rg -c needle dense.txt` early-close -> rc=0 (5/5), and
/// `rg needle m1.txt m2.txt` early-close -> rc=0 (5/5).
///
/// So this constant is SHAPE-BOUND. It is correct for the currently-admitted subset -- exactly one
/// explicit regular file, no `-c` -- and would be WRONG the moment that subset widened to multiple
/// paths or `--count`. Anyone widening `native_can_serve_plain_text` must revisit it.
/// `test_early_closing_consumer_matches_ripgrep` asserts PARITY WITH RG rather than this constant,
/// so a widening (or a platform whose rg differs) fails loudly instead of silently inheriting the
/// wrong code.
pub(crate) const BROKEN_PIPE_EXIT_CODE: i32 = 1;

/// Does any link in this error chain carry `ErrorKind::BrokenPipe`?
///
/// The kind is PRESERVED at the source: `native_search::sink_io_error` copies the original
/// `ErrorKind` when converting to the `io::Error` that `grep_searcher::Sink::Error` requires, and
/// `grep_searcher` propagates that error unwrapped, so the typed walk below finds `BrokenPipe`
/// intact. Every link is still checked rather than just the outermost, because the chain also
/// carries the anyhow context `search_path`'s caller attaches.
///
/// (An earlier revision of this comment described the opposite mechanism -- that
/// `io::Error::other` flattened the kind to `Other` and only the innermost link still said
/// `BrokenPipe`. That was true of the code at the time and became false when `sink_io_error`
/// replaced those 13 call sites; it is corrected here rather than left to contradict the code and
/// the comment nine lines below it.)
pub(crate) fn run_native_search_with_optional_rg_fallback(
    config: NativeSearchConfig,
    rg_fallback: Option<RipgrepSearchArgs>,
) -> anyhow::Result<()> {
    let json = config.json;
    let ndjson = config.ndjson;
    let verbose = config.verbose;
    // Task #17: captured before `config` moves into `execute_native_search` below -- this is the
    // shared chokepoint for BOTH single-pattern front doors (`tg search PATTERN` and the bare
    // positional `tg PATTERN`), so a ceiling refusal reached through either one needs a `path`
    // for the same `--json` envelope `exit_on_native_multi_pattern_ceiling_refusal` emits for the
    // multi-`-e` route.
    let refusal_path = native_search_config_path_display(&config.paths);
    match execute_native_search(config) {
        Ok(stats) => {
            // Task #276 slice C. An incomplete walk exits 2 -- and it is checked BEFORE the
            // no-match branch on purpose, because the two answer different questions and rg
            // resolves them in this order too. `rg needle .` over a tree with one access-denied
            // subdirectory exits 2 whether or not it matched anything elsewhere: "I could not
            // finish looking" outranks "I found nothing", since the second is only trustworthy
            // if the first is false. Reversing these would let a zero-match incomplete scan exit
            // 1, which reads as an authoritative "no matches exist" -- the exact lie #276 exists
            // to stop.
            //
            // 🔴 MERGE PRECONDITION -- NOT satisfied history. PRs #792 and #793 make the six
            // exit-code consumers three-state aware (agent_readiness x3, both benchmark
            // harnesses, the byte-fidelity e2e, mcp_server; crossover.rs verified unreachable).
            // AS OF THIS COMMIT BOTH ARE STILL OPEN, and `origin/main` still has
            // `agent_readiness.py` rejecting any exit not in {0,1}. THIS BRANCH MUST NOT MERGE
            // BEFORE THEM: doing so breaks `tg calibrate` (tg spawns itself and bails on
            // non-zero) and `tg dogfood` (a shipped command), and reds windows-agent-readiness.
            //
            // An earlier revision of this comment stated C0 "landed first" as completed fact.
            // It had not. A comment asserting a merge that never happened is worse than no
            // comment -- it is what the next reader trusts instead of re-checking.
            if stats.walk_errors > 0 {
                std::process::exit(2);
            }
            if stats.total_matches == 0 && stats.binary_match_files == 0 {
                std::process::exit(1);
            }
            Ok(())
        }
        Err(err) => {
            // A BROKEN PIPE is the consumer terminating normally (`tg ... | head -1`, `| less`, an
            // agent that reads N lines and stops), NOT a search failure. It only became reachable
            // for plain text because this route moves ownership of the write loop out of an
            // `Stdio::inherit()` subprocess and into this process: rg absorbs EPIPE itself, while
            // the native sink surfaces it as an error. Without this guard the fallback branch
            // below would print the `warning: native CPU search failed...` line that
            // `test_native_plain_text_route_emits_no_extra_stderr` swears never leaks, and then
            // RE-RUN the entire search into an already-closed pipe.
            //
            // Measured on the shipped v1.98.3 + rg 15.1.0 (Windows), 279 KB dense fixture,
            // consumer `readline(); close()`, 3/3 runs stable, with a full-drain control showing
            // rg 0 / native 0 (i.e. no divergence when nothing closes early):
            //     rg           -> rc=1, stderr ''
            //     tg (native)  -> rc=2, stderr 'native standard output search failed for <path>'
            // Checked BEFORE the structured-error and fallback branches: once the consumer is
            // gone, emitting a JSON error or re-running the search is pointless on every route,
            // not just this one.
            if error_chain_has_broken_pipe(&err) {
                std::process::exit(BROKEN_PIPE_EXIT_CODE);
            }
            let err = if json && !ndjson {
                exit_on_native_multi_pattern_ceiling_refusal(err, json, &refusal_path)
            } else {
                err
            };
            exit_json_search_runtime_error_if_needed(json, ndjson, &err);
            if let Some(rg_args) = rg_fallback
                .filter(|_| !tensor_grep_rs::native_search::is_native_search_open_failure(&err))
            {
                eprintln!("warning: native CPU search failed, falling back to ripgrep: {err}");
                if !json && !ndjson && verbose {
                    emit_verbose_metadata(RoutingDecision::ripgrep());
                }
                let exit_code = execute_ripgrep_search(&rg_args)?;
                if exit_code != 0 {
                    std::process::exit(exit_code.max(1));
                }
                return Ok(());
            }

            eprintln!("{err}");
            std::process::exit(2);
        }
    }
}

pub(crate) fn handle_ripgrep_search(args: SearchArgs) -> anyhow::Result<()> {
    if args.version {
        version_info::print_version(args.verbose);
        return Ok(());
    }
    if args.pcre2_version {
        require_ripgrep_or_exit(ripgrep_is_available(), "--pcre2-version");
        let exit_code = execute_ripgrep_pcre2_version()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if args.type_list {
        require_ripgrep_or_exit(ripgrep_is_available(), "--type-list");
        let exit_code = execute_ripgrep_type_list()?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }
    if args.files && search_files::supports_native_files_search(&args) {
        return search_files::run_native_files_search(&args);
    }

    let request = resolve_search_request(&args)?;
    exit_json_search_input_error_if_needed(
        args.json,
        args.ndjson,
        &request.patterns,
        &request.paths,
    );
    refuse_structured_pcre2(
        args.pcre2,
        args.json,
        args.ndjson,
        args.format.as_deref() == Some("rg"),
    );
    let query = request.query_display();
    let path_display = request.path_display();
    let rg_available = ripgrep_is_available();
    #[cfg_attr(not(feature = "cuda"), allow(unused_variables))]
    let structured_output = args.json || args.ndjson;
    let auto_gpu_ids: [i32; 0] = [];

    // Fail closed instead of silently swapping --pcre2 to the native regex engine (which does not
    // support PCRE2 syntax) when no rg is present. Checked before route_search, whose `config.pcre2
    // && config.rg_available` gate otherwise falls through to NativeCpu/"rg_unavailable" with no
    // signal that PCRE2 semantics were dropped (audit #81 #9).
    if args.pcre2 {
        require_ripgrep_or_exit(rg_available, "--pcre2");
    }

    if search_args_need_broad_generated_guard(&args)
        || !search_args_have_generated_scan_bound(&args)
    {
        let generated_dirs = generated_scan_dir_names(&request.paths, false);
        if !generated_dirs.is_empty() {
            let msg = format_broad_generated_scan_error(&generated_dirs);
            exit_structured_search_error_if_needed(
                args.json,
                args.ndjson,
                "broad_generated_scan_refused",
                &msg,
            );
        }
    }

    // P5·H2 extension (audit/h2 follow-up): refuse gpu + count/files combos on the SEARCH form
    // BEFORE the rg-passthrough early return, so this gate fires in EVERY environment.
    // `search_requires_ripgrep_passthrough`'s hard-flag list diverts all three flags to
    // `command_ripgrep_args`, which threads them into rg's own argv but has NO
    // `--gpu-device-ids` field -- with rg present the combo silently dropped the explicit GPU
    // request (exit 0, the defect), and with rg absent the passthrough block's own
    // `require_ripgrep_or_exit` exited 2 with the generic "requires the ripgrep (`rg`) backend"
    // wording. Airtight-ordering argument: this statement sits BEFORE that block, so for these
    // combos it is the first gate to fire in BOTH environments and the "refusing" message below
    // is deterministic -- CI's rg-absent test-rust-core lanes NEVER reach the rg-required gate
    // for them. The gate also closes the front-door-rewrite shadow: `SEARCH_OPTION_FIRST_FLAGS`
    // includes `--count-matches`, so the positional `tg PAT . --gpu-device-ids 0 --count-matches`
    // normalizes into `tg search ...` and never reaches `run_positional_cli`'s validator.
    // Positional behavior is otherwise UNCHANGED by this commit: `--files-without-match` /
    // `--files-with-matches` long forms are in neither rewrite list and `PositionalCli` has no
    // `-l` spelling, so those positional file-flag routes stay exactly as pre-existing P5·H2 left
    // them (the positional validator refuses `--count-matches` only); they are a separate,
    // unchanged boundary, not silently claimed as covered here. `!args.index` preserves the
    // explicit-index path's OWN `IndexFlagPolicy::Refuse` message for these flags (mirrors the
    // structured validator's carve-out below). Pure `--count-matches`/`-l` without
    // `--gpu-device-ids` stays on its honored rg passthrough (the predicate returns empty).
    if !args.index {
        let dropped = rg_passthrough_gpu_dropped_search_flags(&args);
        if !dropped.is_empty() {
            exit_gpu_dropped_on_rg_passthrough(&dropped);
        }
    }

    if search_prefers_ripgrep_passthrough(&args, &request, rg_available) {
        // search_requires_ripgrep_passthrough (checked first inside the call above) can return
        // true regardless of rg_available -- e.g. --max-depth with TG_DISABLE_RG=1. Without this
        // guard execute_ripgrep_search's Err bubbles via `?` to main()'s default Result
        // termination, which exits 1 -- indistinguishable from a genuine no-match (audit #81 #7).
        require_ripgrep_or_exit(rg_available, "this search's flag combination");
        // execute_ripgrep_search enforces the implicit-path ceiling before resolving
        // ripgrep; command_ripgrep_args preserves path_was_implicit. Native CPU walks
        // enforce the same ceiling through check_native_implicit_walk_ceiling.
        if args.verbose {
            emit_verbose_metadata(RoutingDecision::ripgrep());
        }
        let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args, &request))?;
        if exit_code != 0 {
            std::process::exit(exit_code.max(1));
        }
        return Ok(());
    }

    // P5·H2 (audit Finding 2 hoist): refuse the structured-native count/files combos HERE, before
    // `count_search_corpus_bytes` walks the whole tree on CUDA builds (an invalid request used to
    // traverse every byte before the in-arm refusal, later in this match, exited). The predicate
    // returns empty for every HONORED route (non-JSON count/files go to rg passthrough above;
    // `--format rg --json`'s passthrough is caught by `search_requires_ripgrep_passthrough`), so
    // hoisting changes no honored path. `!args.index` keeps the explicit-index path's OWN refusal:
    // `route_search` routes `index == true` to `TrigramIndex` unconditionally, where
    // `handle_index_search`'s `IndexFlagPolicy::Refuse` for count/files (message pinned) fires --
    // reaching past that here would shadow its message. The in-arm calls this replaces were the
    // ORIGINAL landing spot and are removed as redundant; the wiring is covered end-to-end by
    // `rust_core/tests/test_h2_native_structured_refusal.rs` (CARGO_BIN_EXE_tg: each refused
    // combo must exit 2) and in-process by `native_structured_refusal_validator_returns_refusal_set`.
    if !args.index {
        if let Some(dropped) = validate_search_native_structured_refusals(&args) {
            exit_native_structured_flag_dropped(&dropped, args.json || args.ndjson);
        }
    }

    if request.paths.len() != 1 && !args.gpu_device_ids.is_empty() {
        anyhow::bail!("GPU search currently supports exactly one path root");
    }

    #[cfg(feature = "cuda")]
    let (corpus_bytes, corpus_bytes_known) =
        match count_search_corpus_bytes(&request.path_bufs(), args.no_ignore, &args.globs) {
            Ok(bytes) => (bytes, true),
            Err(err) => {
                eprintln!("warning: corpus size probe failed: {err}");
                (0, false)
            }
        };
    #[cfg(not(feature = "cuda"))]
    let (corpus_bytes, corpus_bytes_known) = (0u64, false);

    let (index_state, warm_loaded_index) = detect_warm_index_state(&args, &request);

    // Perf lever (see `native_can_serve_plain_text`): decide ONCE whether this plain-text request
    // can skip the `rg` subprocess. Computed after the passthrough/guard gates above so an
    // rg-required request has already left; a warm compatible `.tg_index` still wins inside
    // `route_search`, which checks the index before this flag is consulted.
    let plain_text = plain_text_native_request_for_search(&args, &request, stdout_is_terminal());
    let native_plain_text = native_can_serve_plain_text(&plain_text);

    #[cfg(feature = "cuda")]
    let gpu_auto_supported = request.paths.len() == 1
        && gpu_native_fallback_reason(&GpuSearchParams {
            patterns: &request.patterns,
            query: &query,
            path: request.primary_path(),
            line_number: args.line_number && !args.no_line_number,
            ignore_case: args.ignore_case,
            smart_case: args.smart_case,
            fixed_strings: args.fixed_strings,
            invert_match: args.invert_match,
            count: args.count,
            context: search_effective_context(&args),
            max_count: args.max_count,
            word_regexp: args.word_regexp,
            globs: args.globs.clone(),
            hidden: args.hidden,
            max_depth: args.max_depth,
            text: args.text,
            no_ignore: args.no_ignore,
            gpu_device_ids: &auto_gpu_ids,
            json: args.json,
            ndjson: args.ndjson,
            verbose: args.verbose,
            replace: args.replace.clone(),
            only_matching: args.only_matching,
            max_filesize: args.max_filesize.clone(),
            color: args.color.clone(),
            no_ignore_vcs: args.no_ignore_vcs,
            path_was_implicit: request.path_was_implicit,
        })
        .is_none();

    #[cfg(not(feature = "cuda"))]
    let gpu_auto_supported = false;

    #[cfg(feature = "cuda")]
    let calibration = load_search_routing_calibration(Path::new(request.primary_path()));
    #[cfg(not(feature = "cuda"))]
    let calibration: Option<SearchRoutingCalibration> = None;

    #[cfg(feature = "cuda")]
    let gpu_available = auto_gpu_available_for_routing();
    #[cfg(not(feature = "cuda"))]
    let gpu_available = false;

    let decision = route_search(
        &SearchRoutingConfig {
            explicit_index: args.index,
            explicit_gpu_device_ids: !args.gpu_device_ids.is_empty(),
            force_cpu: args.force_cpu,
            ast_command: false,
            json: args.json,
            ndjson: args.ndjson,
            rg_available,
            corpus_bytes,
            corpus_bytes_known,
            gpu_auto_supported,
            prefer_rg_passthrough: search_has_context(&args) && !args.json && !args.ndjson,
            pcre2: args.pcre2,
            native_plain_text,
        },
        calibration.as_ref(),
        index_state,
        gpu_available,
    );

    match decision.selection {
        BackendSelection::TrigramIndex => {
            // Step 3b (I.4): an indexed-search ERROR is exit 2, never exit 1 (= "no match").
            handle_index_search(&args, &request, &query, warm_loaded_index)
        }
        BackendSelection::NativeGpu => {
            let gpu_device_ids = if args.gpu_device_ids.is_empty() {
                &auto_gpu_ids
            } else {
                args.gpu_device_ids.as_slice()
            };
            let params = GpuSearchParams {
                patterns: &request.patterns,
                query: &query,
                path: request.primary_path(),
                line_number: args.line_number && !args.no_line_number,
                ignore_case: args.ignore_case,
                smart_case: args.smart_case,
                fixed_strings: args.fixed_strings,
                invert_match: args.invert_match,
                count: args.count,
                context: search_effective_context(&args),
                max_count: args.max_count,
                word_regexp: args.word_regexp,
                globs: args.globs.clone(),
                hidden: args.hidden,
                max_depth: args.max_depth,
                text: args.text,
                no_ignore: args.no_ignore,
                gpu_device_ids,
                json: args.json,
                ndjson: args.ndjson,
                verbose: args.verbose,
                replace: args.replace.clone(),
                only_matching: args.only_matching,
                max_filesize: args.max_filesize.clone(),
                color: args.color.clone(),
                no_ignore_vcs: args.no_ignore_vcs,
                path_was_implicit: request.path_was_implicit,
            };

            #[cfg(feature = "cuda")]
            if decision.reason == RoutingDecision::native_gpu_auto().reason {
                let fallback_decision =
                    RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output);
                let rg_fallback = fallback_decision
                    .allow_rg_fallback
                    .then(|| command_ripgrep_args(&args, &request));
                return handle_auto_gpu_search(
                    params,
                    native_search_config_for_command(
                        &args,
                        &request.patterns[0],
                        &request.paths,
                        request.path_was_implicit,
                        fallback_decision,
                    ),
                    rg_fallback,
                );
            }

            handle_gpu_search(params)
        }
        BackendSelection::NativeCpu => {
            if decision.reason
                == RoutingDecision::native_cpu_gpu_fallback(rg_available, structured_output).reason
            {
                eprintln!(
                    "warning: CUDA is unavailable: no usable GPU devices were found; falling back to native CPU search; this CPU fallback output is not GPU acceleration proof"
                );
            }
            if args.verbose {
                emit_verbose_metadata(decision);
            }

            let rg_fallback = decision
                .allow_rg_fallback
                .then(|| command_ripgrep_args(&args, &request));

            if request.patterns.len() > 1 {
                let (matches, incomplete_paths, binary_match_files) =
                    match collect_native_multi_pattern_matches(
                        &request.patterns,
                        native_search_config_for_command(
                            &args,
                            &request.patterns[0],
                            &request.paths,
                            request.path_was_implicit,
                            decision,
                        ),
                    ) {
                        Ok(collected) => collected,
                        Err(err) => {
                            exit_json_search_runtime_error_if_needed(args.json, args.ndjson, &err);
                            return Err(err);
                        }
                    };
                return emit_multi_pattern_native_results(
                    NativeSearchOutputOptions {
                        decision,
                        query: &query,
                        path: &path_display,
                        requested_gpu_device_ids: &[],
                        json: args.json,
                        ndjson: args.ndjson,
                        count: args.count,
                        line_number: args.line_number && !args.no_line_number,
                        path_was_implicit: request.path_was_implicit,
                    },
                    matches,
                    incomplete_paths,
                    binary_match_files,
                );
            }

            run_native_search_with_optional_rg_fallback(
                native_search_config_for_command(
                    &args,
                    &request.patterns[0],
                    &request.paths,
                    request.path_was_implicit,
                    decision,
                ),
                rg_fallback,
            )
        }
        BackendSelection::Ripgrep => {
            if args.verbose {
                emit_verbose_metadata(decision);
            }

            let exit_code = execute_ripgrep_search(&command_ripgrep_args(&args, &request))?;
            if exit_code != 0 {
                std::process::exit(exit_code.max(1));
            }
            Ok(())
        }
        _ => anyhow::bail!("unsupported search routing decision: {}", decision.reason),
    }
}

/// Persists `index` to `index_path` under the write-serializing index lock -- the ONLY place in
/// the search path that acquires it. Readers (`TrigramIndex::load`, `detect_warm_index_state`)
/// never take this lock; a wrong lock on the read path would deadlock (or at minimum
/// unnecessarily serialize) EVERY `tg` invocation against a warm index (audit #138 item #2
/// critical trap). Per the Backend Fail-Closed Contract (AGENTS.md), a failed PERSIST must never
/// fail the SEARCH: on a lock-acquire timeout (another writer holding it past the 12s budget) or
/// an actual write/rename failure, this warns to stderr and returns without persisting -- the
/// caller's in-memory `index` (already fully built) is unaffected and is what actually answers
/// the search.
pub(crate) fn save_index_locked(index: &TrigramIndex, index_path: &Path, verbose: bool) {
    let index_dir = match tensor_grep_rs::index_cache::ensure_cache_parent_anchored(index_path) {
        Ok(dir) => dir,
        Err(e) => {
            eprintln!("[index] warning: failed to prepare index cache directory: {e}");
            return;
        }
    };
    let Some(file_name) = index_path.file_name() else {
        eprintln!(
            "[index] warning: failed to persist index: {} has no file name",
            index_path.display()
        );
        return;
    };
    match tensor_grep_rs::index_lock::IndexLockGuard::acquire_anchored(index_path, &index_dir) {
        Ok(_guard) => {
            if let Err(e) = index.save_anchored(&index_dir, file_name) {
                eprintln!("[index] warning: failed to persist index: {e}");
            } else {
                let mmap_path = mmap_index_path(index_path);
                if let Some(mmap_name) = mmap_path.file_name() {
                    if let Err(e) =
                        MmapIndex::save_from_trigram_index_anchored(index, &index_dir, mmap_name)
                    {
                        eprintln!("[index] warning: failed to persist mmap index: {e}");
                    }
                }
                if verbose {
                    eprintln!("[index] persisted index to {}", index_path.display());
                }
            }
        }
        Err(timeout) => {
            eprintln!(
                "[index] warning: {timeout}; skipping persistence for this run (search \
                 results are unaffected -- a later invocation will retry)"
            );
        }
    }
}

pub(crate) fn handle_index_search(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    query: &str,
    preloaded_index: Option<LoadedIndex>,
) -> anyhow::Result<()> {
    if request.paths.len() != 1 {
        anyhow::bail!("index search currently supports exactly one path root");
    }

    // Backend Fail-Closed Contract (audit H1a, superseded by audit fix #1 2026-07-11): H1a
    // originally hand-listed the 6 flags below because route_search() (routing.rs) selects
    // TrigramIndex for --index before any compatibility checks run, and run_index_query()
    // only ever reads a handful of fields -- every OTHER search flag was silently dropped
    // instead of honored or refused (e.g. --index -v used to return the NON-inverted set with
    // exit 0). `index_flag_violations` (above `detect_warm_index_state`) replaces the ad-hoc
    // list with an exhaustive per-field classification covering every `SearchArgs` field, not
    // just these 6, and is shared with `detect_warm_index_state`'s warm-auto-routing gate so
    // the two can't drift apart again. Deliberately excludes the pattern-length and
    // non-ASCII-ignore-case checks detect_warm_index_state also has -- those (H1b/H1c) are
    // handled as a transparent full-scan fallback inside
    // TrigramIndex::search/fixed_string_candidate_selection instead of a refusal, since the
    // index can still honor them correctly, just without the trigram prefilter.
    let unsupported_with_index = index_flag_violations(args, request);
    if !unsupported_with_index.is_empty() {
        anyhow::bail!(
            "--index does not support {} yet; rerun without --index (or without the \
             flag(s) above) to search without the trigram index accelerator",
            unsupported_with_index.join(", ")
        );
    }

    let search_path = Path::new(request.primary_path());
    if !search_path.exists() {
        anyhow::bail!(
            "index search path does not exist: {}",
            search_path.display()
        );
    }

    let index_paths = tensor_grep_rs::index_cache::resolve_index_paths(request.primary_path());

    // M17 (audit-m17): never serve a mismatched root. The preloaded index arrived via
    // `detect_warm_index_state`'s routing gate, which now marks a root-mismatched index
    // stale (so this arm is normally never reached with one); the filter is the
    // defense-in-depth invariant that `handle_index_search` itself never serves a wrong
    // tree even if a future caller bypasses that gate. A filtered-out index falls through
    // to the disk-load branch below, which repeats the check and full-rebuilds.
    let index = if let Some(loaded) = preloaded_index.filter(|loaded| {
        loaded
            .root_servability_reason(Path::new(request.primary_path()))
            .is_none()
    }) {
        // audit #138 item #3 (load-once): `detect_warm_index_state` already loaded this index
        // (and, by construction, only ever hands one to us via the `should_route_to_index()` /
        // warm_index() routing arm, which requires `!is_stale`) -- reuse it instead of reading
        // and re-deserializing the same `.tg_index` file a second time.
        if args.verbose {
            eprintln!(
                "[index] loaded cached index: {} files, {} trigrams",
                loaded.file_count(),
                loaded.trigram_count()
            );
        }
        loaded
    } else if tensor_grep_rs::index_cache::confined_regular_file_exists(&index_paths.read_path)
        || tensor_grep_rs::index_cache::confined_regular_file_exists(&mmap_index_path(
            &index_paths.read_path,
        ))
    {
        let loaded = match LoadedIndex::load_for_search(&index_paths.read_path) {
            Ok(idx) => idx,
            Err(e) => {
                eprintln!("[index] warning: failed to load index: {e}, rebuilding...");
                let started = Instant::now();
                let fresh = TrigramIndex::build_with_options(
                    Path::new(request.primary_path()),
                    args.no_ignore,
                )?;
                save_index_locked(&fresh, &index_paths.write_path, args.verbose);
                if args.verbose {
                    eprintln!(
                        "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                        started.elapsed(),
                        fresh.file_count(),
                        fresh.trigram_count(),
                        fresh.total_postings()
                    );
                }
                return run_index_query(args, request, query, &LoadedIndex::Memory(fresh));
            }
        };
        if let Some(reason) = loaded.root_servability_reason(Path::new(request.primary_path())) {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let fresh = TrigramIndex::build_with_options(
                Path::new(request.primary_path()),
                args.no_ignore,
            )?;
            save_index_locked(&fresh, &index_paths.write_path, args.verbose);
            if args.verbose {
                eprintln!(
                    "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                    started.elapsed(),
                    fresh.file_count(),
                    fresh.trigram_count(),
                    fresh.total_postings()
                );
            }
            return run_index_query(args, request, query, &LoadedIndex::Memory(fresh));
        }
        if let Some(reason) = loaded.staleness_reason(args.no_ignore) {
            if args.verbose {
                eprintln!("[index] stale: {reason}");
            }
            let started = Instant::now();
            let base = match loaded {
                LoadedIndex::Memory(idx) => Ok(idx),
                LoadedIndex::Mmap(_) => TrigramIndex::load(&index_paths.read_path),
            };
            let fresh_or_updated = match base {
                Ok(base_idx) => {
                    let update = base_idx.rebuild_incremental_with_options(
                        Path::new(request.primary_path()),
                        args.no_ignore,
                    )?;
                    if args.verbose {
                        eprintln!(
                            "[index] incremental update complete in {:?}: reused {} unchanged files, added {}, modified {}, deleted {}; {} files, {} trigrams, {} postings",
                            started.elapsed(),
                            update.stats.reused_files,
                            update.stats.added_files,
                            update.stats.modified_files,
                            update.stats.deleted_files,
                            update.index.file_count(),
                            update.index.trigram_count(),
                            update.index.total_postings()
                        );
                    }
                    update.index
                }
                Err(_) => TrigramIndex::build_with_options(
                    Path::new(request.primary_path()),
                    args.no_ignore,
                )?,
            };
            save_index_locked(&fresh_or_updated, &index_paths.write_path, args.verbose);
            LoadedIndex::Memory(fresh_or_updated)
        } else {
            if args.verbose {
                eprintln!(
                    "[index] loaded cached index: {} files, {} trigrams",
                    loaded.file_count(),
                    loaded.trigram_count()
                );
            }
            loaded
        }
    } else {
        if args.verbose {
            eprintln!(
                "[index] full rebuild: building index for {}...",
                request.primary_path()
            );
        }
        let started = Instant::now();
        let fresh =
            TrigramIndex::build_with_options(Path::new(request.primary_path()), args.no_ignore)?;
        save_index_locked(&fresh, &index_paths.write_path, args.verbose);
        if args.verbose {
            eprintln!(
                "[index] full rebuild complete in {:?}: {} files, {} trigrams, {} postings",
                started.elapsed(),
                fresh.file_count(),
                fresh.trigram_count(),
                fresh.total_postings()
            );
        }
        LoadedIndex::Memory(fresh)
    };

    run_index_query(args, request, query, &index)
}

pub(crate) fn run_index_query(
    args: &SearchArgs,
    request: &ResolvedSearchRequest,
    query: &str,
    index: &LoadedIndex,
) -> anyhow::Result<()> {
    if args.verbose {
        emit_verbose_metadata(RoutingDecision::warm_index());
    }

    let include_pattern_metadata = request.patterns.len() > 1;
    let mut matches = Vec::new();
    let mut binary_matches = BTreeMap::<PathBuf, (String, u64)>::new();
    for (pattern_id, pattern) in request.patterns.iter().enumerate() {
        // H1e (audit): resolve smart-case (-S) per pattern before querying the index.
        // -S is NOT diverted to ripgrep in JSON/ndjson mode (search_requires_ripgrep_
        // passthrough gates it behind !json && !ndjson), so it reaches the index here;
        // passing only args.ignore_case (false for -S) would search case-sensitively and
        // silently miss uppercase matches an all-lowercase -S pattern must find. Honoring
        // it (smart-case IS index-doable) rather than refusing avoids a UX regression, and
        // reuses the same ignore_case path -- including the H1b/H1c full-scan safety nets
        // in index.rs -- for the resolved case. This single chokepoint covers BOTH explicit
        // --index and warm auto-routing (both reach run_index_query).
        let ignore_case = args.ignore_case
            || (args.smart_case && smart_case_pattern_is_case_insensitive(pattern));
        let (results, binary) =
            index.search_with_binary_matches(pattern, ignore_case, args.fixed_strings)?;
        for (path, offset) in binary {
            let display = index
                .display_path(Path::new(request.primary_path()), &path)
                .to_string_lossy()
                .into_owned();
            binary_matches.entry(path).or_insert((display, offset));
        }
        // EXEMPT from the raw-bytes/base64-fallback treatment (task #266): `TrigramIndex`
        // persists and returns plain `String`s, so `result.text` is already guaranteed valid
        // UTF-8 by construction -- there is no raw byte source here to preserve losslessly, and
        // extending the persisted index format itself is a materially different, larger change
        // than this fix's scope (the shared native walk emitter).
        matches.extend(results.into_iter().map(|result| {
            let (text, bytes, raw) = guaranteed_utf8_match_fields(result.text);
            // M17 F3: the index dereferenced through the canonical root (sound), but the
            // EMITTED path re-projects back through the QUERY's original spelling so a
            // relative / differently-spelled query sees its own path space (tree/a.txt),
            // matching what the native/rg routes emit for the same invocation.
            let file = index
                .display_path(Path::new(request.primary_path()), &result.file)
                .to_string_lossy()
                .into_owned();
            SearchMatchJson {
                file,
                line: result.line,
                text,
                bytes,
                raw,
                range: None,
                meta_variables: None,
                pattern_id: include_pattern_metadata.then_some(pattern_id),
                pattern_text: include_pattern_metadata.then(|| pattern.clone()),
            }
        }));
    }

    let binary_match_files = (!binary_matches.is_empty()).then_some(binary_matches.len());

    if args.json {
        return emit_json_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            request.path_was_implicit,
            binary_match_files,
            None,
        );
    }

    if args.ndjson {
        return emit_ndjson_search_results(
            RoutingDecision::warm_index(),
            query,
            request.primary_path(),
            &[],
            matches,
            // Task 276: this route observed no walk of its own, so it cannot report a
            // count. `None` means "cannot report", NEVER "complete".
            None,
            request.path_was_implicit,
            binary_match_files,
        );
    }

    let with_filename = Path::new(request.primary_path()).is_dir()
        || matches
            .iter()
            .map(|matched| matched.file.as_str())
            .chain(binary_matches.values().map(|(path, _)| path.as_str()))
            .collect::<BTreeSet<_>>()
            .len()
            > 1;
    for (path, offset) in binary_matches.values() {
        emit_index_binary_match_warning(Path::new(path), *offset, with_filename)?;
    }

    if args.count {
        // Audit fix #1 must-fix : emit per-file `path:count`
        // via the SAME emit_count_search_matches the sibling native aggregate path already uses
        // (emit_multi_pattern_native_results below), NOT a bare aggregate total. The old
        // `println!("{unique_count}")` printed a single number (e.g. `3`) while every other count
        // emitter -- `rg -c`, the native CPU engine's append_count_output_bytes, and
        // emit_count_search_matches -- prints per-file counts. Because `count` is (correctly) an
        // Honor flag, the WARM auto-index path reaches here too, so a plain `tg search -c <pat>
        // <dir>` silently changed output shape (per-file -> bare aggregate) the moment a `.tg_index`
        // happened to exist -- exactly the silent-wrong-shape-with-exit-0 this validator exists to
        // prevent. emit_count_search_matches omits zero-count files, byte-matching `rg -c` (the
        // rg-compat target); the native CPU engine's separate grep-style zero-count emission is its
        // own pre-existing divergence, deliberately not replicated on the index fast path.
        if !matches.is_empty() || binary_matches.is_empty() {
            emit_count_search_matches(request.primary_path(), &matches)?;
        }
        // fold-in (a): rg exit-parity. `rg -c` (and the native CPU / multi-pattern engines) exit 1
        // on zero matches; run_index_query never did, so `--index --count` on a no-match query
        // exited 0 -- indistinguishable from a successful search. `matches.is_empty()` is
        // equivalent to the old `unique_count == 0` (unique_line_matches never empties a non-empty
        // set), and emit_count_search_matches prints nothing for an empty set on a dir target,
        // matching `rg -c`'s empty-stdout-plus-exit-1 no-match contract.
        if matches.is_empty() && binary_matches.is_empty() {
            std::process::exit(1);
        }
        return Ok(());
    }

    // fold-in (b): thread `-N`/`--no-line-number` the same way the native/rg-passthrough
    // configs already do (`args.line_number && !args.no_line_number`, see e.g.
    // native_search_config_for_command) instead of emit_plain_search_matches's hardcoded
    // `true`, which made `-N` a no-op on the index path.
    emit_plain_search_matches_with_line_number(
        request.primary_path(),
        &matches,
        args.line_number && !args.no_line_number,
    )?;

    // fold-in (a): see the --count arm above for why this matches native/GPU exit-parity.
    if matches.is_empty() && binary_matches.is_empty() {
        std::process::exit(1);
    }

    Ok(())
}
