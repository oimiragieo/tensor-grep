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
fn nearest_commands_is_bounded_and_deterministic() {
    let near = nearest_commands("searhc");
    assert!(near.contains(&"search".to_string()), "near={near:?}");
    assert!(near.len() <= 5);
    assert!(nearest_commands("qqqqzzzz").is_empty());
    assert_eq!(near, nearest_commands("searhc"));
}

#[test]
fn top_level_search_format_python_passthrough_args_detects_non_rg_formats() {
    let raw_args = ["tg", "--format=json", "ERROR", "bench_data"]
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
fn top_level_format_normalization_does_not_capture_known_commands() {
    let raw_args = ["tg", "classify", "--format", "json", "sample.log"]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
    assert_eq!(search_format_python_passthrough_args(&raw_args), None);
}

#[test]
fn classify_args_accepts_json_flag() {
    let cli = CommandCli::try_parse_from(["tg", "classify", "--json", "--text", "OOM"]).unwrap();
    assert!(matches!(cli.command, Commands::Classify(a) if a.json && a.format.is_none()));
}

#[test]
fn one_shot_apply_fast_path_is_only_enabled_for_safe_simple_apply() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(can_use_one_shot_apply_fast_path(&args));

    let diff = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--diff",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&diff));

    let json = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&json));

    let verify = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--verify",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&verify));

    let checkpoint = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--checkpoint",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&checkpoint));

    let audit = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--audit-manifest",
        "audit.json",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&audit));

    let selector = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--apply-edit-ids",
        "e0000:fixture.py:0:1",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&selector));

    let reject_selector = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--reject-edit-ids",
        "e0000:fixture.py:0:1",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&reject_selector));

    let validation = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--lint-cmd",
        "echo lint",
        "--test-cmd",
        "echo test",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&validation));

    let batch = parse_run_args(&[
        "tg",
        "run",
        "--batch-rewrite",
        "batch-rewrite.json",
        "--apply",
        "fixture.py",
    ]);
    assert!(!can_use_one_shot_apply_fast_path(&batch));
}

#[test]
fn simple_apply_selects_one_shot_apply_fast_path() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);

    assert_eq!(
        select_rewrite_apply_mode(&args),
        RewriteApplyMode::OneShotFastPath
    );

    let json = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--rewrite",
        "lambda $$$ARGS: $EXPR",
        "--apply",
        "--json",
        "def $F($$$ARGS): return $EXPR",
        "fixture.py",
    ]);

    assert_eq!(
        select_rewrite_apply_mode(&json),
        RewriteApplyMode::PlanThenApply
    );
}

#[test]
fn run_accepts_ast_grep_pattern_option() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "class $NAME: $$$BODY",
        "fixture.py",
    ]);

    assert_eq!(run_pattern(&args).unwrap(), "class $NAME: $$$BODY");
    // Task #26: the pair, so this test also pins the ORIGIN half. An explicit trailing PATH
    // must report `false` -- the control arm for the scope note, which fires only when the
    // caller supplied nothing.
    assert_eq!(
        run_search_path_with_origin(&args),
        ("fixture.py", false),
        "an explicit trailing PATH must not be recorded as implicit"
    );
}

#[test]
fn run_search_path_reports_the_default_as_implicit() {
    // CONTROL ARM for the test above, and the arm the scope note actually depends on. Without
    // it, hard-coding `false` for the origin would satisfy every other assertion in this file
    // while making the `--json` scope disclosure permanently silent on the AST route --
    // exactly the "fixed the route that happened to be reported" failure #26 exists to close.
    //
    // Both positional shapes, because `run_search_path_with_origin` selects a DIFFERENT
    // positional index depending on whether `--pattern` was used, and a test covering only
    // one of them would leave half the selection logic unguarded.
    let with_pattern_flag = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "class $NAME: $$$BODY",
    ]);
    assert_eq!(
        run_search_path_with_origin(&with_pattern_flag),
        (".", true),
        "no PATH after --pattern must default to `.` AND report implicit"
    );

    let positional = parse_run_args(&["tg", "run", "--lang", "python", "class $NAME: $$$BODY"]);
    assert_eq!(
        run_search_path_with_origin(&positional),
        (".", true),
        "no PATH after a positional pattern must default to `.` AND report implicit"
    );
}

