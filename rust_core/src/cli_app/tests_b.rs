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
use super::tests_a::*;
use super::*;
use crate::cli_app::*;
use crate::*;
use std::collections::HashMap;
use tensor_grep_rs::rg_passthrough::{
    implicit_search_walk_exceeds_ceiling, IMPLICIT_SEARCH_WALK_FILE_CEILING,
};
#[test]
fn validation_command_argv_substitutes_brace_file_placeholder_safely() {
    // The {file} placeholder variant must also keep a malicious path in a single argv element.
    let argv = validation_command_argv(
        "ruff check {file}",
        Some("/repo/evil; rm -rf ~/`whoami`.py"),
    );
    assert_eq!(
        argv,
        vec![
            "ruff".to_string(),
            "check".to_string(),
            "/repo/evil; rm -rf ~/`whoami`.py".to_string(),
        ]
    );
}

#[test]
fn split_validation_command_argv_rejects_unterminated_quote() {
    assert!(split_validation_command_argv("python \"foo").is_empty());
    assert!(split_validation_command_argv("python 'foo").is_empty());
}

#[test]
fn run_validation_command_rejects_placeholder_in_program_position() {
    // A template whose only token is the placeholder would run the (attacker-named) file itself.
    let result = run_validation_command(
        "lint",
        "$file",
        Some("/repo/evil; rm -rf ~.py"),
        "$file",
        std::path::Path::new("."),
        DEFAULT_VALIDATION_TIMEOUT_MS,
    );
    assert!(!result.success);
    assert!(result.stderr.contains("must name a program"));
}

#[test]
fn run_validation_command_rejects_unbalanced_quote_template() {
    let result = run_validation_command(
        "lint",
        "python \"foo",
        None,
        "python \"foo",
        std::path::Path::new("."),
        DEFAULT_VALIDATION_TIMEOUT_MS,
    );
    assert!(!result.success);
    assert!(result.stderr.contains("empty or has unbalanced quotes"));
}

// -- audit #10 (validation subprocess timeout) + #34 (validation fan-out cap) -------------

/// Builds a validation-command TEMPLATE string from a program + argv, quoting any argument
/// that contains whitespace so `split_validation_command_argv` round-trips it back into a
/// single token (mirrors how a real `--test-cmd`/`--lint-cmd` value is authored).
fn command_template(program: &str, args: &[String]) -> String {
    let mut parts = vec![program.to_string()];
    for arg in args {
        if arg.chars().any(char::is_whitespace) {
            assert!(
                !arg.contains('"'),
                "test helper does not support embedded double quotes"
            );
            parts.push(format!("\"{arg}\""));
        } else {
            parts.push(arg.clone());
        }
    }
    parts.join(" ")
}

/// Cross-platform "block forever" command as a SINGLE process (no shell/grandchild
/// indirection), so a kill-on-timeout assertion only has to reason about one PID.
fn platform_hang_forever_command() -> (&'static str, Vec<String>) {
    if cfg!(windows) {
        (
            "powershell",
            vec![
                "-NoProfile".to_string(),
                "-NonInteractive".to_string(),
                "-Command".to_string(),
                "Start-Sleep -Seconds 300".to_string(),
            ],
        )
    } else {
        ("sleep", vec!["300".to_string()])
    }
}

/// Cross-platform "exit 0 immediately" command.
fn platform_fast_success_command() -> (&'static str, Vec<String>) {
    if cfg!(windows) {
        (
            "cmd",
            vec!["/C".to_string(), "exit".to_string(), "0".to_string()],
        )
    } else {
        ("true", Vec::new())
    }
}

/// Cross-platform "write ~2.5MB to stdout, fast, without reading anything" command: large
/// enough to exceed a typical OS pipe buffer (commonly 4-64KB), so a successful capture here
/// proves the wait path drains output concurrently instead of deadlocking against a full pipe.
/// `reps` writes of 64KiB. `reps = 0` is the spawn-only BASELINE: same
/// interpreter, same argv shape, no output -- so timing it isolates interpreter
/// startup from drain cost.
///
/// One parameterised producer rather than two literals, deliberately. A separate
/// short baseline literal contained no spaces, so rust's windows argv quoting
/// passed it unquoted and PowerShell stripped the inner single quotes
/// (`Write('A')` arrived as `Write(A)`, a ParserError). Sharing the shape makes
/// that unrepresentable instead of correctly quoted in two places.
fn platform_stdout_command(reps: usize) -> (&'static str, Vec<String>) {
    if cfg!(windows) {
        (
            "powershell",
            vec![
                "-NoProfile".to_string(),
                "-NonInteractive".to_string(),
                "-Command".to_string(),
                format!("$s='A'*65536;for($i=0;$i -lt {reps};$i++){{[Console]::Out.Write($s)}}"),
            ],
        )
    } else {
        (
            "dd",
            vec![
                "if=/dev/zero".to_string(),
                "bs=65536".to_string(),
                format!("count={reps}"),
            ],
        )
    }
}

