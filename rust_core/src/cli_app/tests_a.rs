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
use super::*;
use crate::cli_app::*;
use crate::*;
use std::collections::HashMap;
// Bug #88/#480/#100: these 3 items moved to `rg_passthrough.rs` (see the breadcrumb comment
// above `parse_early_ripgrep_args`); re-imported here so this file's existing tests keep
// compiling unqualified via `use super::*` above.
use tensor_grep_rs::rg_passthrough::{
    implicit_search_walk_exceeds_ceiling, IMPLICIT_SEARCH_WALK_FILE_CEILING,
};

pub(super) fn parse_run_args(tokens: &[&str]) -> RunArgs {
    use clap::Parser;
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    let cli = CommandCli::try_parse_from(&raw_args).expect("expected CLI args to parse");
    match cli.command {
        Commands::Run(args) => args,
        _ => panic!("expected run command"),
    }
}

pub(super) fn parse_args(tokens: &[&str]) -> RipgrepSearchArgs {
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    parse_early_ripgrep_args(&raw_args).expect("expected early rg args to parse")
}

// Task #271: `parse_early_ripgrep_args` recognizes the literal spellings `-u`/
// `--unrestricted` but NOT a ripgrep combined-short-flag cluster like `-uu`/`-iu` -- those
// are rejected as a different token. The actual fail-closed guarantee that such a cluster
// never silently reaches the native fast path is
// `parse_early_ripgrep_args`'s own catch-all arm (`_ if token.starts_with('-') => return
// None`), which forces a fall-through to the full Python CLI for any unrecognized
// `-`-prefixed token. These tests assert that guarantee directly, at the function it actually
// lives in, rather than only via the flags list this comment could otherwise be mistaken for
// covering it.
#[test]
fn parse_early_ripgrep_args_rejects_combined_unrestricted_short_cluster() {
    let raw_args = ["tg", "search", "-uu", "needle", "."]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    assert!(
        parse_early_ripgrep_args(&raw_args).is_none(),
        "-uu must fall through to the Python CLI, not be silently misparsed by the native \
         fast-path parser"
    );
}

#[test]
fn parse_early_ripgrep_args_rejects_combined_ignore_case_unrestricted_cluster() {
    let raw_args = ["tg", "search", "-iu", "needle", "."]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    assert!(
        parse_early_ripgrep_args(&raw_args).is_none(),
        "-iu must fall through to the Python CLI, not be silently misparsed by the native \
         fast-path parser"
    );
}

pub(super) fn parse_default_frontdoor_args(tokens: &[&str]) -> RipgrepSearchArgs {
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    parse_default_search_frontdoor_args(&raw_args).expect("expected frontdoor args to parse")
}

pub(super) fn parse_search_args(tokens: &[&str]) -> SearchArgs {
    use clap::Parser;
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    let cli = CommandCli::try_parse_from(&raw_args).expect("expected CLI args to parse");
    match cli.command {
        Commands::Search(args) => args,
        _ => panic!("expected search command"),
    }
}

pub(super) fn parse_positional_cli(tokens: &[&str]) -> PositionalCli {
    use clap::Parser;
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    PositionalCli::try_parse_from(&raw_args).expect("expected CLI args to parse")
}

pub(super) fn json_conflicts(tokens: &[&str]) -> Vec<String> {
    let raw_args = tokens.iter().map(OsString::from).collect::<Vec<_>>();
    json_aggregate_render_flag_conflicts(&raw_args)
}

#[test]
fn json_aggregate_flags_incompatible_render_flags() {
    // audit C3: plain --json + a render-only flag must be flagged so the native binary
    // rejects it directly instead of delegating to (and deadlocking via) a stale Python
    // sidecar in the native<->python re-exec chain.
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "-b", "foo", "f.py"]),
        vec!["-b".to_string()]
    );
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "--heading", "foo", "f.py"]),
        vec!["--heading".to_string()]
    );
    // Option-first form (no explicit `search` subcommand) flags trigger flags too:
    // `-b` is in SEARCH_PYTHON_PASSTHROUGH_FLAGS so it is recognized as a search.
    assert_eq!(
        json_conflicts(&["tg", "--json", "-b", "foo", "f.py"]),
        vec!["-b".to_string()]
    );
    // `tg search --json --passthru` (explicit search) delegates via the --passthru gate,
    // so the native guard must reject it directly.
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "--passthru", "foo", "f.py"]),
        vec!["--passthru".to_string()]
    );
    // --byte-offset is an alias of -b and normalizes to the canonical spelling.
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "--byte-offset", "foo", "f.py"]),
        vec!["-b".to_string()]
    );
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "--passthru", "-b", "foo", "f.py"]),
        vec!["--passthru".to_string(), "-b".to_string()]
    );
}

#[test]
fn json_aggregate_allows_plain_json_and_rg_format() {
    // plain --json (no render flag) is the native aggregate path — allowed.
    assert!(json_conflicts(&["tg", "search", "--json", "foo", "f.py"]).is_empty());
    // --format rg --json carries render metadata via ripgrep JSON Lines — allowed.
    assert!(
        json_conflicts(&["tg", "search", "--format", "rg", "--json", "-b", "foo", "f.py"])
            .is_empty()
    );
    // a literal render-flag-looking pattern after `--` is not a flag.
    assert!(json_conflicts(&["tg", "search", "--json", "--", "--passthru"]).is_empty());
}

#[test]
fn json_aggregate_format_rg_after_double_dash_does_not_suppress_real_conflict() {
    // Regression (audit MED): `--format rg` / `--format=rg` smuggled AFTER `--` is a
    // literal search pattern, not the format flag, so it must NOT satisfy the rg-format
    // allowance. The genuine `-b` render-flag conflict BEFORE `--` must still be reported
    // (otherwise the native binary delegates the --json+render combo to the Python
    // sidecar, re-opening the C3 fork-bomb against a guard-less Python).
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "-b", "--", "--format", "rg"]),
        vec!["-b".to_string()]
    );
    assert_eq!(
        json_conflicts(&["tg", "search", "--json", "-b", "--", "--format=rg"]),
        vec!["-b".to_string()]
    );
}