// --- The main.rs JSON envelope LITERALS ------------------------------------------------
//
// These three structs had NO direct test. The only envelope with one was `NativeJsonOutput`
// over in `native_search.rs`, whose tests go through `emit_json_matches` because that emitter
// writes to a configurable `output_target`. The three here `println!` instead, so capturing
// their output means capturing process stdout -- which is why they went untested.
//
// Serialising the struct DIRECTLY tests the thing that actually matters: the omit-when-
// inapplicable shape. Every `skip_serializing_if` here is a promise that a complete,
// explicitly-scoped search stays BYTE-IDENTICAL for existing consumers, and nothing was
// checking that promise.

// Imported HERE rather than at module level: `DEFAULTED_SCOPE_NOTE` is referenced only by the
// tests below, and a top-level `use` for it would be an unused import in every non-test build
// -- which `cargo clippy -- -D warnings` turns into a CI failure.
use tensor_grep_rs::native_search::DEFAULTED_SCOPE_NOTE;

fn summary_value(summary: &SearchSummaryNdjson) -> serde_json::Value {
    serde_json::to_value(summary).expect("summary must serialize")
}

#[test]
fn ndjson_summary_omits_every_optional_field_when_nothing_is_wrong() {
    // THE BYTE-IDENTICAL PROMISE. Optional fields must be ABSENT, not present-and-null:
    // a `null` is a NEW KEY on the happy path, which is a wire change wearing a default value.
    let value = summary_value(&SearchSummaryNdjson {
        record_type: "summary",
        version: JSON_OUTPUT_VERSION,
        total_matches: 3,
        routing_backend: None,
        routing_reason: None,
        sidecar_used: None,
        requested_gpu_device_ids: None,
        routing_gpu_device_ids: None,
        gpu_evidence_status: None,
        gpu_proof: None,
        native_gpu_unavailable: None,
        not_gpu_proof_reason: None,
        binary_match_files: None,
        result_incomplete: None,
        incomplete_reason_class: None,
        incomplete_paths_count: None,
        path_was_defaulted: None,
        scope_note: None,
    });

    assert_eq!(value["type"], serde_json::json!("summary"));
    assert_eq!(value["total_matches"], serde_json::json!(3));
    for key in [
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "requested_gpu_device_ids",
        "routing_gpu_device_ids",
        "gpu_evidence_status",
        "gpu_proof",
        "native_gpu_unavailable",
        "not_gpu_proof_reason",
        "binary_match_files",
        "result_incomplete",
        "incomplete_reason_class",
        "incomplete_paths_count",
        "path_was_defaulted",
        "scope_note",
    ] {
        assert!(
            value.get(key).is_none(),
            "a complete, explicitly-scoped summary must not carry `{key}`: {value}"
        );
    }
}

#[test]
fn ndjson_summary_carries_both_disclosure_families_when_they_apply() {
    // TREATMENT, and deliberately BOTH families at once. They are independent -- an incomplete
    // walk and a defaulted scope can co-occur -- and a test that only ever sets one would pass
    // against an emitter that dropped the other.
    let value = summary_value(&SearchSummaryNdjson {
        record_type: "summary",
        version: JSON_OUTPUT_VERSION,
        total_matches: 0,
        routing_backend: Some("NativeCpuBackend"),
        routing_reason: Some("force_cpu"),
        sidecar_used: Some(false),
        requested_gpu_device_ids: Some(Vec::new()),
        routing_gpu_device_ids: Some(Vec::new()),
        gpu_evidence_status: None,
        gpu_proof: None,
        native_gpu_unavailable: None,
        not_gpu_proof_reason: None,
        binary_match_files: Some(2),
        result_incomplete: Some(true),
        incomplete_reason_class: Some("unreadable_path"),
        incomplete_paths_count: Some(2),
        path_was_defaulted: Some(true),
        scope_note: Some(DEFAULTED_SCOPE_NOTE),
    });

    assert_eq!(value["result_incomplete"], serde_json::json!(true));
    assert_eq!(value["binary_match_files"], serde_json::json!(2));
    assert_eq!(value["routing_backend"], "NativeCpuBackend");
    assert_eq!(value["routing_reason"], "force_cpu");
    assert_eq!(value["sidecar_used"], false);
    assert_eq!(value["requested_gpu_device_ids"], serde_json::json!([]));
    assert_eq!(value["routing_gpu_device_ids"], serde_json::json!([]));
    assert_eq!(
        value["incomplete_reason_class"],
        serde_json::json!("unreadable_path")
    );
    assert_eq!(value["incomplete_paths_count"], serde_json::json!(2));
    assert_eq!(value["path_was_defaulted"], serde_json::json!(true));
    assert_eq!(
        value["scope_note"],
        serde_json::json!(DEFAULTED_SCOPE_NOTE),
        "the summary must carry the SHARED note constant, not a local paraphrase"
    );
}