#[test]
fn run_validation_command_kills_a_hanging_process_within_the_timeout() {
    let (program, args) = platform_hang_forever_command();
    let template = command_template(program, &args);
    let timeout_ms = 300;

    let started = Instant::now();
    let result = run_validation_command(
        "test",
        &template,
        None,
        &template,
        std::path::Path::new("."),
        timeout_ms,
    );
    let elapsed = started.elapsed();

    assert!(!result.success, "a hung command must not report success");
    assert_eq!(result.exit_code, None, "a killed process has no exit code");
    assert!(
        result.stderr.contains("exceeded") && result.stderr.contains("timeout"),
        "expected a timeout message, got: {}",
        result.stderr
    );
    // Bounded, not the full 300s the command asked to sleep for: proves the child was
    // actually terminated at the timeout rather than the call blocking until natural exit
    // (the exact #400 hang class, applied to the validation subprocess path).
    assert!(
        elapsed < Duration::from_secs(10),
        "expected the timeout to bound the wait; took {elapsed:?}"
    );
}

#[test]
fn run_validation_command_fast_command_still_succeeds_within_timeout() {
    let (program, args) = platform_fast_success_command();
    let template = command_template(program, &args);

    let result = run_validation_command(
        "lint",
        &template,
        None,
        &template,
        std::path::Path::new("."),
        DEFAULT_VALIDATION_TIMEOUT_MS,
    );

    assert!(result.success, "expected success, got: {result:?}");
    assert_eq!(result.exit_code, Some(0));
}

#[test]
fn run_validation_command_captures_large_stdout_without_deadlock() {
    let (program, args) = platform_stdout_command(40);
    let template = command_template(program, &args);
    // Generous but bounded: if the pipe-fill deadlock footgun (rust-lang#45572) were
    // reintroduced (e.g. a hand-rolled spawn + wait_timeout + wait_with_output instead of
    // process_control's drain-while-timing-out wait), the child would block writing to a
    // full, undrained pipe and this call would hit the timeout and report failure instead of
    // completing quickly -- this is a regression guard, not just a happy-path check.
    let timeout_ms = 60_000;
    // The bound below is PAIRED, not absolute, and it is not what catches a deadlock --
    // a deadlock exhausts the limit, `terminate_for_timeout` kills the child, and the
    // FIRST assertion fires on `result.success`. What it catches is the weaker "drains,
    // but pathologically slowly" regression (a poll loop reading a tiny buffer at a low
    // duty cycle), which is why it is kept rather than deleted.
    //
    // It is paired because the absolute form measured the GENERATOR: windows spawns
    // PowerShell (startup dominates and swings with runner load), unix spawns `dd`
    // (~2ms). Every false red here has therefore been windows -- twice under #303 at
    // 15s/10s, then again at 35.1s after that was widened to 60s/30s. Subtracting a
    // spawn-only run of the SAME interpreter cancels startup and load; a real drain
    // regression still blows the allowance, because it inflates the large arm only.
    let drain_allowance = Duration::from_millis(timeout_ms / 4);

    let (baseline_program, baseline_args) = platform_stdout_command(0);
    let baseline_template = command_template(baseline_program, &baseline_args);
    let baseline_started = Instant::now();
    let baseline_result = run_validation_command(
        "test",
        &baseline_template,
        None,
        &baseline_template,
        std::path::Path::new("."),
        timeout_ms,
    );
    let baseline = baseline_started.elapsed();
    assert!(
        baseline_result.success,
        "the baseline spawn must succeed or it cannot be subtracted from anything; \
         a failed baseline means the interpreter is unavailable, not that drain is fast. \
         got: {baseline_result:?}"
    );

    let started = Instant::now();
    let result = run_validation_command(
        "test",
        &template,
        None,
        &template,
        std::path::Path::new("."),
        timeout_ms,
    );
    let elapsed = started.elapsed();

    assert!(
        result.success,
        "expected the large-output command to finish successfully, got: {result:?}"
    );
    assert!(
        result.stdout.len() > 1_000_000,
        "expected >1MB of captured stdout (pipe-buffer-exceeding), got {} bytes",
        result.stdout.len()
    );
    let drain_cost = elapsed.saturating_sub(baseline);
    assert!(
        drain_cost < drain_allowance,
        "drain is pathologically slow, though not deadlocked -- a deadlock would have failed \
         the success assertion above. Draining 2.6MB cost {drain_cost:?} after subtracting a \
         {baseline:?} baseline spawn of the same interpreter (total {elapsed:?}), against an \
         allowance of {drain_allowance:?}. Because the baseline is subtracted, a merely slow \
         or loaded runner does NOT reach this assertion; a real drain regression does."
    );
}