// Not cuda-gated (unlike the tests that originally motivated it): task #131 F3 tests against
// the non-cuda-gated `gpu_sidecar_search_payload`/`ripgrep_args_for_gpu_params`/
// `gpu_cpu_fallback_unhonorable_flag` also need a `GpuSearchParams` fixture.
pub(super) fn gpu_params_for_patterns(patterns: &[String]) -> GpuSearchParams<'_> {
    GpuSearchParams {
        patterns,
        query: patterns.first().map(String::as_str).unwrap_or_default(),
        path: ".",
        line_number: false,
        ignore_case: false,
        smart_case: false,
        fixed_strings: true,
        invert_match: false,
        count: false,
        context: None,
        max_count: None,
        word_regexp: false,
        globs: Vec::new(),
        hidden: false,
        max_depth: None,
        text: false,
        no_ignore: true,
        gpu_device_ids: &[0],
        json: true,
        ndjson: false,
        verbose: false,
        replace: None,
        only_matching: false,
        max_filesize: None,
        color: None,
        no_ignore_vcs: false,
        path_was_implicit: false,
    }
}

#[cfg(feature = "cuda")]
#[test]
fn gpu_native_route_rejects_case_insensitive_smart_case_patterns() {
    let lowercase = vec!["warning".to_string()];
    let mut params = gpu_params_for_patterns(&lowercase);
    params.smart_case = true;
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("case-insensitive searches are not yet supported by native GPU routing")
    );

    let uppercase = vec!["WARNING".to_string()];
    let mut params = gpu_params_for_patterns(&uppercase);
    params.smart_case = true;
    assert_eq!(gpu_native_fallback_reason(&params), None);
}

#[cfg(feature = "cuda")]
#[test]
fn gpu_native_route_rejects_line_terminator_patterns() {
    let patterns = vec!["foo\nbar".to_string()];
    let params = gpu_params_for_patterns(&patterns);

    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("line-terminator patterns require CPU or sidecar routing")
    );
}

#[cfg(feature = "cuda")]
#[test]
fn gpu_native_route_rejects_binary_as_text_searches() {
    let patterns = vec!["SECRET".to_string()];
    let mut params = gpu_params_for_patterns(&patterns);
    params.text = true;

    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("binary-as-text searches are not yet supported by native GPU routing")
    );
}

// -- task #131 F3 (Backend Fail-Closed Contract: GpuSearchParams flag completeness) --------
//
// Before this fix, `GpuSearchParams` had no field at all for `--replace`, `--only-matching`,
// `--max-filesize`, `--color`, or `--no-ignore-vcs`, and `line_number` was hardcoded at every
// construction site -- `tg PAT --gpu-device-ids 0 --replace X` printed "falling back to
// native CPU" and then silently ran WITHOUT `--replace`, exit 0. These tests would not even
// have COMPILED against that struct (the fields/functions they reference did not exist),
// which is itself the clearest possible RED signal for a field-completeness bug.

#[cfg(feature = "cuda")]
#[test]
fn gpu_native_route_rejects_previously_unrepresented_flags() {
    let patterns = vec!["needle".to_string()];

    let mut params = gpu_params_for_patterns(&patterns);
    params.replace = Some("REPLACED".to_string());
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("--replace searches are not yet supported by native GPU routing")
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.only_matching = true;
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("--only-matching searches are not yet supported by native GPU routing")
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.max_filesize = Some("10M".to_string());
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("--max-filesize is not yet supported by native GPU routing")
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.color = Some("always".to_string());
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("--color is not yet supported by native GPU routing")
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.no_ignore_vcs = true;
    assert_eq!(
        gpu_native_fallback_reason(&params),
        Some("--no-ignore-vcs is not yet supported by native GPU routing")
    );

    // Baseline: none of the 5 set (plus the pre-existing 8 conditions unset) must still fall
    // through to native-GPU routing, else every GPU search would now wrongly fall back.
    let params = gpu_params_for_patterns(&patterns);
    assert_eq!(gpu_native_fallback_reason(&params), None);
}

// GPU-P0-3 (#171): `validate_requested_cuda_device_ids` (gpu_native.rs) raises "invalid CUDA
// device id {id}; available CUDA devices: {..}" for an out-of-range --gpu-device-ids request.
// Before this fix, `classify_gpu_route_failure`'s catch-all arm relabeled that message as
// "CUDA initialization failed: ..." -- true-sounding but wrong: nothing failed to initialize,
// the id was simply invalid. These tests pin the classifier's OWN reason, verbatim, distinct
// from the genuine-initialization-failure arms below.
//
// GPU Phase-0 gate-nit #172 NIT-4 / MF-1: these 3 tests used to ALSO carry their own
// `#[cfg(feature = "cuda")]`, so a default `cargo test` (no --features cuda) never compiled
// or ran them at all -- silently, since `mod tests` itself is only `#[cfg(test)]`-gated, so
// nothing signaled the gap. `classify_gpu_route_failure` and its types are now gated
// `any(feature = "cuda", test)` (see the definitions above), which is present under plain
// `cargo test`, so the redundant per-test cuda gate is dropped here and these run by default.
#[test]
fn classify_gpu_route_failure_reports_invalid_device_id_as_its_own_fatal_reason() {
    let failure =
        classify_gpu_route_failure("invalid CUDA device id 99; available CUDA devices: 0, 1");

    assert_eq!(failure.kind, GpuRouteFailureKind::Fatal);
    assert_eq!(
        failure.message,
        "invalid CUDA device id 99; available CUDA devices: 0, 1"
    );
}

#[test]
fn classify_gpu_route_failure_does_not_relabel_invalid_device_id_as_init_failure() {
    let failure =
        classify_gpu_route_failure("invalid CUDA device id 3; available CUDA devices: 0, 1, 2");

    assert!(
        !failure.message.starts_with("CUDA initialization failed"),
        "message={}",
        failure.message
    );
}

#[test]
fn classify_gpu_route_failure_still_labels_genuine_init_failures_as_such() {
    // Baseline: the pre-existing "CUDA initialization failed:" arm must keep working --
    // the new invalid-device-id arm must not swallow unrelated Fatal messages.
    let failure = classify_gpu_route_failure("CUDA initialization failed: driver too old");

    assert_eq!(failure.kind, GpuRouteFailureKind::Fatal);
    assert_eq!(
        failure.message,
        "CUDA initialization failed: driver too old"
    );
}