fn aggregate_value(
    result_incomplete: Option<bool>,
    incomplete_reason_class: Option<&'static str>,
    incomplete_paths_count: Option<usize>,
    path_was_defaulted: Option<bool>,
    scope_note: Option<&'static str>,
    binary_match_files: Option<usize>,
) -> serde_json::Value {
    let payload = SearchResultJson {
        version: JSON_OUTPUT_VERSION,
        routing_backend: "NativeCpuBackend",
        routing_reason: "native_cpu",
        sidecar_used: false,
        requested_gpu_device_ids: Vec::new(),
        routing_gpu_device_ids: Vec::new(),
        gpu_evidence_status: None,
        gpu_proof: None,
        native_gpu_unavailable: None,
        not_gpu_proof_reason: None,
        query: "needle",
        path: ".",
        total_files: 0,
        total_matches: 0,
        binary_match_files,
        matched_file_paths: Vec::new(),
        match_counts_by_file: std::collections::BTreeMap::new(),
        matches: Vec::new(),
        remediation: None,
        advisory_hints: Vec::new(),
        result_incomplete,
        incomplete_reason_class,
        incomplete_paths_count,
        path_was_defaulted,
        scope_note,
    };
    serde_json::to_value(&payload).expect("aggregate envelope must serialize")
}

#[test]
fn aggregate_envelope_omits_every_optional_field_when_nothing_is_wrong() {
    // CONTROL, and the load-bearing half: this is the shape every existing `--json` consumer
    // sees today. A key appearing here is a silent contract change.
    let value = aggregate_value(None, None, None, None, None, None);

    for key in [
        "result_incomplete",
        "incomplete_reason_class",
        "incomplete_paths_count",
        "path_was_defaulted",
        "scope_note",
        "binary_match_files",
        "gpu_evidence_status",
        "gpu_proof",
        "native_gpu_unavailable",
        "not_gpu_proof_reason",
    ] {
        assert!(
            value.get(key).is_none(),
            "a complete, explicitly-scoped, non-GPU search must not carry `{key}`: {value}"
        );
    }
    // The unconditional fields must still be there -- an "omits everything" assertion passes
    // trivially against an emitter that emits nothing at all.
    assert_eq!(
        value["routing_backend"],
        serde_json::json!("NativeCpuBackend")
    );
    assert_eq!(value["total_matches"], serde_json::json!(0));
    assert_eq!(value["query"], serde_json::json!("needle"));
}

#[test]
fn aggregate_envelope_carries_both_disclosure_families_when_they_apply() {
    let value = aggregate_value(
        Some(true),
        Some("unreadable_path"),
        Some(1),
        Some(true),
        Some(DEFAULTED_SCOPE_NOTE),
        Some(2),
    );

    assert_eq!(value["result_incomplete"], serde_json::json!(true));
    assert_eq!(
        value["incomplete_reason_class"],
        serde_json::json!("unreadable_path")
    );
    assert_eq!(value["incomplete_paths_count"], serde_json::json!(1));
    assert_eq!(value["path_was_defaulted"], serde_json::json!(true));
    assert_eq!(value["scope_note"], serde_json::json!(DEFAULTED_SCOPE_NOTE));
    assert_eq!(value["binary_match_files"], serde_json::json!(2));
}