#[test]
fn resolve_validation_timeout_ms_prefers_flag_over_env_over_default() {
    assert_eq!(
        resolve_validation_timeout_ms(Some(5_000), Some("9000".to_string())),
        5_000,
        "an explicit --validation-timeout-ms flag must win over the env var"
    );
    assert_eq!(
        resolve_validation_timeout_ms(None, Some("9000".to_string())),
        9_000,
        "TG_VALIDATION_TIMEOUT_MS must be honored when no flag is set"
    );
    assert_eq!(
        resolve_validation_timeout_ms(None, None),
        DEFAULT_VALIDATION_TIMEOUT_MS
    );
    assert_eq!(
        resolve_validation_timeout_ms(None, Some("not-a-number".to_string())),
        DEFAULT_VALIDATION_TIMEOUT_MS,
        "a malformed env value must fall back to the default, not panic or become 0"
    );
}

#[test]
fn cap_validation_targets_truncates_and_reports_totals() {
    let targets: Vec<String> = (0..100).map(|i| format!("file_{i}.py")).collect();

    let (capped, truncated, total) = cap_validation_targets(targets.clone(), 50);
    assert_eq!(capped.len(), 50);
    assert!(truncated);
    assert_eq!(total, 100);
    assert_eq!(capped, targets[..50]);

    let (not_capped, truncated, total) = cap_validation_targets(targets.clone(), 200);
    assert_eq!(not_capped.len(), 100);
    assert!(!truncated);
    assert_eq!(total, 100);

    let (unlimited, truncated, total) = cap_validation_targets(targets, 0);
    assert_eq!(unlimited.len(), 100, "0 must disable the cap");
    assert!(!truncated);
    assert_eq!(total, 100);
}

#[test]
fn run_post_apply_validation_caps_targets_and_reports_truncation() {
    let edits: Vec<tensor_grep_rs::backend_ast::RewriteEdit> = (0..100)
        .map(|i| tensor_grep_rs::backend_ast::RewriteEdit {
            id: format!("edit-{i}"),
            file: PathBuf::from(format!("validation_target_{i}.py")),
            planned_mtime_ns: 0,
            line: 1,
            byte_range: 0..0,
            original_text: String::new(),
            replacement_text: String::new(),
            metavar_env: HashMap::new(),
        })
        .collect();

    let (program, mut args) = platform_fast_success_command();
    args.push("{file}".to_string());
    let template = command_template(program, &args);

    let cli_args = parse_run_args(&[
        "tg",
        "run",
        "--test-cmd",
        &template,
        "--max-validation-targets",
        "50",
        ".",
    ]);

    let summary = run_post_apply_validation(&cli_args, ".", &edits)
        .expect("expected a validation summary when --test-cmd is set");

    assert_eq!(
        summary.commands.len(),
        50,
        "expected exactly 50 spawns, one per capped target"
    );
    assert!(summary.validation_targets_truncated);
    assert_eq!(summary.validation_targets_total, 100);
    assert!(
        summary.success,
        "all 50 spawned no-op commands should succeed: {summary:?}"
    );
}

#[test]
fn run_post_apply_validation_does_not_truncate_when_under_the_cap() {
    let edits: Vec<tensor_grep_rs::backend_ast::RewriteEdit> = (0..5)
        .map(|i| tensor_grep_rs::backend_ast::RewriteEdit {
            id: format!("edit-{i}"),
            file: PathBuf::from(format!("validation_target_{i}.py")),
            planned_mtime_ns: 0,
            line: 1,
            byte_range: 0..0,
            original_text: String::new(),
            replacement_text: String::new(),
            metavar_env: HashMap::new(),
        })
        .collect();

    let (program, mut args) = platform_fast_success_command();
    args.push("{file}".to_string());
    let template = command_template(program, &args);

    let cli_args = parse_run_args(&["tg", "run", "--test-cmd", &template, "."]);

    let summary = run_post_apply_validation(&cli_args, ".", &edits)
        .expect("expected a validation summary when --test-cmd is set");

    assert_eq!(summary.commands.len(), 5);
    assert!(!summary.validation_targets_truncated);
    assert_eq!(summary.validation_targets_total, 5);
    assert!(summary.success);
}