// NIT-3 (#172): `gpu_fatal_native_error_kind` is the thin, pure, directly-testable string
// check the two `exit_structured_search_error_if_needed` emission sites now call instead of
// hardcoding "gpu_fatal" -- these pin its two branches directly, independent of the enum
// `kind` (both messages below classify as `GpuRouteFailureKind::Fatal`; only the WIRE error
// string differs).
#[test]
fn gpu_fatal_native_error_kind_distinguishes_invalid_device_id() {
    assert_eq!(
        gpu_fatal_native_error_kind("invalid CUDA device id 99; available CUDA devices: 0, 1"),
        "gpu_invalid_device_id"
    );
    assert_eq!(
        gpu_fatal_native_error_kind("CUDA initialization failed: driver too old"),
        "gpu_fatal"
    );
}

#[test]
fn gpu_cpu_fallback_unhonorable_flag_detects_each_of_the_three() {
    let patterns = vec!["needle".to_string()];

    let params = gpu_params_for_patterns(&patterns);
    assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);

    let mut params = gpu_params_for_patterns(&patterns);
    params.max_filesize = Some("10M".to_string());
    assert_eq!(
        gpu_cpu_fallback_unhonorable_flag(&params),
        Some("--max-filesize")
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.color = Some("always".to_string());
    assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), Some("--color"));

    // N3: `--color never`/`auto` are honorable no-ops on the plain-text native engine (mirrors
    // `index_flag_violations`) -- they must NOT trigger the rg redirect. Only `always` (or an
    // unrecognized value) does.
    let mut params = gpu_params_for_patterns(&patterns);
    params.color = Some("never".to_string());
    assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
    params.color = Some("auto".to_string());
    assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
    params.color = Some("bogus".to_string());
    assert_eq!(
        gpu_cpu_fallback_unhonorable_flag(&params),
        Some("--color"),
        "an unrecognized --color value is still unhonorable (fail closed)"
    );

    let mut params = gpu_params_for_patterns(&patterns);
    params.no_ignore_vcs = true;
    assert_eq!(
        gpu_cpu_fallback_unhonorable_flag(&params),
        Some("--no-ignore-vcs")
    );

    // `replace`/`only_matching` ARE honorable by `NativeSearchConfig` (see
    // `native_search_config_for_gpu_params`) -- they must NOT trip this redirect-or-refuse
    // gate, or a plain CPU-fallback search would wrongly detour through rg / refuse.
    let mut params = gpu_params_for_patterns(&patterns);
    params.replace = Some("R".to_string());
    params.only_matching = true;
    assert_eq!(gpu_cpu_fallback_unhonorable_flag(&params), None);
}

#[test]
fn ripgrep_args_for_gpu_params_carries_every_previously_dropped_flag() {
    let patterns = vec!["needle".to_string(), "second".to_string()];
    let mut params = gpu_params_for_patterns(&patterns);
    params.path = "src";
    params.path_was_implicit = false;
    params.line_number = false; // -N / --no-line-number
    params.max_filesize = Some("10M".to_string());
    params.color = Some("always".to_string());
    params.no_ignore_vcs = true;
    params.replace = Some("R".to_string());
    params.only_matching = true;
    params.context = Some(3); // M1: -C 3 must survive the rg redirect too

    let rg_args = ripgrep_args_for_gpu_params(&params);

    assert_eq!(
        rg_args.patterns,
        vec!["needle".to_string(), "second".to_string()]
    );
    assert_eq!(rg_args.paths, vec!["src".to_string()]);
    assert!(
        !rg_args.line_number,
        "-N must suppress line numbers on the redirect path too"
    );
    assert!(rg_args.no_line_number);
    assert_eq!(rg_args.max_filesize.as_deref(), Some("10M"));
    assert_eq!(rg_args.color.as_deref(), Some("always"));
    assert!(rg_args.no_ignore_vcs);
    assert_eq!(rg_args.replace.as_deref(), Some("R"));
    assert!(rg_args.only_matching);
    // M1 (gate must-fix): the rg redirect must not silently drop -C/-A/-B context. Pre-fix
    // this field defaulted to None => zero context lines, exit 0 = silent-wrong-output.
    assert_eq!(
        rg_args.context,
        Some(3),
        "-C 3 must survive the GPU-fallback rg redirect (M1)"
    );
}

#[test]
fn positional_gpu_path_defaults_line_number_off_aligned_with_native() {
    // N2 (task #131 F3): `tg PAT --gpu-device-ids N` with no -n/-N derives line_number via
    // `cli.line_number && !cli.no_line_number` at both GpuSearchParams construction sites in
    // `run_positional_cli`, replacing the old hardcoded `line_number: true`. Because
    // `PositionalCli::line_number` is `#[arg(short='n')]` (default false), that is a
    // user-visible flip to line-numbers-OFF -- a DELIBERATE alignment with the sibling
    // `native_search_config_for_positional` (the CPU fallback it delegates to), which derives
    // the identical expression. This test locks both the default-off value and the alignment.
    let cli = parse_positional_cli(&["tg", "PATTERN"]);
    let derived_line_number = cli.line_number && !cli.no_line_number;
    assert!(
        !derived_line_number,
        "positional GPU path must default line_number OFF with no -n/-N (N2)"
    );

    // The alignment target: native_search_config_for_positional derives the same value from
    // the same cli. If a future edit diverges the two paths, this assert fails.
    let native = native_search_config_for_positional(
        &cli,
        "PATTERN",
        &[".".to_string()],
        RoutingDecision::native_cpu_gpu_fallback(false, false),
    );
    assert_eq!(
        native.line_number, derived_line_number,
        "positional GPU line_number must stay aligned with native_search_config_for_positional"
    );

    // -n flips it back on (both paths honor the explicit request).
    let cli_n = parse_positional_cli(&["tg", "-n", "PATTERN"]);
    assert!(
        cli_n.line_number && !cli_n.no_line_number,
        "explicit -n must turn line numbers back on"
    );
}

#[test]
fn gpu_sidecar_search_payload_carries_every_previously_dropped_flag() {
    // Primary RED test (per the build spec): `handle_gpu_sidecar_search`'s JSON payload used
    // to omit all 6 of these keys entirely, because `GpuSearchParams` had nowhere to read
    // them from.
    let patterns = vec!["needle".to_string()];
    let mut params = gpu_params_for_patterns(&patterns);
    params.replace = Some("REPLACED".to_string());
    params.only_matching = true;
    params.max_filesize = Some("10M".to_string());
    params.color = Some("always".to_string());
    params.no_ignore_vcs = true;
    params.line_number = false; // -N

    let payload = gpu_sidecar_search_payload(&params);

    assert_eq!(payload["replace"], serde_json::json!("REPLACED"));
    assert_eq!(payload["only_matching"], serde_json::json!(true));
    assert_eq!(payload["max_filesize"], serde_json::json!("10M"));
    assert_eq!(payload["color"], serde_json::json!("always"));
    assert_eq!(payload["no_ignore_vcs"], serde_json::json!(true));
    assert_eq!(payload["line_number"], serde_json::json!(false));
}