// THE THIRD LITERAL, `GpuNativeSearchResultJson`, IS DELIBERATELY NOT TESTED HERE, and this
// comment is the honest record of why rather than a silent omission.
//
// It is `#[cfg(feature = "cuda")]`. The only job that compiles that feature is
// `cuda-feature-check`, which runs `cargo check --features cuda --all-targets` -- CHECK, not
// TEST. So a `#[cfg(feature = "cuda")] #[test]` added here would be type-checked and NEVER
// EXECUTED by any job in CI, on any runner. Checking is not running, and a test that never
// runs is worse than no test: it reports the surface as covered.
//
// Whether `cargo test --features cuda` can even LINK on a GPU-less runner is UNANSWERED (task
// #279 territory) -- I did not verify it, and cargo is CPU-forbidden on the authoring machine,
// so I am not going to claim either way. If someone establishes that it links, the two tests
// above port to it directly: the struct differs only by the `pipeline` field and the GPU proof
// quartet, and `incomplete_envelope_fields`/`defaulted_scope_fields` already feed it the same
// way they feed these.

#[test]
fn run_rejects_duplicate_pattern_forms() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "class $NAME: $$$BODY",
        "def $F(): $$$BODY",
        "fixture.py",
    ]);

    let error = run_pattern(&args).unwrap_err().to_string();
    assert!(error.contains("--pattern accepts at most one positional PATH"));
}

#[test]
fn run_files_with_matches_is_read_only() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "class $NAME: $$$BODY",
        "--files-with-matches",
        "fixture.py",
    ]);

    assert!(args.files_with_matches);
    assert!(validate_run_args(&args).is_ok());

    let rewrite = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "class $NAME: $$$BODY",
        "--files-with-matches",
        "--rewrite",
        "class $NAME: pass",
        "fixture.py",
    ]);

    let error = validate_run_args(&rewrite).unwrap_err().to_string();
    assert!(error.contains("read-only search output mode"));
}

#[test]
fn run_ast_grep_semantic_options_are_read_only_python_passthrough() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "print($A)",
        "--selector",
        "call",
        "--strictness",
        "relaxed",
        "--globs",
        "*.py",
        "fixture.py",
    ]);

    assert!(validate_run_args(&args).is_ok());
    assert!(ast_run_requires_python_passthrough(&args));

    let rewrite = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "print($A)",
        "--selector",
        "call",
        "--rewrite",
        "logger.info($A)",
        "fixture.py",
    ]);
    let error = validate_run_args(&rewrite).unwrap_err().to_string();
    assert!(error.contains("ast-grep semantic run options are read-only"));
}

#[test]
fn run_stdin_rejects_files_with_matches() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "print($A)",
        "--stdin",
        "--files-with-matches",
    ]);

    let error = validate_run_args(&args).unwrap_err().to_string();
    assert!(error.contains("--stdin cannot be combined with --files-with-matches"));
}

#[test]
fn run_files_with_matches_rejects_json() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "print($A)",
        "--files-with-matches",
        "--json",
        "fixture.py",
    ]);

    let error = validate_run_args(&args).unwrap_err().to_string();
    assert!(error.contains("--files-with-matches is a read-only text output mode"));
}

#[test]
fn run_stdin_python_passthrough_omits_default_path() {
    let args = parse_run_args(&[
        "tg",
        "run",
        "--lang",
        "python",
        "--pattern",
        "print($A)",
        "--stdin",
        "--json",
    ]);

    let passthrough = ast_run_python_passthrough_args(&args).unwrap();
    assert!(passthrough.contains(&"--stdin".to_string()));
    assert!(!passthrough.contains(&".".to_string()));
}

#[test]
fn run_semantic_options_reject_existing_path_without_pattern_option() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().to_string_lossy().to_string();
    let args = parse_run_args(&["tg", "run", "--selector", "call", &path]);

    let error = validate_run_args(&args).unwrap_err().to_string();

    assert!(
        error.contains("require --pattern <PATTERN> before PATH"),
        "{error}"
    );
}