#[test]
fn search_request_preserves_multiple_path_roots_for_structured_output() {
    let args = parse_search_args(&["tg", "search", "ERROR", "src", "tests", "docs", "--ndjson"]);
    let request = resolve_search_request(&args).expect("expected search request");
    let decision = RoutingDecision::native_cpu_json(false);

    assert_eq!(request.patterns, vec!["ERROR".to_string()]);
    assert_eq!(
        request.paths,
        vec!["src".to_string(), "tests".to_string(), "docs".to_string()]
    );
    assert_eq!(request.path_display(), "src tests docs");
    assert_eq!(
        command_ripgrep_args(&args, &request).paths,
        vec!["src".to_string(), "tests".to_string(), "docs".to_string()]
    );
    assert_eq!(
        native_search_config_for_command(
            &args,
            "ERROR",
            &request.paths,
            request.path_was_implicit,
            decision
        )
        .paths,
        vec![
            PathBuf::from("src"),
            PathBuf::from("tests"),
            PathBuf::from("docs")
        ]
    );
}

// --- Audit #105: native-CPU implicit-walk-ceiling signal threading ---------------------
// #100 hoisted a walk-ceiling gate into `execute_ripgrep_search` (rg-passthrough engine
// only); the native-CPU engine (`run_native_search`, reached via `--json`, `--force-cpu`,
// single-pattern `--fixed-strings`, and rg-unavailable routing) never received the
// `path_was_implicit` signal at all -- `NativeSearchConfig` had no such field. These tests
// pin that the signal is now correctly recorded end-to-end from real CLI parsing through
// both `NativeSearchConfig` builders, mirroring
// `frontdoor_args_record_path_was_implicit_for_e_flag_bypass`.

#[test]
fn native_search_config_for_command_records_path_was_implicit_for_json_route() {
    // `tg search -e "TODO" --json` (no explicit PATH) is exactly the #105 bypass shape: this
    // routes to `native_cpu_json` (reason "json_output"), never through
    // `execute_ripgrep_search`'s #100 gate at all.
    let implicit_args = parse_search_args(&["tg", "search", "-e", "TODO", "--json"]);
    let implicit_request = resolve_search_request(&implicit_args).expect("expected search request");
    assert!(
        implicit_request.path_was_implicit,
        "no PATH given must record path_was_implicit = true"
    );
    let implicit_config = native_search_config_for_command(
        &implicit_args,
        "TODO",
        &implicit_request.paths,
        implicit_request.path_was_implicit,
        RoutingDecision::native_cpu_json(false),
    );
    assert!(
        implicit_config.path_was_implicit,
        "NativeSearchConfig must record path_was_implicit = true for an implicit-path \
         --json search"
    );

    let explicit_args = parse_search_args(&["tg", "search", "-e", "TODO", "--json", "src"]);
    let explicit_request = resolve_search_request(&explicit_args).expect("expected search request");
    assert!(
        !explicit_request.path_was_implicit,
        "an explicit trailing PATH must record path_was_implicit = false"
    );
    let explicit_config = native_search_config_for_command(
        &explicit_args,
        "TODO",
        &explicit_request.paths,
        explicit_request.path_was_implicit,
        RoutingDecision::native_cpu_json(false),
    );
    assert!(
        !explicit_config.path_was_implicit,
        "NativeSearchConfig must record path_was_implicit = false for an explicit-path \
         search"
    );
}