/// Task #131 F3 recurrence guard. An EXHAUSTIVE destructure (no `..`) of every `SearchArgs`
/// field: the moment a new field is added to that struct, THIS FUNCTION STOPS COMPILING until
/// a human adds it here and consciously classifies it into one of:
///   - HONORED: threaded into `GpuSearchParams` (directly, or folded into a derived field
///     like `context`/`line_number`) and never silently dropped on any GPU-routed request.
///   - NO-OP: restates a default / has no observable effect on this or any other backend
///     (cross-checked against `index_flag_violations`'s own `PassthroughSafe` bucket for the
///     same flags, e.g. `ignore`/`no_hidden`/`unicode`/`messages`).
///   - MOOT-OR-UNREACHABLE: some earlier, verified gate (an unconditional early return, or
///     `route_search`'s fixed precedence order) makes this field either impossible to reach
///     the GPU branch with, or unable to change which branch is taken once there.
///   - OUT_OF_SCOPE_GAP (a REAL, pre-existing gap this PR does NOT fix): confirmed by reading
///     `search_requires_ripgrep_passthrough`/`search_prefers_ripgrep_passthrough` -- their
///     whole "hard list" is gated behind `!args.json && !args.ndjson`, so combined with
///     `--json`/`--ndjson` (and, for a few of them, combined with nothing at all -- see
///     `format`) the request CAN reach `NativeCpu`/`NativeGpu` today, and `NativeSearchConfig`
///     has no field for it either. This is a general native-engine limitation, not specific to
///     `--gpu-device-ids`, discovered while building #131 F3 and explicitly OUT OF SCOPE for
///     it -- flagged here (not silently left "safe") so it isn't lost, pending its own task.
///     P5·H2 (2026-08-08) CLOSED the count/files trio of OUT_OF_SCOPE_GAPs below
///     (`count_matches`/`files_with_matches`/`files_without_match`): the native-route
///     refusals (`validate_search_native_structured_refusals` /
///     `validate_positional_native_structured_refusals`) now HARD-REFUSE them (exit 2) before
///     the GPU/CPU branches are reached, so on the GPU branch they are re-classified
///     MOOT-OR-UNREACHABLE. Their OUT_OF_SCOPE_GAP siblings here keep that bucket.
#[cfg(test)]
pub(super) fn assert_search_args_gpu_field_classification_is_exhaustive(args: &SearchArgs) {
    let SearchArgs {
        ignore_case: _,      // HONORED
        fixed_strings: _,    // HONORED
        no_fixed_strings: _, // NO-OP (hardcoded false in every rg-passthrough builder too)
        invert_match: _,     // HONORED
        no_invert_match: _,  // NO-OP (ditto)
        count: _,            // HONORED
        count_matches: _,    // MOOT-OR-UNREACHABLE (P5·H2: the native-route gate refuses
        // this before the GPU branch is reached)
        line_number: _,    // HONORED (`line_number && !no_line_number`, task #131 F3)
        no_line_number: _, // HONORED (see line_number)
        column: _,         // OUT_OF_SCOPE_GAP
        no_column: _,      // NO-OP (moot: `column` itself is never emitted by this engine)
        replace: _,        // HONORED (task #131 F3)
        format: _,         // OUT_OF_SCOPE_GAP (only `format=="rg"` is independently
        // verified unreachable here -- forces rg passthrough
        // unconditionally; any OTHER non-`rg` value's routing was NOT
        // independently traced through the Python front door in this
        // pass, so it is bucketed as a gap rather than asserted safe)
        sort: _,                // OUT_OF_SCOPE_GAP
        sort_reverse: _,        // OUT_OF_SCOPE_GAP
        sort_files: _,          // OUT_OF_SCOPE_GAP
        null: _,                // OUT_OF_SCOPE_GAP
        null_data: _,           // OUT_OF_SCOPE_GAP
        multiline: _,           // OUT_OF_SCOPE_GAP
        multiline_dotall: _,    // OUT_OF_SCOPE_GAP
        context: _,             // HONORED (via search_effective_context)
        after_context: _,       // HONORED (folds into context, see search_effective_context)
        before_context: _,      // HONORED (ditto)
        max_count: _,           // HONORED
        max_depth: _,           // HONORED
        word_regexp: _,         // HONORED
        smart_case: _,          // HONORED
        globs: _,               // HONORED
        no_ignore: _,           // HONORED
        ignore: _,              // NO-OP (restates the no_ignore default)
        no_ignore_dot: _,       // OUT_OF_SCOPE_GAP
        no_ignore_exclude: _,   // OUT_OF_SCOPE_GAP
        no_ignore_files: _,     // OUT_OF_SCOPE_GAP
        no_ignore_global: _,    // OUT_OF_SCOPE_GAP
        no_ignore_parent: _,    // OUT_OF_SCOPE_GAP
        hidden: _,              // HONORED
        no_hidden: _,           // NO-OP (restates the default)
        follow: _,              // OUT_OF_SCOPE_GAP
        text: _,                // HONORED
        files_with_matches: _,  // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
        files_without_match: _, // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
        file_type: _,           // OUT_OF_SCOPE_GAP
        index: _,               // MOOT-OR-UNREACHABLE (route_search checks explicit_index
        // BEFORE explicit_gpu_device_ids -- TrigramIndex always wins)
        force_cpu: _, // MOOT-OR-UNREACHABLE (route_search checks
        // explicit_gpu_device_ids BEFORE force_cpu -- an explicit
        // --gpu-device-ids always wins the branch regardless of this
        // field's value, so there is nothing for it to affect here)
        gpu_device_ids: _, // HONORED (the field that selects this whole path)
        color: _,          // HONORED (task #131 F3)
        path_separator: _, // OUT_OF_SCOPE_GAP
        only_matching: _,  // HONORED (task #131 F3)
        vimgrep: _,        // OUT_OF_SCOPE_GAP
        passthru: _,       // OUT_OF_SCOPE_GAP
        json: _,           // HONORED
        ndjson: _,         // HONORED
        verbose: _,        // HONORED
        regexp: _,         // HONORED (folds into `request.patterns` -> `params.patterns`)
        pattern: _,        // HONORED (ditto)
        path: _,           // HONORED (-> params.path / params.path_was_implicit)
        pcre2: _,          // MOOT-OR-UNREACHABLE (rg available -> routes to
        // ripgrep_pcre2() before the gpu check in route_search; rg
        // unavailable -> require_ripgrep_or_exit hard-exits before
        // route_search is even called)
        auto_hybrid_regex: _, // OUT_OF_SCOPE_GAP
        unicode: _,           // NO-OP (restates the Unicode-mode default)
        pcre2_unicode: _,     // NO-OP (alias of unicode; same reasoning)
        max_filesize: _,      // HONORED (task #131 F3)
        no_ignore_vcs: _,     // HONORED (task #131 F3)
        require_git: _,       // OUT_OF_SCOPE_GAP
        messages: _,          // NO-OP (restates the default; no diagnostic-message mode here)
        no_config: _,         // NO-OP (this backend never reads an rg config file either)
        pcre2_version: _,     // MOOT-OR-UNREACHABLE (early return at the very top of
        // handle_ripgrep_search, before any routing)
        type_list: _,                  // MOOT-OR-UNREACHABLE (ditto)
        version: _,                    // MOOT-OR-UNREACHABLE (ditto)
        files: _,                      // MOOT-OR-UNREACHABLE (ditto)
        allow_broad_generated_scan: _, // MOOT-OR-UNREACHABLE (ditto)
    } = args;
}