#[test]
fn early_ripgrep_fast_path_preserves_glob_cases() {
    let spaced = parse_args(&["tg", "search", "--glob", "*.log", "ERROR", "bench_data"]);
    let equals = parse_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);

    assert!(should_use_early_ripgrep_fast_path(&spaced));
    assert!(should_use_early_ripgrep_fast_path(&equals));
    assert_eq!(spaced.globs, vec!["*.log".to_string()]);
    assert_eq!(equals.globs, vec!["*.log".to_string()]);

    let frontdoor =
        parse_default_frontdoor_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);
    assert_eq!(frontdoor.globs, vec!["*.log".to_string()]);
}

#[test]
fn frontdoor_args_record_path_was_implicit_for_e_flag_bypass() {
    // Audit #100 RED-before-fix shape: `tg search -e "TODO" --glob "*.py"` with NO explicit
    // PATH used to bypass the walk-ceiling probe entirely -- `parse_early_ripgrep_args`'s
    // `-e` arm defaulted `paths` to `["."]` with no `path_was_implicit` record at all, so no
    // caller (including this exact frontdoor) could gate on it. This pins the fix: the
    // frontdoor parser now records `path_was_implicit` correctly for the `-e` form.
    let implicit = parse_default_frontdoor_args(&["tg", "search", "-e", "TODO", "--glob", "*.py"]);
    assert!(
        implicit.path_was_implicit,
        "no PATH given via -e + --glob must record path_was_implicit = true"
    );

    let explicit = parse_default_frontdoor_args(&[
        "tg",
        "search",
        "-e",
        "TODO",
        "--glob",
        "*.py",
        "some/scoped/dir",
    ]);
    assert!(
        !explicit.path_was_implicit,
        "an explicit trailing PATH must record path_was_implicit = false"
    );

    // #105 extension: the SAME `-e` bypass shape without any --glob/--type at all must also
    // record path_was_implicit = true -- the hoisted gate no longer requires a glob/type to
    // fire, so this bare form must still surface the implicit-path signal.
    let bare_implicit = parse_default_frontdoor_args(&["tg", "search", "-e", "TODO"]);
    assert!(
        bare_implicit.path_was_implicit,
        "a bare -e with no PATH and no glob/type must still record path_was_implicit = true"
    );

    // Positional-pattern form (no -e) requires >= 2 positionals to parse at all (pattern +
    // >= 1 path), so it can never observe an implicit path through this parser.
    let positional = parse_default_frontdoor_args(&["tg", "search", "ERROR", "bench_data"]);
    assert!(
        !positional.path_was_implicit,
        "positional-pattern form always carries an explicit path when it parses at all"
    );
}

fn _make_stub_file_dir(dir: &std::path::Path, file_count: usize) {
    for index in 0..file_count {
        std::fs::write(dir.join(format!("stub_{index}.py")), "TODO placeholder\n").unwrap();
    }
}

#[test]
fn implicit_search_walk_refuses_over_ceiling_file_count() {
    // Bug #88: a bare `--glob` search with NO explicit PATH on a root whose WALK exceeds the
    // ceiling must be refused -- the exact gap that let a bare `tg search --glob X PATTERN`
    // from a large/unscoped cwd walk/search unbounded via `execute_ripgrep_search`.
    let dir = tempfile::tempdir().unwrap();
    _make_stub_file_dir(dir.path(), 1600);

    let exceeds = implicit_search_walk_exceeds_ceiling(
        &[dir.path().to_string_lossy().to_string()],
        None,
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );

    assert!(
        exceeds,
        "expected the 1600-file root's WALK to exceed the 1500 ceiling"
    );
}

#[test]
fn implicit_search_walk_allows_count_under_ceiling() {
    let dir = tempfile::tempdir().unwrap();
    _make_stub_file_dir(dir.path(), 50);

    let exceeds = implicit_search_walk_exceeds_ceiling(
        &[dir.path().to_string_lossy().to_string()],
        None,
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );

    assert!(!exceeds, "a 50-file root must not be refused");
}