#[test]
fn native_search_config_for_positional_records_path_was_implicit() {
    // Sibling of the above for the bare positional fast-path CLI (`tg PATTERN [PATH]`).
    use clap::Parser;
    let implicit_raw_args = ["tg", "TODO", "--json"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let implicit_cli =
        PositionalCli::try_parse_from(&implicit_raw_args).expect("expected CLI to parse");
    let implicit_paths = implicit_search_paths(&implicit_cli.path, false);
    let implicit_config = native_search_config_for_positional(
        &implicit_cli,
        "TODO",
        &implicit_paths,
        RoutingDecision::native_cpu_json(false),
    );
    assert!(
        implicit_config.path_was_implicit,
        "no PATH given must record path_was_implicit = true"
    );

    let explicit_raw_args = ["tg", "TODO", "--json", "src"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let explicit_cli =
        PositionalCli::try_parse_from(&explicit_raw_args).expect("expected CLI to parse");
    let explicit_paths = implicit_search_paths(&explicit_cli.path, false);
    let explicit_config = native_search_config_for_positional(
        &explicit_cli,
        "TODO",
        &explicit_paths,
        RoutingDecision::native_cpu_json(false),
    );
    assert!(
        !explicit_config.path_was_implicit,
        "an explicit trailing PATH must record path_was_implicit = false"
    );
}

#[test]
fn collect_native_multi_pattern_matches_exits_2_not_1_on_ceiling_refusal() {
    // Audit #105: `collect_native_multi_pattern_matches` (used by every multi-`-e` native-CPU
    // route) used to let an implicit-walk-ceiling refusal `Err` propagate via `?` all the way
    // to `main()`'s default exit-1 termination instead of the fast-bounded exit-2 refusal
    // every OTHER native-CPU route gets. `exit_on_native_multi_pattern_ceiling_refusal`
    // (the fix) calls `std::process::exit(2)` directly for this ONE error, which cannot be
    // observed in-process without exiting the test binary -- so this pins the OTHER half of
    // the contract instead: the recognizer used to gate that exit call correctly identifies
    // the shared refusal message and does not misfire on an unrelated native-search error.
    let refusal = tensor_grep_rs::rg_passthrough::format_unbounded_implicit_search_walk_error(1500);
    assert!(is_unbounded_implicit_search_walk_refusal(&refusal));
    assert!(!is_unbounded_implicit_search_walk_refusal(
        "native search path does not exist: /nope"
    ));
}

#[test]
fn broad_scan_refusal_json_envelope_matches_python_field_for_field() {
    // Task #17: pins the exact vocabulary `cli/main.py`'s `_emit_broad_scan_refusal` uses
    // (`tests/unit/test_broad_scan_refusal_json_envelope.py` pins the Python side) -- the
    // whole point of this task is that BOTH front doors must use these same field names.
    let message = tensor_grep_rs::rg_passthrough::format_unbounded_implicit_search_walk_error(
        tensor_grep_rs::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );
    let payload = broad_scan_refusal_json_envelope(".", &message);
    assert_eq!(payload["version"], JSON_OUTPUT_VERSION);
    assert_eq!(payload["path"], ".");
    assert_eq!(payload["total_matches"], 0);
    assert_eq!(payload["total_files"], 0);
    assert_eq!(payload["matches"], serde_json::json!([]));
    assert_eq!(payload["truncated"], true);
    assert_eq!(payload["result_incomplete"], true);
    assert_eq!(payload["incomplete_reason"], message);
    assert_eq!(payload["incomplete_reason_class"], "scan_limit");
    assert_eq!(payload["error"]["code"], "broad_scan_refused");
    assert_eq!(payload["error"]["message"], message);
    assert_eq!(payload["error"]["retryable"], false);
}

#[test]
fn exit_on_native_multi_pattern_ceiling_refusal_passes_through_other_errors_unchanged() {
    // CONTROL ARM for task #17's `json`/`path` parameters: an error that is NOT the shared
    // ceiling refusal must return completely unmodified -- no printing, no exit, and (unlike
    // the refusal path) this half CAN be observed in-process because it never reaches
    // `std::process::exit`. Regressing this would mean bad-pattern/bad-path errors under
    // `--json` start being misreported as `broad_scan_refused`.
    let original = "native search path does not exist: /nope";
    let err = anyhow::anyhow!(original);
    let returned = exit_on_native_multi_pattern_ceiling_refusal(err, true, ".");
    assert_eq!(returned.to_string(), original);
}

#[test]
fn native_search_config_path_display_mirrors_resolved_search_request_convention() {
    // Same join-with-space-or-default-to-dot convention as `ResolvedSearchRequest::
    // path_display` (used for the SAME field on a successful result), so a refusal's `path`
    // never reads differently from what a successful run of the same invocation would show.
    assert_eq!(native_search_config_path_display(&[]), ".");
    assert_eq!(
        native_search_config_path_display(&[PathBuf::from("src")]),
        "src"
    );
    assert_eq!(
        native_search_config_path_display(&[PathBuf::from("src"), PathBuf::from("tests")]),
        "src tests"
    );
}

#[test]
fn search_request_resolves_multiple_regexp_patterns_and_paths() {
    let args = parse_search_args(&[
        "tg",
        "search",
        "--fixed-strings",
        "-e",
        "TODO",
        "-e",
        "FIXME",
        "src",
        "tests",
    ]);
    let request = resolve_search_request(&args).expect("expected search request");

    assert_eq!(
        request.patterns,
        vec!["TODO".to_string(), "FIXME".to_string()]
    );
    assert_eq!(request.paths, vec!["src".to_string(), "tests".to_string()]);
    assert_eq!(request.query_display(), "TODO | FIXME");
    assert_eq!(request.path_display(), "src tests");
    assert_eq!(
        command_ripgrep_args(&args, &request).patterns,
        vec!["TODO".to_string(), "FIXME".to_string()]
    );
}

#[test]
fn search_request_accepts_dash_leading_regexp_pattern() {
    let args = parse_search_args(&["tg", "search", "-e", "-needle", "--sort", "path", "."]);
    let request = resolve_search_request(&args).expect("expected search request");

    assert_eq!(request.patterns, vec!["-needle".to_string()]);
    assert_eq!(request.paths, vec![".".to_string()]);
    assert_eq!(
        command_ripgrep_args(&args, &request).patterns,
        vec!["-needle".to_string()]
    );
}

#[test]
fn default_search_frontdoor_treats_format_rg_as_noop() {
    let args =
        parse_default_frontdoor_args(&["tg", "search", "--format", "rg", "ERROR", "bench_data"]);

    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn top_level_search_frontdoor_treats_format_rg_as_noop() {
    let args = parse_default_frontdoor_args(&["tg", "--format", "rg", "ERROR", "bench_data"]);

    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn top_level_search_frontdoor_accepts_format_rg_equals_form() {
    let args = parse_default_frontdoor_args(&["tg", "--format=rg", "ERROR", "bench_data"]);

    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn top_level_search_frontdoor_accepts_explicit_format_rg_fixed_string() {
    let args = parse_default_frontdoor_args(&[
        "tg",
        "--format",
        "rg",
        "--color",
        "never",
        "--sort",
        "path",
        "-n",
        "-F",
        "ERROR",
        "bench_data",
    ]);

    assert!(args.fixed_strings);
    assert!(args.line_number);
    assert_eq!(args.color.as_deref(), Some("never"));
    assert_eq!(args.sort.as_deref(), Some("path"));
    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn top_level_search_frontdoor_accepts_no_line_number() {
    let args = parse_default_frontdoor_args(&[
        "tg",
        "--format",
        "rg",
        "-n",
        "-N",
        "-F",
        "ERROR",
        "bench_data",
    ]);

    assert!(args.fixed_strings);
    assert!(!args.line_number);
    assert!(args.no_line_number);
    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn top_level_search_frontdoor_accepts_context_flags_option_first() {
    let args = parse_default_frontdoor_args(&["tg", "-n", "-C", "2", "ERROR", "bench_data"]);

    assert!(args.line_number);
    assert_eq!(args.context, Some(2));
    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn search_frontdoor_rejects_plain_json_without_explicit_rg_format() {
    for tokens in [
        vec!["tg", "search", "--json", "ERROR", "bench_data"],
        vec!["tg", "--json", "ERROR", "bench_data"],
    ] {
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        assert!(
            parse_default_search_frontdoor_args(&raw_args).is_none(),
            "plain --json must stay on tensor-grep aggregate JSON path for {tokens:?}"
        );
    }
}

#[test]
fn search_frontdoor_accepts_json_when_rg_format_is_explicit() {
    for tokens in [
        vec![
            "tg",
            "search",
            "--format",
            "rg",
            "--json",
            "ERROR",
            "bench_data",
        ],
        vec!["tg", "--format", "rg", "--json", "ERROR", "bench_data"],
    ] {
        let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
        let parsed = parse_default_search_frontdoor_args(&raw_args)
            .expect("explicit --format rg --json should use rg JSON Lines passthrough");
        assert!(parsed.json);
        assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }
}

#[test]
fn default_search_frontdoor_rejects_non_rg_format() {
    let raw_args = ["tg", "search", "--format=json", "ERROR", "bench_data"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
}

#[test]
fn default_search_frontdoor_accepts_sort_path_passthrough() {
    let args =
        parse_default_frontdoor_args(&["tg", "search", "--sort", "path", "ERROR", "bench_data"]);

    assert_eq!(args.sort.as_deref(), Some("path"));
    assert_eq!(args.patterns, vec!["ERROR".to_string()]);
    assert_eq!(args.paths, vec!["bench_data".to_string()]);
}

#[test]
fn search_args_accept_format_rg_when_native_frontdoor_handles_richer_rg_modes() {
    let args = parse_search_args(&[
        "tg",
        "search",
        "--format",
        "rg",
        "--files-with-matches",
        "--sort",
        "path",
        "ERROR",
        "bench_data",
    ]);

    assert_eq!(args.format.as_deref(), Some("rg"));
    assert!(args.files_with_matches);
    assert_eq!(args.sort.as_deref(), Some("path"));
}

#[test]
fn search_format_python_passthrough_args_detects_non_rg_formats() {
    let raw_args = ["tg", "search", "--format=json", "ERROR", "bench_data"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(
        search_format_python_passthrough_args(&raw_args),
        Some(vec![
            "--format=json".to_string(),
            "ERROR".to_string(),
            "bench_data".to_string()
        ])
    );
}

#[test]
fn search_format_python_passthrough_args_keeps_rg_format_native() {
    let raw_args = ["tg", "search", "--format", "rg", "ERROR", "bench_data"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn search_format_python_passthrough_args_routes_rank_flag_to_python() {
    // `tg search --rank` must delegate to the Python sidecar (which owns the BM25 re-rank)
    // instead of being clap-rejected as an unknown flag by the native front door.
    let raw_args = ["tg", "search", "--rank", "invoice", "src"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(
        search_format_python_passthrough_args(&raw_args),
        Some(vec![
            "--rank".to_string(),
            "invoice".to_string(),
            "src".to_string()
        ])
    );
}

#[test]
fn search_format_python_passthrough_args_routes_semantic_flag_to_python() {
    // `tg search --semantic` must delegate to the Python sidecar (which owns the dense/RRF
    // hybrid re-rank) instead of being clap-rejected as an unknown flag by the native front
    // door -- mirrors the --rank case above.
    let raw_args = ["tg", "search", "--semantic", "invoice", "src"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(
        search_format_python_passthrough_args(&raw_args),
        Some(vec![
            "--semantic".to_string(),
            "invoice".to_string(),
            "src".to_string()
        ])
    );
}

#[test]
fn search_format_python_passthrough_args_routes_ltl_flag_to_python() {
    // `tg search --ltl` is a Python-side temporal query (CPUBackend::_search_ltl);
    // it must delegate to the Python sidecar instead of being clap-rejected as an
    // unknown flag by the native front door -- mirrors the --rank case above.
    // Registered on BOTH front doors per the 2-front-door law (the other door is
    // bootstrap.py::_TG_ONLY_SEARCH_FLAGS).
    let raw_args = ["tg", "search", "--ltl", "open -> eventually close", "src"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(
        search_format_python_passthrough_args(&raw_args),
        Some(vec![
            "--ltl".to_string(),
            "open -> eventually close".to_string(),
            "src".to_string()
        ])
    );
}

// -- Audit #138/#140: --index must short-circuit the Python-passthrough front door --------

#[test]
fn search_format_python_passthrough_args_short_circuits_index_with_json_gated_flag() {
    // `--no-hidden` is in SEARCH_PYTHON_PASSTHROUGH_FLAGS (honored unconditionally by that
    // allowlist). Before the fix, combining it with --index still forwarded the whole
    // invocation -- literal "--index" token included -- to the Python sidecar, which has no
    // such option.
    let raw_args = [
        "tg",
        "search",
        "--index",
        "--no-hidden",
        "--json",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();

    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn search_format_python_passthrough_args_short_circuits_index_with_unconditional_allowlist_flag() {
    // `--require-git` is honored by SEARCH_PYTHON_PASSTHROUGH_FLAGS unconditionally (no
    // --json gate needed) -- a different branch than the --json-gated case above.
    let raw_args = [
        "tg",
        "search",
        "--index",
        "--require-git",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();

    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn search_format_python_passthrough_args_short_circuits_index_with_structured_output_only_flag() {
    // `--passthru` only routes to Python when combined with --json/--ndjson (the function's
    // third check); confirm --index short-circuits that branch too.
    let raw_args = [
        "tg",
        "search",
        "--index",
        "--json",
        "--passthru",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();

    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn search_format_python_passthrough_args_short_circuits_index_with_multiline_only_flag() {
    // `-U`/`--multiline` only routes to Python when combined with --json/--ndjson (the
    // function's fourth, final check); confirm --index short-circuits that branch too.
    let raw_args = [
        "tg",
        "search",
        "--index",
        "--json",
        "-U",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();

    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn search_format_python_passthrough_args_still_routes_non_index_invocations_to_python() {
    // TRAP guard: the exact same flag combinations as the short-circuit tests above, MINUS
    // --index, must still route to the Python sidecar exactly as before the fix -- adding the
    // --index short-circuit must not change behavior for non-index invocations.
    let no_hidden = [
        "tg",
        "search",
        "--no-hidden",
        "--json",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();
    assert_eq!(
        search_format_python_passthrough_args(&no_hidden),
        Some(vec![
            "--no-hidden".to_string(),
            "--json".to_string(),
            "ERROR".to_string(),
            "bench_data".to_string(),
        ])
    );

    let require_git = ["tg", "search", "--require-git", "ERROR", "bench_data"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    assert_eq!(
        search_format_python_passthrough_args(&require_git),
        Some(vec![
            "--require-git".to_string(),
            "ERROR".to_string(),
            "bench_data".to_string(),
        ])
    );

    let passthru = [
        "tg",
        "search",
        "--json",
        "--passthru",
        "ERROR",
        "bench_data",
    ]
    .iter()
    .map(OsString::from)
    .collect::<Vec<_>>();
    assert_eq!(
        search_format_python_passthrough_args(&passthru),
        Some(vec![
            "--json".to_string(),
            "--passthru".to_string(),
            "ERROR".to_string(),
            "bench_data".to_string(),
        ])
    );
}

#[test]
fn orient_is_a_known_python_command_not_a_search_pattern() {
    // `tg orient PATH` must be recognized as a passthrough command so the native front door
    // delegates to the Python `orient` handler instead of treating "orient" as a ripgrep
    // pattern via run_positional_cli().
    assert!(is_known_python_command("orient"));
}

#[test]
fn scoped_parser_keeps_known_and_reserved_disjoint() {
    // A90 lifecycle invariant: a reserved name must read `reserved == true` AND
    // `known == false` (the unscoped include_str parser leaked reserved names into
    // "known", which would have made the not-known gate never fire). Both directions:
    // known names must still read reserved == false.
    for reserved in ["edit-ready", "verify-edit", "workspace"] {
        assert!(
            is_reserved_python_command(reserved),
            "{reserved} must be reserved"
        );
        assert!(
            !is_known_python_command(reserved),
            "{reserved} must NOT be known (disjointness)"
        );
    }
    assert!(!is_reserved_python_command("orient"));
    assert!(is_known_python_command("orient"));
    assert!(is_known_python_command("search"));
    assert!(is_known_python_command("coverage"));
    assert!(!is_reserved_python_command("search"));
}

#[test]
fn scoped_parser_member_surface_is_robust() {
    // A90 codex R2 MEDIUM pins: the scoped set extractor must be quote/escape aware,
    // comment aware, brace-aware inside strings, and trailing-comma tolerant. Real
    // commands.py members are all plain identifiers; these pins protect the parser against
    // future edits (e.g. a member containing '#' or braces, or an escaped quote).
    let members = python_set_members("KNOWN_COMMANDS");
    // The authoritative set must parse completely and losslessly.
    assert!(members.contains(&"search".to_string()));
    assert!(members.contains(&"blast-radius".to_string()));
    assert!(members.contains(&"install-dense".to_string()));
    // Known members are exactly the quoted literals in the block, none come from comments
    // or the PYTHON_FULL_HELP_COMMANDS block (e.g. the docstring/comment lines mention
    // "ripgrep" and "Rust" in prose that must not parse as members).
    assert!(!members.contains(&"ripgrep".to_string()));
    assert!(!members.contains(&"Rust".to_string()));
    // Structure pin: every parsed member must be a CLEAN identifier — no backslash, no
    // quote, no trailing comma smuggled into the name. A leak here means the scanner's
    // escape/quote handling regressed (codex R2 MEDIUM).
    for member in &members {
        assert!(
            !member.contains('\\'),
            "member leaked a backslash: {member:?}"
        );
        assert!(!member.contains('"'), "member leaked a quote: {member:?}");
        assert!(
            !member.ends_with(','),
            "member leaked a trailing comma: {member:?}"
        );
    }
    // Internal hidden commands are legitimate members and must be recognized by membership
    // (so hidden-command dispatch still works); nearest hides them from suggestions.
    assert!(is_known_python_command("__gpu-native-stats"));
    assert!(members.contains(&"__gpu-native-stats".to_string()));
    assert!(!nearest_commands("__gpu-native-stats").contains(&"__gpu-native-stats".to_string()));

    // Reserved names are parsed from THEIR OWN block, not KNOWN_COMMANDS.
    let reserved = python_set_members("RESERVED_TOP_LEVEL_COMMANDS");
    assert!(reserved.contains(&"edit-ready".to_string()));
    assert!(!reserved.contains(&"orient".to_string()));
}

#[test]
fn unknown_top_level_command_refusal_fires_on_reserved_flag_shapes() {
    // A90: `tg edit-ready --json` / `--help` refuse; unreserved pattern+flag stays search.
    let reserved_json = ["tg", "edit-ready", "--json"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let reserved_help = ["tg", "edit-ready", "--help"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let unknown_help = ["tg", "qqq", "--help"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let pattern_flag = ["tg", "hello", "--json"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let pattern_path = ["tg", "hello", "docs/"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let reserved_positional = ["tg", "edit-ready", "docs/"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let known = ["tg", "orient", "--json"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(top_level_unknown_command_refusal(&reserved_json));
    assert!(top_level_unknown_command_refusal(&reserved_help));
    assert!(top_level_unknown_command_refusal(&unknown_help));
    assert!(!top_level_unknown_command_refusal(&pattern_flag));
    assert!(!top_level_unknown_command_refusal(&pattern_path));
    assert!(!top_level_unknown_command_refusal(&reserved_positional));
    assert!(!top_level_unknown_command_refusal(&known));
}