#[test]
fn search_args_gpu_field_classification_covers_a_real_parsed_instance() {
    // Exercising the exhaustive destructure against a real clap-parsed value keeps this test
    // from being vacuous; the actual recurrence-guard value is the destructure compiling at
    // all (see the function's doc comment).
    let args = parse_search_args(&["tg", "search", "PATTERN"]);
    assert_search_args_gpu_field_classification_is_exhaustive(&args);
}

/// Sibling guard for `PositionalCli` (the `tg PATTERN` front door). Every one of its fields is
/// name-identical to a `SearchArgs` field above (verified by inspection when this was written)
/// with the same meaning, so the same classification applies; `run_positional_cli` has no
/// `search_requires_ripgrep_passthrough`-equivalent gate at all, so its OUT_OF_SCOPE_GAP
/// fields are reachable via `--gpu-device-ids` unconditionally (not only combined with
/// `--json`/`--ndjson` as for `SearchArgs`) -- a strictly broader exposure of the same
/// pre-existing, out-of-scope gap. P5·H2 (2026-08-08) closed the `count_matches` member of
/// that gap: `validate_positional_native_structured_refusals` now HARD-REFUSES it (exit 2)
/// at the top of both native positional arms (the structured doors and, unconditionally, the
/// `--gpu-device-ids` door), so on the GPU branch it is MOOT-OR-UNREACHABLE.
#[cfg(test)]
pub(super) fn assert_positional_cli_gpu_field_classification_is_exhaustive(cli: &PositionalCli) {
    let PositionalCli {
        pattern: _,           // HONORED
        path: _,              // HONORED
        count: _,             // HONORED
        count_matches: _,     // MOOT-OR-UNREACHABLE (P5·H2: refused before this branch)
        line_number: _,       // HONORED (task #131 F3)
        no_line_number: _,    // HONORED (task #131 F3)
        column: _,            // OUT_OF_SCOPE_GAP
        max_count: _,         // HONORED
        fixed_strings: _,     // HONORED
        invert_match: _,      // HONORED
        ignore_case: _,       // HONORED
        word_regexp: _,       // HONORED
        replace: _,           // HONORED (task #131 F3)
        force_cpu: _,         // MOOT-OR-UNREACHABLE (see the SearchArgs bucket above)
        gpu_device_ids: _,    // HONORED
        color: _,             // HONORED (task #131 F3)
        path_separator: _,    // OUT_OF_SCOPE_GAP
        only_matching: _,     // HONORED (task #131 F3)
        vimgrep: _,           // OUT_OF_SCOPE_GAP
        json: _,              // HONORED
        ndjson: _,            // HONORED
        verbose: _,           // HONORED
        pcre2: _,             // MOOT-OR-UNREACHABLE (see the SearchArgs bucket above)
        auto_hybrid_regex: _, // OUT_OF_SCOPE_GAP
        unicode: _,           // NO-OP
        pcre2_unicode: _,     // NO-OP
        max_filesize: _,      // HONORED (task #131 F3)
        no_ignore: _,         // HONORED
        ignore: _,            // NO-OP
        messages: _,          // NO-OP
        require_git: _,       // OUT_OF_SCOPE_GAP
        no_hidden: _,         // NO-OP
        no_ignore_vcs: _,     // HONORED (task #131 F3)
    } = cli;
}

#[test]
fn positional_cli_gpu_field_classification_covers_a_real_parsed_instance() {
    let cli = parse_positional_cli(&["tg", "PATTERN"]);
    assert_positional_cli_gpu_field_classification_is_exhaustive(&cli);
}