#[test]
fn implicit_search_walk_counts_the_walk_not_a_selective_glob_match() {
    // The dogfood re-harvest's core finding (hypothesis 3): the hang is TREE-WALK cost, NOT
    // post-glob MATCH count. This probe counts every FILE the walker VISITS (glob-independent),
    // so a huge tree that a SELECTIVE glob would narrow to a few matches is STILL refused --
    // because the real search must still WALK the whole tree to find those few matches. Here
    // 1600 `.py` files exist but the search glob is `*.txt` (0 matches); the walk is still
    // 1600 files, which must exceed the ceiling. (The old match-count probe returned false
    // here -> proceeded -> hung; this is the RED-before case for the walk-count fix.)
    let dir = tempfile::tempdir().unwrap();
    _make_stub_file_dir(dir.path(), 1600);

    let exceeds = implicit_search_walk_exceeds_ceiling(
        &[dir.path().to_string_lossy().to_string()],
        None,
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );

    assert!(
        exceeds,
        "walk cost (1600 files) must be refused regardless of how selective the glob is"
    );
}

#[test]
fn implicit_search_walk_respects_max_depth() {
    // `--max-depth` genuinely bounds the WALK (unlike a file glob): nest 1600 files one dir
    // deep and confirm `--max-depth 1` (which never descends into them) is not refused.
    let dir = tempfile::tempdir().unwrap();
    let nested = dir.path().join("nested");
    std::fs::create_dir(&nested).unwrap();
    _make_stub_file_dir(&nested, 1600);

    let exceeds = implicit_search_walk_exceeds_ceiling(
        &[dir.path().to_string_lossy().to_string()],
        Some(1),
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );

    assert!(
        !exceeds,
        "max-depth 1 must not descend into the nested 1600-file dir"
    );
}

#[test]
fn implicit_search_walk_empty_paths_probe_is_self_bounded_no_root() {
    // Regression for the SECOND gap the re-harvest caught: `request.paths` is EMPTY (not
    // `["."]`) when stdin is readable, so the probe saw no root and skipped. The probe itself
    // returns false on genuinely-empty roots (nothing to walk); the FIX lives at the call
    // site, which normalizes an implicit empty-paths search to `["."]` before calling this.
    // This test pins the probe's empty-roots contract so a future refactor cannot make it
    // panic or scan the process cwd unexpectedly.
    let exceeds = implicit_search_walk_exceeds_ceiling(
        &[],
        None,
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );
    assert!(!exceeds, "no roots -> nothing to walk -> not refused");
}

#[test]
fn implicit_search_walk_only_fires_when_path_implicit() {
    // Non-regression (Trap #3 parity): an EXPLICIT path combined with --glob over the ceiling
    // must NOT be refused -- callers gate the probe on `request.path_was_implicit`. Verified
    // directly here since `handle_ripgrep_search` spawns a real rg subprocess and is not
    // unit-testable in-process. The probe WOULD flag this root; the `path_was_implicit` gate
    // at the call site is the only thing that lets an explicit-path glob search proceed.
    let dir = tempfile::tempdir().unwrap();
    _make_stub_file_dir(dir.path(), 1600);
    let path_str = dir.path().to_string_lossy().to_string();

    let request = ResolvedSearchRequest {
        patterns: vec!["TODO".to_string()],
        paths: vec![path_str.clone()],
        path_was_implicit: false,
    };
    assert!(!request.path_was_implicit);

    let exceeds = implicit_search_walk_exceeds_ceiling(
        &request.paths,
        None,
        false,
        false,
        IMPLICIT_SEARCH_WALK_FILE_CEILING,
    );
    assert!(
        exceeds,
        "sanity: the fixture itself exceeds the ceiling, so only the path_was_implicit gate protects it"
    );
}

#[test]
fn early_ripgrep_fast_path_rejects_fixed_and_word_cases() {
    let glob = parse_args(&["tg", "search", "--glob=*.log", "ERROR", "bench_data"]);
    let fixed = parse_args(&["tg", "search", "-F", "[ERROR]", "bench_data"]);
    let word = parse_args(&["tg", "search", "-w", "timeout", "bench_data"]);

    assert!(should_use_early_ripgrep_fast_path(&glob));
    assert!(!should_use_early_ripgrep_fast_path(&fixed));
    assert!(!should_use_early_ripgrep_fast_path(&word));
}

#[test]
fn early_ripgrep_fast_path_keeps_plain_benchmark_shapes() {
    let simple = parse_args(&["tg", "search", "ERROR", "bench_data"]);
    let regex = parse_args(&["tg", "search", "ERROR.*timeout", "bench_data"]);
    let count = parse_args(&["tg", "search", "-c", "ERROR", "bench_data"]);
    let context = parse_args(&["tg", "search", "-C", "2", "CRITICAL", "bench_data"]);
    let max_count = parse_args(&["tg", "search", "-m", "10", "ERROR", "bench_data"]);
    assert!(should_use_early_ripgrep_fast_path(&simple));
    assert!(should_use_early_ripgrep_fast_path(&regex));
    assert!(should_use_early_ripgrep_fast_path(&count));
    assert!(should_use_early_ripgrep_fast_path(&context));
    assert!(should_use_early_ripgrep_fast_path(&max_count));
}

#[test]
fn cold_rg_shaped_modes_prefer_ripgrep_passthrough() {
    let count_args = parse_search_args(&["tg", "search", "--count", "ERROR", "bench_data"]);
    let count_request = resolve_search_request(&count_args).unwrap();
    assert!(search_prefers_ripgrep_passthrough(
        &count_args,
        &count_request,
        true
    ));

    let glob_args = parse_search_args(&["tg", "search", "--glob", "*.log", "ERROR", "bench_data"]);
    let glob_request = resolve_search_request(&glob_args).unwrap();
    assert!(search_prefers_ripgrep_passthrough(
        &glob_args,
        &glob_request,
        true
    ));

    let no_ignore_args = parse_search_args(&["tg", "search", "--no-ignore", "ERROR", "bench_data"]);
    let no_ignore_request = resolve_search_request(&no_ignore_args).unwrap();
    assert!(!search_requires_ripgrep_passthrough(&no_ignore_args));
    assert!(search_prefers_ripgrep_passthrough(
        &no_ignore_args,
        &no_ignore_request,
        true
    ));
    let no_ignore_json_args = parse_search_args(&[
        "tg",
        "search",
        "--json",
        "--no-ignore",
        "ERROR",
        "bench_data",
    ]);
    let no_ignore_json_request = resolve_search_request(&no_ignore_json_args).unwrap();
    assert!(!search_requires_ripgrep_passthrough(&no_ignore_json_args));
    assert!(!search_prefers_ripgrep_passthrough(
        &no_ignore_json_args,
        &no_ignore_json_request,
        true
    ));
    let no_ignore_ndjson_args = parse_search_args(&[
        "tg",
        "search",
        "--ndjson",
        "--no-ignore",
        "ERROR",
        "bench_data",
    ]);
    let no_ignore_ndjson_request = resolve_search_request(&no_ignore_ndjson_args).unwrap();
    assert!(!search_requires_ripgrep_passthrough(&no_ignore_ndjson_args));
    assert!(!search_prefers_ripgrep_passthrough(
        &no_ignore_ndjson_args,
        &no_ignore_ndjson_request,
        true
    ));

    let no_ignore_vcs_args =
        parse_search_args(&["tg", "search", "--no-ignore-vcs", "ERROR", "bench_data"]);
    let no_ignore_vcs_request = resolve_search_request(&no_ignore_vcs_args).unwrap();
    assert!(!search_requires_ripgrep_passthrough(&no_ignore_vcs_args));
    assert!(search_prefers_ripgrep_passthrough(
        &no_ignore_vcs_args,
        &no_ignore_vcs_request,
        true
    ));
    let no_ignore_vcs_json_args = parse_search_args(&[
        "tg",
        "search",
        "--json",
        "--no-ignore-vcs",
        "ERROR",
        "bench_data",
    ]);
    let no_ignore_vcs_json_request = resolve_search_request(&no_ignore_vcs_json_args).unwrap();
    assert!(!search_requires_ripgrep_passthrough(
        &no_ignore_vcs_json_args
    ));
    assert!(!search_prefers_ripgrep_passthrough(
        &no_ignore_vcs_json_args,
        &no_ignore_vcs_json_request,
        true
    ));

    let many_fixed_args = parse_search_args(&[
        "tg",
        "search",
        "--fixed-strings",
        "-e",
        "ERROR",
        "-e",
        "WARN",
        "bench_data",
    ]);
    let many_fixed_request = resolve_search_request(&many_fixed_args).unwrap();
    assert!(search_prefers_ripgrep_passthrough(
        &many_fixed_args,
        &many_fixed_request,
        true
    ));
    assert!(!search_prefers_ripgrep_passthrough(
        &many_fixed_args,
        &many_fixed_request,
        false
    ));

    let explicit_index =
        parse_search_args(&["tg", "search", "--index", "--count", "ERROR", "bench_data"]);
    let explicit_index_request = resolve_search_request(&explicit_index).unwrap();
    assert!(!search_prefers_ripgrep_passthrough(
        &explicit_index,
        &explicit_index_request,
        true
    ));

    let warm_index_dir = tempfile::tempdir().unwrap();
    std::fs::write(warm_index_dir.path().join(".tg_index"), b"stale").unwrap();
    let warm_index_path = warm_index_dir.path().to_str().unwrap();
    let warm_index_count =
        parse_search_args(&["tg", "search", "--count", "ERROR", warm_index_path]);
    let warm_index_request = resolve_search_request(&warm_index_count).unwrap();
    assert!(!search_prefers_ripgrep_passthrough(
        &warm_index_count,
        &warm_index_request,
        true
    ));

    let forced_cpu = parse_search_args(&[
        "tg",
        "search",
        "--cpu",
        "--fixed-strings",
        "-e",
        "ERROR",
        "-e",
        "WARN",
        "bench_data",
    ]);
    let forced_cpu_request = resolve_search_request(&forced_cpu).unwrap();
    assert!(!search_prefers_ripgrep_passthrough(
        &forced_cpu,
        &forced_cpu_request,
        true
    ));
}