/// P5·H2 coverage ratchet (modeled on `assert_search_args_gpu_field_classification_is_exhaustive`
/// above). An EXHAUSTIVE destructure (no `..`) of every `SearchArgs` field: adding a field to
/// that struct stops THIS function from compiling until a human classifies it for the NATIVE
/// STRUCTURED route (`--json`/`--ndjson` -> `BackendSelection::NativeCpu`/`NativeGpu`, minus
/// the `--format rg --json` passthrough that `search_requires_ripgrep_passthrough` honors).
/// Disposition bucket per field, for that route specifically:
///   - HONORED: threaded into `NativeSearchConfig` / the structured emitter (or, for
///     `format`/`pcre2`-class fields, routed to an rg passthrough that carries them).
///   - HARD-REFUSED: THIS PR's class -- the native engine silently dropped it pre-fix
///     (`native_structed_dropped_search_flags` now lists it and the native arms exit 2).
///   - NO-OP: restates a default the native engine already behaves as on this route.
///   - MOOT-OR-UNREACHABLE: an earlier, verified gate (early return or routing precedence)
///     makes it impossible to reach a native structured branch, or impossible to matter once
///     there.
///   - IRRELEVANT-TO-THIS-CLASS: not a native-engine drop; its disposition is the GPU/plain-
///     text ratchets' business (cross-referenced, this function does not re-litigate it).
#[cfg(test)]
pub(super) fn assert_search_args_native_structured_field_classification_is_exhaustive(
    args: &SearchArgs,
) {
    let SearchArgs {
        ignore_case: _,      // HONORED (NativeSearchConfig)
        fixed_strings: _,    // HONORED
        no_fixed_strings: _, // NO-OP (hardcoded false in every rg-passthrough builder too)
        invert_match: _,     // HONORED
        no_invert_match: _,  // NO-OP (ditto)
        count: _,            // HONORED (line-granular contract, credentialed for -c)
        count_matches: _,    // HARD-REFUSED (P5·H2 -- THIS PR)
        line_number: _,      // HONORED
        no_line_number: _,   // HONORED (see line_number)
        column: _,           // IRRELEVANT-TO-THIS-CLASS (neither a silent native drop fix nor
        // an honored native field; see the GPU ratchet's OUT_OF_SCOPE_GAP)
        no_column: _, // NO-OP (moot: `column` is never emitted by this engine)
        replace: _,   // HONORED (task #131 F3)
        format: _,    // ROUTE SELECTOR: `"rg"` => honored rg passthrough (carries the
        // refused flags through `command_ripgrep_args`, so it is excluded from the refusal
        // predicate); any other value reaches native structured only via the json gate and is
        // not a silent drop of THIS class (no native field for it to drop)
        sort: _,                // IRRELEVANT-TO-THIS-CLASS (GPU ratchet OUT_OF_SCOPE_GAP)
        sort_reverse: _,        // IRRELEVANT-TO-THIS-CLASS
        sort_files: _,          // IRRELEVANT-TO-THIS-CLASS
        null: _,                // IRRELEVANT-TO-THIS-CLASS
        null_data: _,           // IRRELEVANT-TO-THIS-CLASS
        multiline: _,           // IRRELEVANT-TO-THIS-CLASS
        multiline_dotall: _,    // IRRELEVANT-TO-THIS-CLASS
        context: _,             // HONORED
        after_context: _,       // HONORED
        before_context: _,      // HONORED
        max_count: _,           // HONORED
        max_depth: _,           // HONORED
        word_regexp: _,         // HONORED
        smart_case: _,          // HONORED
        globs: _,               // HONORED
        no_ignore: _,           // HONORED
        ignore: _,              // NO-OP (restates the no_ignore default)
        no_ignore_dot: _,       // IRRELEVANT-TO-THIS-CLASS
        no_ignore_exclude: _,   // IRRELEVANT-TO-THIS-CLASS
        no_ignore_files: _,     // IRRELEVANT-TO-THIS-CLASS
        no_ignore_global: _,    // IRRELEVANT-TO-THIS-CLASS
        no_ignore_parent: _,    // IRRELEVANT-TO-THIS-CLASS
        hidden: _,              // HONORED
        no_hidden: _,           // NO-OP
        follow: _,              // IRRELEVANT-TO-THIS-CLASS
        text: _,                // HONORED
        files_with_matches: _,  // HARD-REFUSED (P5·H2 -- THIS PR)
        files_without_match: _, // HARD-REFUSED (P5·H2 -- THIS PR)
        file_type: _,           // IRRELEVANT-TO-THIS-CLASS
        index: _,               // MOOT-OR-UNREACHABLE (route_search checks explicit_index
        // BEFORE json/ndjson -> the index path, which itself Refuses count/files flags
        // (IndexFlagPolicy::Refuse) with its own message; a structured index search never
        // reaches these native arms)
        force_cpu: _, // MOOT-OR-UNREACHABLE (structured output + force_cpu routes to
        // native_cpu_force, whose arms below refuse identically; a structured request can
        // never be served exclusively by rg)
        gpu_device_ids: _, // HONORED (the field that selects the whole path; refusal predicate
        // fires on the positional twin for it, see below)
        color: _,          // HONORED (task #131 F3)
        path_separator: _, // IRRELEVANT-TO-THIS-CLASS
        only_matching: _,  // HONORED (task #131 F3 -- do NOT extend the refusal to `-o`)
        vimgrep: _,        // IRRELEVANT-TO-THIS-CLASS
        passthru: _,       // IRRELEVANT-TO-THIS-CLASS
        json: _,           // ROUTE SELECTOR (native structured trigger; honored as the
        // emitter mode -- the refusal predicate makes the *combination* json + refused flag
        // exit 2, never both silently)
        ndjson: _,  // ROUTE SELECTOR (ditto)
        verbose: _, // HONORED
        regexp: _,  // HONORED
        pattern: _, // HONORED
        path: _,    // HONORED
        pcre2: _,   // MOOT-OR-UNREACHABLE (rg available -> routed to ripgrep_pcre2
        // before the native branches; rg unavailable -> require_ripgrep_or_exit hard-exits)
        auto_hybrid_regex: _, // IRRELEVANT-TO-THIS-CLASS
        unicode: _,           // NO-OP (restates the Unicode default)
        pcre2_unicode: _,     // NO-OP (alias of unicode)
        max_filesize: _,      // HONORED (task #131 F3)
        no_ignore_vcs: _,     // HONORED (task #131 F3)
        require_git: _,       // IRRELEVANT-TO-THIS-CLASS
        messages: _,          // NO-OP (this engine emits no diagnostic-message mode)
        no_config: _,         // NO-OP (this backend never reads an rg config file either)
        pcre2_version: _,     // MOOT-OR-UNREACHABLE (early return at the top of
        // handle_ripgrep_search)
        type_list: _,                  // MOOT-OR-UNREACHABLE (ditto)
        version: _,                    // MOOT-OR-UNREACHABLE (ditto)
        files: _,                      // MOOT-OR-UNREACHABLE (ditto)
        allow_broad_generated_scan: _, // MOOT-OR-UNREACHABLE (ditto)
    } = args;
}

#[test]
fn search_args_native_structured_field_classification_covers_a_real_parsed_instance() {
    // Exercising against real clap-parsed instances keeps the ratchet from being vacuous; the
    // recurrence guard is the destructure compiling at all.
    let args = parse_search_args(&["tg", "search", "PATTERN"]);
    assert_search_args_native_structured_field_classification_is_exhaustive(&args);
    let args = parse_search_args(&["tg", "search", "--json", "--count-matches", "PATTERN"]);
    assert_search_args_native_structured_field_classification_is_exhaustive(&args);
}