#[test]
fn early_positional_ripgrep_args_parse_plain_shapes() {
    let raw_args = ["tg", "-i", "warning", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let parsed = parse_early_positional_ripgrep_args(&raw_args)
        .expect("expected early positional rg args to parse");

    assert!(parsed.ignore_case);
    assert_eq!(parsed.patterns, vec!["warning".to_string()]);
    assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
}

#[test]
fn implicit_search_paths_follow_rg_stdin_semantics() {
    assert_eq!(
        implicit_search_paths(&[], false),
        vec![".".to_string()],
        "without readable stdin, no-path searches should default to cwd"
    );
    assert!(
        implicit_search_paths(&[], true).is_empty(),
        "with readable stdin, no-path searches should let rg read stdin"
    );
    assert_eq!(
        implicit_search_paths(&["fixture.txt".to_string()], true),
        vec!["fixture.txt".to_string()],
        "explicit paths must beat piped stdin"
    );
}

#[test]
fn early_positional_ripgrep_args_parse_max_count_shape() {
    let raw_args = ["tg", "-m", "1", "warning", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let parsed = parse_early_positional_ripgrep_args(&raw_args)
        .expect("expected early positional rg max-count args to parse");

    assert_eq!(parsed.max_count, Some(1));
    assert_eq!(parsed.patterns, vec!["warning".to_string()]);
    assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
}

#[test]
fn early_positional_ripgrep_args_parse_word_regexp_shape() {
    let short_args = ["tg", "-w", "word", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let long_args = ["tg", "--word-regexp", "word", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let short = parse_early_positional_ripgrep_args(&short_args)
        .expect("expected early positional rg word-regexp args to parse");
    let long = parse_early_positional_ripgrep_args(&long_args)
        .expect("expected early positional rg long word-regexp args to parse");

    for parsed in [short, long] {
        assert!(parsed.word_regexp);
        assert_eq!(parsed.patterns, vec!["word".to_string()]);
        assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    }
}

#[test]
fn early_positional_ripgrep_args_reject_structured_and_force_cpu_shapes() {
    let structured = ["tg", "--json", "warning", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let force_cpu = ["tg", "--cpu", "warning", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(parse_early_positional_ripgrep_args(&structured).is_none());
    assert!(parse_early_positional_ripgrep_args(&force_cpu).is_none());
}