#[test]
fn native_structured_route_hard_refuses_count_and_files_flags() {
    // P5·H2 behavioral arm (the refusal predicate): pre-fix, `search_requires_ripgrep_passthrough`'s
    // hard-flag list is gated behind !json&&!ndjson, so on `--json`/`--ndjson` these flags fell
    // through to the native engine and were SILENTLY DROPPED (No `NativeSearchConfig` field;
    // verified live on the shipped native front door: exit 0 with a plain match list). Each
    // combinator must now resolve to the EXACT refusal set, not an empty/green ok.
    for tokens in [
        ["tg", "search", "--json", "--count-matches", "needle", "."].as_slice(),
        ["tg", "search", "--count-matches", "--json", "needle", "."].as_slice(),
        ["tg", "search", "--ndjson", "--count-matches", "needle", "."].as_slice(),
    ] {
        let args = parse_search_args(tokens);
        assert_eq!(
            native_structured_dropped_search_flags(&args),
            vec!["--count-matches"],
            "structured native route must list --count-matches as refused: {tokens:?}"
        );
    }
    let args = parse_search_args(&[
        "tg",
        "search",
        "--json",
        "--files-with-matches",
        "--files-without-match",
        "needle",
        ".",
    ]);
    assert_eq!(
        native_structured_dropped_search_flags(&args),
        vec!["--files-with-matches", "--files-without-match"],
        "both files flags must be listed, in argv order"
    );
    let args = parse_search_args(&["tg", "search", "--json", "needle", "."]);
    assert!(
        native_structured_dropped_search_flags(&args).is_empty(),
        "a plain --json search must NOT be refused"
    );
}

/// TG-007: the file-SET trio changes WHICH files are searched; each was accepted then ignored.
#[test]
fn native_structured_route_hard_refuses_file_set_flags() {
    for (tokens, expected) in [
        (
            ["tg", "search", "--json", "-t", "py", "n", "."].as_slice(),
            "--type",
        ),
        (
            ["tg", "search", "--ndjson", "--type", "py", "n", "."].as_slice(),
            "--type",
        ),
        (
            ["tg", "search", "--json", "--follow", "n", "."].as_slice(),
            "--follow",
        ),
        (
            ["tg", "search", "--json", "--max-filesize", "1", "n", "."].as_slice(),
            "--max-filesize",
        ),
    ] {
        assert_eq!(
            native_structured_dropped_search_flags(&parse_search_args(tokens)),
            vec![expected],
            "file-set flag must be refused on the structured route: {tokens:?}"
        );
    }
}
#[test]
fn format_rg_json_passthrough_stays_honored_not_refused() {
    // `--format rg --json` is an rg PASSTHROUGH (search_requires_ripgrep_passthrough returns
    // true), and `command_ripgrep_args` carries count/files flags into rg's own argv. The
    // refusal predicate must NOT fire there, or it would break a currently-honored route.
    let args = parse_search_args(&[
        "tg",
        "search",
        "--format",
        "rg",
        "--json",
        "--count-matches",
        "needle",
        ".",
    ]);
    assert!(
        search_requires_ripgrep_passthrough(&args),
        "--format rg --json must stay an rg passthrough (not native)"
    );
    assert!(
        native_structured_dropped_search_flags(&args).is_empty(),
        "--format rg --json + --count-matches is honored via rg passthrough -- never refused"
    );
}

#[test]
fn non_json_count_matches_is_not_refused_by_the_structured_gate() {
    // The ALREADY-FINE path (locked by bootstrap test_rust_first_count_matches_refuses_via_native_self_guard):
    // without --json/--ndjson, `search_requires_ripgrep_passthrough`'s non-json hard list makes
    // count/files flags route to rg (honored) or self-refuse when rg is missing. The new refusal
    // predicate must keep its hands off, or it would double-refuse a route a later gate already
    // owns correctly.
    let args = parse_search_args(&["tg", "search", "--count-matches", "needle", "."]);
    assert!(
        search_requires_ripgrep_passthrough(&args),
        "non-json --count-matches still requires rg passthrough"
    );
    assert!(native_structured_dropped_search_flags(&args).is_empty());
}

#[test]
fn positional_gpu_and_structured_doors_hard_refuse_count_matches() {
    // P5·H2 positional twin: `run_positional_cli` has no json/ndjson gate, so `--count-matches`
    // reaches the native engine via the structured doors AND, unconditionally, the explicit
    // `--gpu-device-ids` door (`native_search_config_for_positional` maps `count`, not
    // `count_matches`). The predicate must name it there.
    let cli = parse_positional_cli(&[
        "tg",
        "needle",
        ".",
        "--gpu-device-ids",
        "0",
        "--count-matches",
    ]);
    assert_eq!(
        positional_native_dropped_search_flags(&cli),
        vec!["--count-matches"],
        "positional --gpu-device-ids + --count-matches is a native silent drop -> refuse"
    );
    let cli = parse_positional_cli(&["tg", "needle", ".", "--json", "--count-matches"]);
    assert_eq!(
        positional_native_dropped_search_flags(&cli),
        vec!["--count-matches"],
        "positional --json + --count-matches is a native silent drop -> refuse"
    );
    let cli = parse_positional_cli(&["tg", "needle", ".", "--count-matches"]);
    assert!(
        positional_native_dropped_search_flags(&cli).is_empty(),
        "bare positional --count-matches routes to the Ripgrep arm (front-door-shielded); \
         not this gate's class"
    );
    let cli = parse_positional_cli(&["tg", "needle", ".", "--gpu-device-ids", "0"]);
    assert!(
        positional_native_dropped_search_flags(&cli).is_empty(),
        "no flag, no refuse"
    );
}

#[test]
fn only_matching_and_count_stay_honored_not_refused() {
    // Scoped-out must stay honored: `-o`/`--only-matching` is carried by NativeSearchConfig
    // (task #131 F3) and `-c`/`--count` has the line-count contract the engine provides. They
    // must never join the refusal set.
    let args = parse_search_args(&["tg", "search", "--json", "-o", "-c", "needle", "."]);
    assert!(native_structured_dropped_search_flags(&args).is_empty());
}

#[test]
fn native_structured_refusal_validator_returns_refusal_set() {
    // P5·H2 audit Finding 3: the PREDICATE tests above inspect `native_structured_dropped_search_flags`
    // directly, so deleting every validator CALL SITE would leave them green. These assert the
    // refusers themselves -- the functions the (source-wired) call sites run -- return the
    // exact refusal set for each newly-refused combo, and None for every honored one. The
    // validators are PURE (they return the set instead of exiting), so this runs in-process
    // without `exit(2)` killing the test binary; the exit is applied only at the call sites,
    // covered end-to-end by `rust_core/tests/test_h2_native_structured_refusal.rs` against
    // the real built `tg` binary (CARGO_BIN_EXE_tg) plus this in-process grid.
    let cases: &[(&[&str], Option<Vec<&'static str>>)] = &[
        // search-command structured doors
        (
            &["tg", "search", "--json", "--count-matches", "needle", "."],
            Some(vec!["--count-matches"]),
        ),
        (
            &["tg", "search", "--ndjson", "--count-matches", "needle", "."],
            Some(vec!["--count-matches"]),
        ),
        (
            &[
                "tg",
                "search",
                "--json",
                "--files-with-matches",
                "--files-without-match",
                "needle",
                ".",
            ],
            Some(vec!["--files-with-matches", "--files-without-match"]),
        ),
        // honored search-command routes
        (&["tg", "search", "--json", "needle", "."], None),
        (&["tg", "search", "--count-matches", "needle", "."], None),
        // positional doors
        (
            &[
                "tg",
                "needle",
                ".",
                "--gpu-device-ids",
                "0",
                "--count-matches",
            ],
            Some(vec!["--count-matches"]),
        ),
        (
            &["tg", "needle", ".", "--json", "--count-matches"],
            Some(vec!["--count-matches"]),
        ),
        // positional honored routes
        (&["tg", "needle", ".", "--count-matches"], None),
        (&["tg", "needle", ".", "--gpu-device-ids", "0"], None),
    ];
    for (tokens, expected) in cases {
        if tokens[1..].contains(&"search") {
            let args = parse_search_args(tokens);
            assert_eq!(
                validate_search_native_structured_refusals(&args).as_ref(),
                expected.as_ref(),
                "search validator mismatch for {tokens:?}"
            );
        } else {
            let cli = parse_positional_cli(tokens);
            assert_eq!(
                validate_positional_native_structured_refusals(&cli).as_ref(),
                expected.as_ref(),
                "positional validator mismatch for {tokens:?}"
            );
        }
    }
}

#[test]
fn rg_passthrough_gpu_dropped_search_flags_returns_count_files_only_with_gpu() {
    // P5·H2 extension predicate (audit/h2 follow-up): the rg-passthrough route honors the
    // count/files flags but has no `--gpu-device-ids` field, so the SEARCH-form refusal set is
    // non-empty ONLY when explicit GPU ids are present AND a count/files flag rides the
    // request. Pure `--count-matches`/`-l` (no gpu), gpu alone, and the native-mapped
    // `--count` stay EMPTY -- they keep their honored routes. This is the predicate the
    // source-wired call site in `handle_ripgrep_search` runs; the exit wiring is covered
    // end-to-end by `rust_core/tests/test_h2_native_structured_refusal.rs`.
    let cases: &[(&[&str], Vec<&'static str>)] = &[
        // refused: gpu + each count/files spelling, and a combined pair
        (
            &[
                "tg",
                "search",
                "--gpu-device-ids",
                "0",
                "--count-matches",
                "needle",
                ".",
            ],
            vec!["--count-matches"],
        ),
        (
            &["tg", "search", "--gpu-device-ids", "0", "-l", "needle", "."],
            vec!["--files-with-matches"],
        ),
        (
            &[
                "tg",
                "search",
                "--gpu-device-ids",
                "0",
                "--files-with-matches",
                "needle",
                ".",
            ],
            vec!["--files-with-matches"],
        ),
        (
            &[
                "tg",
                "search",
                "--gpu-device-ids",
                "0",
                "--files-without-match",
                "needle",
                ".",
            ],
            vec!["--files-without-match"],
        ),
        (
            &[
                "tg",
                "search",
                "--gpu-device-ids",
                "0",
                "--count-matches",
                "--files-without-match",
                "needle",
                ".",
            ],
            vec!["--count-matches", "--files-without-match"],
        ),
        // honored: pure count/files without gpu, gpu without a count/files flag, and --count
        (
            &["tg", "search", "--count-matches", "needle", "."],
            Vec::new(),
        ),
        (&["tg", "search", "-l", "needle", "."], Vec::new()),
        (
            &["tg", "search", "--files-without-match", "needle", "."],
            Vec::new(),
        ),
        (
            &["tg", "search", "--gpu-device-ids", "0", "needle", "."],
            Vec::new(),
        ),
        (
            &[
                "tg",
                "search",
                "--gpu-device-ids",
                "0",
                "--count",
                "needle",
                ".",
            ],
            Vec::new(),
        ),
    ];
    for (tokens, expected) in cases {
        let args = parse_search_args(tokens);
        assert_eq!(
            &rg_passthrough_gpu_dropped_search_flags(&args),
            expected,
            "predicate mismatch for {tokens:?}"
        );
    }
}

#[test]
fn validation_command_argv_keeps_malicious_path_in_one_token_no_shell_injection() {
    // A maliciously named file with shell metacharacters must land in a SINGLE argv element so a
    // direct spawn cannot interpret it as a pipeline/command-substitution (SECURITY regression).
    let argv = validation_command_argv(
        r#"python -m py_compile "$file""#,
        Some("/repo/evil; rm -rf ~/`whoami`.py"),
    );
    assert_eq!(
        argv,
        vec![
            "python".to_string(),
            "-m".to_string(),
            "py_compile".to_string(),
            "/repo/evil; rm -rf ~/`whoami`.py".to_string(),
        ]
    );
}

#[test]
fn validation_command_argv_preserves_quoted_path_with_spaces() {
    let argv =
        validation_command_argv(r#"python -m py_compile "C:\path with spaces\app.py""#, None);
    assert_eq!(
        argv,
        vec![
            "python".to_string(),
            "-m".to_string(),
            "py_compile".to_string(),
            r#"C:\path with spaces\app.py"#.to_string(),
        ]
    );
}
