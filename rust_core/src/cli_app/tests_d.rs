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
fn default_search_frontdoor_accepts_plain_benchmark_shapes() {
    let raw_args = ["tg", "search", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let parsed = parse_default_search_frontdoor_args(&raw_args)
        .expect("expected default search frontdoor args to parse");

    assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
    assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
    assert!(!parsed.line_number);
}

/// Thin wrapper so every assertion below stays a short, unambiguous line.
fn frontdoor_admits(argv: &[OsString], stdout_is_terminal: bool) -> bool {
    frontdoor_search_is_native_plain_text_eligible_with_terminal(argv, stdout_is_terminal)
}

fn os_argv(tokens: &[&str]) -> Vec<OsString> {
    tokens.iter().map(OsString::from).collect()
}

/// Perf lever: an admitted plain-text search (single pattern, single explicit REGULAR FILE,
/// only allow-listed flags, piped stdout) must be declined by the rg front door so it can be
/// answered in-process by the native CPU engine instead of paying for an `rg` subprocess.
#[test]
fn frontdoor_plain_text_eligibility_admits_only_the_proven_subset() {
    let corpus = tempfile::tempdir().unwrap();
    let file = corpus.path().join("a.txt");
    std::fs::write(&file, "needle alpha\n").unwrap();
    let file_arg = file.display().to_string();
    let dir_arg = corpus.path().display().to_string();

    let with_flags = |extra: &[&str], path: &str| -> Vec<OsString> {
        let mut raw = vec!["tg".to_string(), "search".to_string()];
        raw.extend(extra.iter().map(|flag| (*flag).to_string()));
        raw.push("needle".to_string());
        raw.push(path.to_string());
        raw.iter().map(OsString::from).collect()
    };

    // ADMITTED: bare, each allow-listed flag on its own, and clap's COMBINED short clusters.
    // `-in` is admitted deliberately: clap expands it to `-i -n`, both admitted, so
    // `search_args_allow_plain_text_native` admits it and this adapter MUST agree. An earlier
    // revision of this test asserted `-in` kept spawning rg, which was FALSE at system level:
    // `parse_early_ripgrep_args` rejects `-in`, so it falls through to clap and reaches
    // `route_search` regardless of what this front door decides.
    let admitted: &[&[&str]] = &[
        &[],
        &["-i"],
        &["--ignore-case"],
        &["-F"],
        &["--fixed-strings"],
        &["-w"],
        &["--word-regexp"],
        &["-n"],
        &["--line-number"],
        &["--verbose"],
        &["-in"],
        &["-iw"],
        &["-Fn"],
        &["-i", "-n"],
    ];
    for extra in admitted {
        let argv = with_flags(extra, &file_arg);
        assert!(frontdoor_admits(&argv, false), "{extra:?} must stay native");
    }

    // EXCLUDED flags: each must keep today's rg passthrough. A cluster containing ONE
    // non-admitted letter refuses as a whole.
    let excluded: &[&[&str]] = &[
        &["-c"],
        &["--count"],
        &["-v"],
        &["-o"],
        &["-N"],
        &["-S"],
        &["--smart-case"],
        &["-a"],
        &["--hidden"],
        &["-L"],
        &["-l"],
        &["--count-matches"],
        &["--column"],
        &["--vimgrep"],
        &["--passthru"],
        &["-P"],
        &["--json"],
        &["--ndjson"],
        &["--cpu"],
        &["--index"],
        &["-ic"],
        &["-vn"],
    ];
    for extra in excluded {
        let argv = with_flags(extra, &file_arg);
        assert!(!frontdoor_admits(&argv, false), "{extra:?} must use rg");
    }

    // A terminal keeps rg (heading + color layout the native engine does not reproduce).
    let on_terminal = with_flags(&[], &file_arg);
    assert!(!frontdoor_admits(&on_terminal, true));

    // A DIRECTORY path keeps rg: walking diverges on binary-file messages and file order.
    let directory = with_flags(&[], &dir_arg);
    assert!(!frontdoor_admits(&directory, false));

    // A BINARY file keeps rg: `rg` spells the notice `"\0"` while the native engine's
    // GOVERNED output contract spells it `"/0"` (see `native_can_serve_plain_text` note 5c).
    let binary = corpus.path().join("binary.bin");
    std::fs::write(&binary, b"needle\0binary tail\n").unwrap();
    let binary_argv = with_flags(&[], &binary.display().to_string());
    assert!(!frontdoor_admits(&binary_argv, false));

    // A CRLF file keeps rg: the native plain sink strips the trailing `\r` while rg keeps it
    // (note 5a). This is the divergence that would have hit routine Windows searches.
    let crlf = corpus.path().join("crlf.txt");
    std::fs::write(&crlf, b"needle alpha\r\nplain\r\n").unwrap();
    let crlf_argv = with_flags(&[], &crlf.display().to_string());
    assert!(!frontdoor_admits(&crlf_argv, false));

    // A NON-UTF-8 file keeps rg: the native plain sink is `Lossy` and substitutes U+FFFD
    // where rg writes the raw byte (note 5b) -- silent corruption.
    let latin1 = corpus.path().join("latin1.txt");
    std::fs::write(&latin1, b"caf\xe9 needle here\n").unwrap();
    let latin1_argv = with_flags(&[], &latin1.display().to_string());
    assert!(!frontdoor_admits(&latin1_argv, false));

    // A missing path keeps rg (rg owns the error message).
    let absent = corpus.path().join("absent.txt").display().to_string();
    let absent_argv = with_flags(&[], &absent);
    assert!(!frontdoor_admits(&absent_argv, false));

    // An EMPTY PATTERN keeps rg: `run_native_search` rejects it and the rg fallback net then
    // prints a `warning: native CPU search failed...` line rg never emits (note 6).
    let empty_pattern = os_argv(&["tg", "search", "", file_arg.as_str()]);
    assert!(!frontdoor_admits(&empty_pattern, false));

    // PATTERN-level refusals (note 7). The file probe cannot see these -- they are properties
    // of the pattern on an otherwise fully admitted request. Rows 1-2 are an exit-code
    // regression (rg rc=2 -> native rc=1); row 3 fails to compile and trips the extra-stderr
    // fallback warning.
    for pattern in [
        "needle\\n",
        "\\n",
        "[\\n]",
        "needle\\r\\n",
        "\\x00",
        "\\0",
        "\\u{a}",
        "needle\n",
        "[",
        "(",
        "\\Qx\\E",
        "a{500}{500}{500}",
    ] {
        let argv = os_argv(&["tg", "search", pattern, file_arg.as_str()]);
        assert!(!frontdoor_admits(&argv, false), "{pattern:?} must use rg");
    }

    // ... while an ordinary regex with escapes the native matcher handles stays admitted.
    for pattern in ["needle", "need(le|ful)", "\\bneedle\\b", "\\d+", "needle$"] {
        let argv = os_argv(&["tg", "search", pattern, file_arg.as_str()]);
        assert!(
            frontdoor_admits(&argv, false),
            "{pattern:?} must stay native"
        );
    }

    // An IMPLICIT path keeps rg: `rg needle` prints `a.txt:...` while the native engine is
    // handed the literal "." default and would print `./a.txt:...`.
    let implicit = os_argv(&["tg", "search", "needle"]);
    assert!(!frontdoor_admits(&implicit, false));

    // TWO paths keep rg (the predicate admits exactly one).
    let two_paths = os_argv(&[
        "tg",
        "search",
        "needle",
        file_arg.as_str(),
        file_arg.as_str(),
    ]);
    assert!(!frontdoor_admits(&two_paths, false));

    // `-e PATTERN FILE` is the same admitted shape by another spelling and must agree.
    let dash_e = os_argv(&["tg", "search", "-e", "needle", file_arg.as_str()]);
    assert!(frontdoor_admits(&dash_e, false));

    // Two `-e` patterns refuse (the predicate admits exactly one).
    let two_e = os_argv(&["tg", "search", "-e", "a", "-e", "b", file_arg.as_str()]);
    assert!(!frontdoor_admits(&two_e, false));
}

/// The front door itself must decline the admitted shape (returning `None` hands the request
/// on to clap -> `handle_ripgrep_search` -> `route_search`, which selects the native engine).
#[test]
fn default_search_frontdoor_declines_admitted_plain_text_file_search() {
    let corpus = tempfile::tempdir().unwrap();
    let file = corpus.path().join("a.txt");
    std::fs::write(&file, "needle alpha\n").unwrap();

    let file_arg = file.display().to_string();
    let raw_args = os_argv(&["tg", "search", "needle", file_arg.as_str()]);

    // Guard against a terminal-attached local run, where the predicate correctly refuses.
    if frontdoor_admits(&raw_args, false) && !stdout_is_terminal() {
        assert!(parse_default_search_frontdoor_args(&raw_args).is_none());
    }
}

/// `--format rg` is an explicit demand for ripgrep's own renderer and must never be diverted.
#[test]
fn default_search_frontdoor_still_passes_explicit_rg_format_through() {
    let corpus = tempfile::tempdir().unwrap();
    let file = corpus.path().join("a.txt");
    std::fs::write(&file, "needle alpha\n").unwrap();

    let file_arg = file.display().to_string();
    let raw_args = os_argv(&[
        "tg",
        "search",
        "--format",
        "rg",
        "needle",
        file_arg.as_str(),
    ]);

    let parsed = parse_default_search_frontdoor_args(&raw_args)
        .expect("--format rg must stay on the ripgrep passthrough");
    assert_eq!(parsed.patterns, vec!["needle".to_string()]);
}

/// THE adapter-agreement gate. The raw-argv front-door adapter and the clap-side computation
/// (`plain_text_native_request_for_search`, the SAME function `handle_ripgrep_search` calls)
/// must return an identical verdict for every shape listed here.
///
/// Why this is load-bearing rather than tidiness: the front door is only ONE path into
/// `route_search`. Anything `parse_early_ripgrep_args` rejects -- a combined short (`-in`), a
/// `-w`/`-F` request (excluded by `should_use_early_ripgrep_fast_path`), an unknown token --
/// falls through to clap and reaches `route_search` regardless of what the front door thinks.
/// So the real admission surface is the clap side, and any shape the front door judges
/// differently is a shape whose routing nobody actually reasoned about.
///
/// SCOPE, stated precisely so this is not read as a stronger guarantee than it is: equality
/// holds for the shapes below, NOT for every conceivable argv. Attached-value short spellings
/// (`-ie needle f`, `-eneedle f`, `-e=needle f`) are known to disagree -- the front door
/// refuses on the cluster letter `e` while clap admits. Those are all in the SAFE direction and
/// are pinned separately by
/// `plain_text_native_frontdoor_is_never_looser_than_the_clap_gate`.
#[test]
fn plain_text_native_adapters_agree_on_the_listed_shapes() {
    let corpus = tempfile::tempdir().unwrap();
    let file = corpus.path().join("a.txt");
    std::fs::write(&file, "needle alpha\n").unwrap();
    let crlf = corpus.path().join("crlf.txt");
    std::fs::write(&crlf, b"needle alpha\r\n").unwrap();
    let latin1 = corpus.path().join("latin1.txt");
    std::fs::write(&latin1, b"caf\xe9 needle here\n").unwrap();
    let binary = corpus.path().join("binary.bin");
    std::fs::write(&binary, b"needle\0tail\n").unwrap();

    let file_arg = file.display().to_string();
    let dir_arg = corpus.path().display().to_string();
    let crlf_arg = crlf.display().to_string();
    let latin1_arg = latin1.display().to_string();
    let binary_arg = binary.display().to_string();
    let absent_arg = corpus.path().join("absent.txt").display().to_string();

    let shapes: Vec<Vec<&str>> = vec![
        vec!["needle", file_arg.as_str()],
        vec!["-i", "needle", file_arg.as_str()],
        vec!["-n", "needle", file_arg.as_str()],
        vec!["-F", "needle", file_arg.as_str()],
        vec!["-w", "needle", file_arg.as_str()],
        vec!["--verbose", "needle", file_arg.as_str()],
        vec!["-in", "needle", file_arg.as_str()],
        vec!["-iw", "needle", file_arg.as_str()],
        vec!["-i", "-n", "needle", file_arg.as_str()],
        vec!["-ic", "needle", file_arg.as_str()],
        vec!["-e", "needle", file_arg.as_str()],
        vec!["-e", "needle", "-w", file_arg.as_str()],
        vec!["-e", "needle", "-e", "alpha", file_arg.as_str()],
        vec!["", file_arg.as_str()],
        vec!["needle", dir_arg.as_str()],
        vec!["needle", crlf_arg.as_str()],
        vec!["needle", latin1_arg.as_str()],
        vec!["needle", binary_arg.as_str()],
        vec!["needle", absent_arg.as_str()],
        vec!["needle", file_arg.as_str(), file_arg.as_str()],
        vec!["--", "needle", file_arg.as_str()],
        vec!["-i", "--", "needle", file_arg.as_str()],
        vec!["needle"],
        // Refusal note 7: patterns rg rejects outright, and patterns that do not compile.
        vec!["needle\\n", file_arg.as_str()],
        vec!["\\n", file_arg.as_str()],
        vec!["[\\n]", file_arg.as_str()],
        vec!["needle\\r\\n", file_arg.as_str()],
        vec!["\\x00", file_arg.as_str()],
        vec!["[", file_arg.as_str()],
        vec!["(", file_arg.as_str()],
        vec!["\\Qx\\E", file_arg.as_str()],
        vec!["a{500}{500}{500}", file_arg.as_str()],
        vec!["-F", "needle\\n", file_arg.as_str()],
        // A REAL newline byte in the pattern, not the two-character escape.
        vec!["needle\n", file_arg.as_str()],
        vec!["-c", "needle", file_arg.as_str()],
        vec!["-v", "needle", file_arg.as_str()],
        vec!["-N", "needle", file_arg.as_str()],
        vec!["-S", "needle", file_arg.as_str()],
        vec!["--json", "needle", file_arg.as_str()],
        vec!["--ndjson", "needle", file_arg.as_str()],
        vec!["--cpu", "needle", file_arg.as_str()],
    ];

    for shape in shapes {
        let mut argv = vec!["tg", "search"];
        argv.extend(shape.iter().copied());

        let raw_args = os_argv(&argv);
        let frontdoor = frontdoor_admits(&raw_args, false);

        let args = parse_search_args(&argv);
        let request = resolve_search_request_with_stdin(&args, false)
            .expect("expected the shape to resolve into a search request");
        let facts = plain_text_native_request_for_search(&args, &request, false);
        let clap_side = native_can_serve_plain_text(&facts);

        assert_eq!(
            frontdoor, clap_side,
            "adapters disagree on {shape:?}: frontdoor={frontdoor} clap={clap_side}"
        );
    }
}

/// The general invariant, weaker than equality but true for ALL argv: the front door may be
/// STRICTER than the clap gate, never looser. A front-door refusal on an admitted shape costs
/// at most one `rg` spawn (and for these shapes not even that -- `parse_early_ripgrep_args`
/// rejects them first, so the request reaches clap and routes native anyway). A front-door
/// ADMISSION on a shape the clap gate would refuse would be a correctness bug, because the
/// front door's only action is to decline and hand off, and the clap gate then decides.
///
/// These three shapes are the known, deliberate asymmetry: attached-value short spellings,
/// where the front door refuses on the cluster letter `e` rather than reimplementing clap's
/// attached-value argv parsing. The test asserts only what matters -- the front door refuses,
/// and the invariant holds -- and deliberately does NOT assert the clap gate's verdict, which
/// is not load-bearing here and would be an unverified claim dressed up as a test.
#[test]
fn plain_text_native_frontdoor_is_never_looser_than_the_clap_gate() {
    let corpus = tempfile::tempdir().unwrap();
    let file = corpus.path().join("a.txt");
    std::fs::write(&file, "needle alpha\n").unwrap();
    let file_arg = file.display().to_string();

    let asymmetric: Vec<Vec<&str>> = vec![
        vec!["-ie", "needle", file_arg.as_str()],
        vec!["-eneedle", file_arg.as_str()],
        vec!["-e=needle", file_arg.as_str()],
    ];

    for shape in asymmetric {
        let mut argv = vec!["tg", "search"];
        argv.extend(shape.iter().copied());

        let frontdoor = frontdoor_admits(&os_argv(&argv), false);
        assert!(!frontdoor, "front door is expected to refuse {shape:?}");

        // The safe direction: refusing here is allowed. Admitting where clap refuses is not.
        let args = parse_search_args(&argv);
        let request = resolve_search_request_with_stdin(&args, false)
            .expect("expected the shape to resolve into a search request");
        let facts = plain_text_native_request_for_search(&args, &request, false);
        let clap_side = native_can_serve_plain_text(&facts);
        assert!(
            !frontdoor || clap_side,
            "front door looser than clap on {shape:?}"
        );
    }
}

/// The environment clause's semantics, tested WITHOUT mutating the process environment --
/// `env::set_var` is process-global and Rust tests run in parallel, so a test that set
/// `RIPGREP_CONFIG_PATH` would silently flip the verdict of every concurrently-running
/// eligibility test. The end-to-end proof (a real config file changing real results) lives in
/// `tests/e2e/test_native_plain_text_parity.py`, where the variable is scoped to a subprocess.
#[test]
fn rg_config_env_is_active_requires_a_set_and_non_empty_value() {
    assert!(!rg_config_env_is_active(None));
    // `rg` IGNORES an empty value, so "set" alone must not disqualify.
    assert!(!rg_config_env_is_active(Some(OsStr::new(""))));
    assert!(rg_config_env_is_active(Some(OsStr::new("/etc/rgrc"))));
    // A DANGLING path still counts: rg emits a read-failure diagnostic the native route omits.
    assert!(rg_config_env_is_active(Some(OsStr::new(
        "/nonexistent/rgrc"
    ))));
    assert_eq!(RIPGREP_CONFIG_PATH_ENV, "RIPGREP_CONFIG_PATH");
}

/// Constant <-> predicate drift guard. `PLAIN_TEXT_NATIVE_ALLOWED_FLAGS` does not DRIVE
/// `search_args_allow_plain_text_native` (that one destructures `SearchArgs`), so listing a
/// flag in the constant while the destructure still rejects it -- or vice versa -- would be
/// silent policy drift. This drives the real predicate with every constant entry and requires
/// agreement, replacing an earlier test that merely restated the constant's contents.
#[test]
fn every_allow_listed_flag_is_actually_admitted_by_the_search_args_predicate() {
    let allowed = tensor_grep_rs::routing::PLAIN_TEXT_NATIVE_ALLOWED_FLAGS;
    assert!(!allowed.is_empty());

    for &flag in allowed {
        let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
        let admitted = search_args_allow_plain_text_native(&args);
        assert!(admitted, "{flag} is allow-listed but SearchArgs rejects it");
        let token_ok = plain_text_native_flag_token_is_allowed(flag);
        assert!(
            token_ok,
            "{flag} is allow-listed but the token matcher rejects it"
        );
    }

    // And the reverse direction: a flag NOT in the constant must be rejected by both.
    for flag in ["-c", "-v", "-N", "-S", "-o", "--hidden", "--json"] {
        assert!(!allowed.contains(&flag), "{flag} must not be allow-listed");
        let token_ok = plain_text_native_flag_token_is_allowed(flag);
        assert!(!token_ok, "{flag} must be rejected by the token matcher");
        let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
        let admitted = search_args_allow_plain_text_native(&args);
        assert!(
            !admitted,
            "{flag} must be rejected by the SearchArgs predicate"
        );
    }
}

/// Breadth check on the parsed-`SearchArgs` adapter: setting ANY excluded flag must refuse.
/// (Cross-adapter equality is `plain_text_native_adapters_agree_on_every_shape`; constant
/// drift is `every_allow_listed_flag_is_actually_admitted_by_the_search_args_predicate`.)
#[test]
fn search_args_plain_text_native_adapter_matches_the_allow_list() {
    let bare = parse_search_args(&["tg", "search", "PATTERN", "file.txt"]);
    assert!(search_args_allow_plain_text_native(&bare));

    for flag in ["-i", "-F", "-w", "-n", "--verbose"] {
        let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
        let admitted = search_args_allow_plain_text_native(&args);
        assert!(admitted, "{flag} is allow-listed and must stay eligible");
    }

    for flag in [
        "-c",
        "-v",
        "-o",
        "-N",
        "-S",
        "-a",
        "-L",
        "-l",
        "--hidden",
        "--column",
        "--vimgrep",
        "--passthru",
        "--count-matches",
        "--files-without-match",
        "--json",
        "--ndjson",
        "--cpu",
        "--index",
        "--pcre2",
        "--no-ignore",
        "--sort-files",
        "--null",
        "--multiline",
        "--unicode",
        "--messages",
        "--require-git",
        "--no-config",
    ] {
        let args = parse_search_args(&["tg", "search", flag, "PATTERN", "file.txt"]);
        let admitted = search_args_allow_plain_text_native(&args);
        assert!(!admitted, "{flag} must keep spawning rg");
    }

    for (flag, value) in [
        ("-C", "2"),
        ("-A", "2"),
        ("-B", "2"),
        ("-m", "5"),
        ("-d", "1"),
        ("-g", "*.py"),
        ("-t", "py"),
        ("-r", "hit"),
        ("--color", "always"),
        ("--sort", "path"),
        ("--format", "rg"),
        ("--max-filesize", "10M"),
        ("--path-separator", "/"),
    ] {
        let tokens = ["tg", "search", flag, value, "PATTERN", "file.txt"];
        let admitted = search_args_allow_plain_text_native(&parse_search_args(&tokens));
        assert!(!admitted, "{flag} must keep spawning rg");
    }
}

#[test]
fn default_search_frontdoor_accepts_case_insensitive_and_max_count_shapes() {
    let ignore_case = ["tg", "search", "-i", "warning", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let max_count = ["tg", "search", "-m", "5", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let parsed_ignore_case = parse_default_search_frontdoor_args(&ignore_case)
        .expect("expected default search frontdoor case-insensitive args to parse");
    assert!(parsed_ignore_case.ignore_case);
    assert_eq!(parsed_ignore_case.patterns, vec!["warning".to_string()]);
    assert_eq!(parsed_ignore_case.paths, vec!["bench_data".to_string()]);

    let parsed_max_count = parse_default_search_frontdoor_args(&max_count)
        .expect("expected default search frontdoor max-count args to parse");
    assert_eq!(parsed_max_count.max_count, Some(5));
    assert_eq!(parsed_max_count.patterns, vec!["ERROR".to_string()]);
    assert_eq!(parsed_max_count.paths, vec!["bench_data".to_string()]);
}

#[test]
fn default_search_frontdoor_accepts_equals_max_count_shape() {
    let raw_args = ["tg", "search", "--max-count=5", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    let parsed = parse_default_search_frontdoor_args(&raw_args)
        .expect("expected default search frontdoor equals max-count args to parse");

    assert_eq!(parsed.max_count, Some(5));
    assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
    assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
}

#[test]
fn default_search_frontdoor_accepts_column_no_column_last_wins() {
    let raw_args = [
        "tg",
        "search",
        "--format",
        "rg",
        "--column",
        "--no-column",
        "-n",
        "-F",
        "ERROR",
        "bench_data",
    ]
    .into_iter()
    .map(OsString::from)
    .collect::<Vec<_>>();

    let parsed = parse_default_search_frontdoor_args(&raw_args)
        .expect("expected default search frontdoor column override args to parse");

    assert!(!parsed.column);
    assert!(parsed.no_column);
    assert!(parsed.line_number);
    assert!(parsed.fixed_strings);
    assert_eq!(parsed.patterns, vec!["ERROR".to_string()]);
    assert_eq!(parsed.paths, vec!["bench_data".to_string()]);
}

#[test]
fn default_search_frontdoor_rejects_structured_and_advanced_shapes() {
    let structured = ["tg", "search", "--json", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(parse_default_search_frontdoor_args(&structured).is_none());
}

#[test]
fn default_search_frontdoor_rejects_positional_cli_shapes() {
    let positional = ["tg", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let positional_count = ["tg", "-c", "ERROR", "bench_data"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert!(parse_default_search_frontdoor_args(&positional).is_none());
    assert!(parse_default_search_frontdoor_args(&positional_count).is_none());
}

#[test]
fn test_search_args_parses_replace_flag() {
    use clap::Parser;
    let args = ["tg", "search", "-r", "REPLACEMENT", "PATTERN", "path"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    let cli = CommandCli::try_parse_from(&args).expect("Failed to parse args");
    if let Commands::Search(search_args) = cli.command {
        assert_eq!(search_args.replace.as_deref(), Some("REPLACEMENT"));
    } else {
        panic!("Expected Search command");
    }

    let long_args = [
        "tg",
        "search",
        "--replace",
        "REPLACEMENT",
        "PATTERN",
        "path",
    ]
    .into_iter()
    .map(OsString::from)
    .collect::<Vec<_>>();
    let cli_long = CommandCli::try_parse_from(&long_args).expect("Failed to parse long args");
    if let Commands::Search(search_args) = cli_long.command {
        assert_eq!(search_args.replace.as_deref(), Some("REPLACEMENT"));
    } else {
        panic!("Expected Search command");
    }
}

#[test]
fn early_positional_ripgrep_args_rejects_replace_flag() {
    let replace = ["tg", "-r", "REPLACEMENT", "PATTERN", "path"]
        .into_iter()
        .map(OsString::from)
        .collect::<Vec<_>>();
    assert!(parse_early_positional_ripgrep_args(&replace).is_none());
}

// -- Audit fix #1: --index capability validator (index_flag_violations) ------------------

fn index_violations_for(tokens: &[&str]) -> Vec<&'static str> {
    let args = parse_search_args(tokens);
    let request = resolve_search_request(&args).expect("expected request to resolve");
    index_flag_violations(&args, &request)
}

/// Runtime backstop to the compile-time ratchet: `index_flag_violations` exhaustively
/// destructures `SearchArgs` (no `..`), so a NEW field already fails compilation until it's
/// added to that `match`/destructure. This test additionally guards the SEPARATE
/// `INDEX_FLAG_POLICY` documentation table (used only by the tests below) from drifting out
/// of sync with the real clap arg list -- e.g. someone satisfies the compiler by adding
/// `newfield: _` to the destructure but forgets to record its classification here.
#[test]
fn index_flag_policy_table_is_exhaustive_over_search_args_clap_ids() {
    let command = <SearchArgs as clap::Args>::augment_args(clap::Command::new("t"));
    let clap_ids: Vec<String> = command
        .get_arguments()
        .map(|arg| arg.get_id().as_str().to_string())
        .filter(|id| id != "help")
        .collect();
    assert!(
        !clap_ids.is_empty(),
        "sanity: clap should report at least one SearchArgs argument"
    );

    let missing: Vec<&String> = clap_ids
        .iter()
        .filter(|id| {
            !INDEX_FLAG_POLICY
                .iter()
                .any(|(name, _)| *name == id.as_str())
        })
        .collect();
    assert!(
        missing.is_empty(),
        "SearchArgs has clap arg(s) not classified in INDEX_FLAG_POLICY for the --index \
         capability validator (index_flag_violations): {missing:?}"
    );

    let stale: Vec<&str> = INDEX_FLAG_POLICY
        .iter()
        .map(|(name, _)| *name)
        .filter(|name| !clap_ids.iter().any(|id| id == name))
        .collect();
    assert!(
        stale.is_empty(),
        "INDEX_FLAG_POLICY has stale entries no longer present on SearchArgs: {stale:?}"
    );

    let mut seen: Vec<&str> = Vec::new();
    for (name, _) in INDEX_FLAG_POLICY {
        assert!(
            !seen.contains(name),
            "INDEX_FLAG_POLICY has a duplicate entry for {name:?}"
        );
        seen.push(name);
    }
}

#[test]
fn index_flag_violations_catches_flags_outside_the_original_six() {
    // None of these are in the original H1a 6-flag deny-list (invert_match/context/
    // max_count/word_regexp/globs/multi-pattern); before audit fix #1 they were silently
    // dropped by run_index_query the moment they reached it (combined with --json here so
    // they reach index_flag_violations at all -- see its doc comment on reachability).
    let cases: &[(&[&str], &str)] = &[
        (&["--hidden"], "-./--hidden"),
        (&["--max-depth", "2"], "-d/--max-depth"),
        (&["-t", "py"], "-t/--type"),
        (&["--sort", "path"], "--sort"),
        (&["--sortr", "path"], "--sortr"),
        (&["--sort-files"], "--sort-files"),
        (&["-o"], "-o/--only-matching"),
        (&["-r", "X"], "-r/--replace"),
        (&["--max-filesize", "10K"], "--max-filesize"),
        (&["--no-ignore-vcs"], "--no-ignore-vcs"),
        (&["--require-git"], "--require-git"),
        (&["-L"], "-L/--follow"),
        (&["-a"], "-a/--text"),
        (&["-l"], "-l/--files-with-matches"),
        (&["--files-without-match"], "--files-without-match"),
        (&["--column"], "--column"),
        (&["--count-matches"], "--count-matches"),
        (&["--vimgrep"], "--vimgrep"),
        (&["--passthru"], "--passthru"),
        (&["--null"], "-0/--null"),
        (&["--null-data"], "--null-data"),
        (&["-U"], "-U/--multiline"),
        (&["--multiline-dotall"], "--multiline-dotall"),
        (&["--path-separator", "/"], "--path-separator"),
        (&["--no-ignore-dot"], "--no-ignore-dot"),
        (&["--no-ignore-exclude"], "--no-ignore-exclude"),
        (&["--no-ignore-files"], "--no-ignore-files"),
        (&["--no-ignore-global"], "--no-ignore-global"),
        (&["--no-ignore-parent"], "--no-ignore-parent"),
        (&["--format", "text"], "--format"),
    ];
    for (extra, expected) in cases {
        let mut tokens = vec!["tg", "search", "--index", "--json"];
        tokens.extend_from_slice(extra);
        tokens.push("foo");
        tokens.push(".");
        let violations = index_violations_for(&tokens);
        assert!(
            violations.contains(expected),
            "flags {extra:?} should be refused (expected {expected:?}); got {violations:?}"
        );
    }
}

#[test]
fn index_flag_violations_allows_passthrough_safe_bundle() {
    let tokens = [
        "tg",
        "search",
        "--index",
        "--json",
        "--no-fixed-strings",
        "--no-invert-match",
        "--ignore",
        "--no-hidden",
        "--no-column",
        "--unicode",
        "--pcre2-unicode",
        "--messages",
        "--no-config",
        "--auto-hybrid-regex",
        "--color",
        "auto",
        "foo",
        ".",
    ];
    assert_eq!(
        index_violations_for(&tokens),
        Vec::<&str>::new(),
        "PassthroughSafe flags must not be refused"
    );
}

#[test]
fn index_flag_violations_color_never_and_auto_are_safe_but_always_is_refused() {
    assert!(
        index_violations_for(&["tg", "search", "--index", "--color", "never", "foo", "."])
            .is_empty()
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "--color", "auto", "foo", "."])
            .is_empty()
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "--color", "always", "foo", "."])
            .contains(&"--color"),
        "explicit --color always asks for output the index path cannot produce"
    );
}

#[test]
fn index_flag_violations_refuses_contradictory_engine_selection() {
    // fold-in (c): --index combined with an explicit alternate engine is contradictory;
    // route_search currently checks explicit_index before force_cpu/explicit_gpu_device_ids,
    // so without this check the engine flag would be silently dropped, not honored.
    assert!(
        index_violations_for(&["tg", "search", "--index", "--cpu", "foo", "."])
            .contains(&"--cpu/--force-cpu")
    );
    assert!(index_violations_for(&[
        "tg",
        "search",
        "--index",
        "--gpu-device-ids",
        "0",
        "foo",
        "."
    ])
    .contains(&"--gpu-device-ids"));
}

#[test]
fn index_flag_violations_honors_original_six_plus_no_line_number() {
    // The pre-existing H1a 6 must still be classified Refuse after the rewrite.
    assert!(
        index_violations_for(&["tg", "search", "--index", "-v", "foo", "."])
            .contains(&"-v/--invert-match")
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "-C", "2", "foo", "."])
            .contains(&"-C/-A/-B (context)")
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "-m", "1", "foo", "."])
            .contains(&"-m/--max-count")
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "-w", "foo", "."])
            .contains(&"-w/--word-regexp")
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "-g", "*.rs", "foo", "."])
            .contains(&"-g/--glob")
    );
    assert!(
        index_violations_for(&["tg", "search", "--index", "-e", "foo", "-e", "bar", "."])
            .contains(&"multiple patterns (-e)")
    );

    // Honor: -N/--no-line-number (and -n) must NOT be refused -- it's threaded into the
    // emit call (fold-in b) instead of being rejected.
    assert!(index_violations_for(&["tg", "search", "--index", "-N", "foo", "."]).is_empty());
    assert!(index_violations_for(&["tg", "search", "--index", "-n", "foo", "."]).is_empty());
    assert!(index_violations_for(&["tg", "search", "--index", "-S", "foo", "."]).is_empty());
    assert!(
        index_violations_for(&["tg", "search", "--index", "--no-ignore", "foo", "."]).is_empty()
    );
}

/// Creates a symlink at `link` pointing to `target`, dispatching to the platform-specific
/// primitive (mirrors write_bytes_refuse_symlink's own unix/windows/other split). Tests
/// call this and skip gracefully (matching the existing audit-manifest symlink tests) when
/// the sandbox lacks symlink privilege instead of failing the whole suite.
fn try_symlink_file(target: &Path, link: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        std::os::windows::fs::symlink_file(target, link)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link);
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "symlinks not supported on this platform",
        ))
    }
}

#[cfg(windows)]
#[test]
fn test_create_checkpoint_refuses_symlink_at_index_path() {
    // #115 Gap 2: checkpoint_index_path (checkpoint_storage_dir(root).join("index.json"))
    // is a SHARED, PREDICTABLE path across every checkpoint under a given root -- unlike
    // metadata.json (namespaced under a random checkpoint_id, unpredictable ahead of the
    // call), an attacker who can write into the checkpoint root can plant a symlink at
    // index.json in advance of any create_checkpoint call. Confirm create_checkpoint
    // refuses to follow it instead of silently overwriting whatever the symlink targets.
    let dir = tempfile::tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    std::fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

    let outside_dir = tempfile::tempdir().unwrap();
    let outside_target = outside_dir.path().join("outside-target.json");
    // Valid (empty-array) JSON so the pre-write "does index.json already exist -> read
    // + parse it" branch succeeds and execution actually reaches the write call this
    // test targets, instead of failing earlier on a JSON-parse error.
    std::fs::write(&outside_target, b"[]").unwrap();

    // Reuse the production scope-detection so the storage dir we plant the symlink under
    // is byte-identical to the one create_checkpoint computes internally (both must agree
    // post-canonicalization, or the symlink would land at the wrong path).
    let path_str = file_path.to_str().unwrap();
    let scope = detect_checkpoint_scope(path_str);
    let storage_dir = checkpoint_storage_dir(&scope.root);
    std::fs::create_dir_all(&storage_dir).unwrap();
    let index_path = storage_dir.join("index.json");
    if let Err(err) = try_symlink_file(&outside_target, &index_path) {
        eprintln!(
            "skipping test_create_checkpoint_refuses_symlink_at_index_path: cannot create a symlink in this environment: {err}"
        );
        return;
    }

    let result = create_checkpoint(path_str);
    assert!(
        result.is_err(),
        "create_checkpoint must refuse to write the shared index through a symlink"
    );
    assert_eq!(
        std::fs::read(&outside_target).unwrap(),
        b"[]",
        "the symlink's target outside the checkpoint root must be left untouched"
    );
}

#[cfg(windows)]
#[test]
fn test_create_checkpoint_cleanup_respects_platform_ownership_on_index_parse_failure() {
    // NIT (v1.76 #602 gate, "NIT-3"): checkpoint_store.py::create_checkpoint (the Python
    // side) already wraps its copy-loop + metadata-write in `except BaseException: rmtree
    // (snapshot_dir.parent); raise` (audit #125a, shipped in #602) so a failure never
    // orphans the random-id snapshot dir -- audit #178 later widened that same Python guard
    // to also cover the index write (see checkpoint_store.py::create_checkpoint), closing
    // the one gap that earlier widening had not yet reached. The Rust `create_checkpoint`
    // above had NO equivalent at all: a failure in the copy loop, the metadata write, OR
    // the index write left the just-created checkpoint_id directory behind. Native cleanup
    // now removes it on Windows where the retained directory handle proves ownership. Unix
    // keeps the unpublished directory and reports the refusal because there is no portable
    // atomic mkdir-plus-handle ownership operation against same-credential replacement.
    //
    // Force a deterministic, cross-platform failure at the LAST fallible step (the
    // pre-existing index.json fails to parse) so the copy loop and the metadata.json write
    // both succeed first -- proving the cleanup covers the whole write sequence, not just
    // the copy loop.
    let dir = tempfile::tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    std::fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

    let path_str = file_path.to_str().unwrap();
    let scope = detect_checkpoint_scope(path_str);
    let storage_dir = checkpoint_storage_dir(&scope.root);
    std::fs::create_dir_all(&storage_dir).unwrap();
    let index_path = storage_dir.join("index.json");
    std::fs::write(&index_path, b"not valid json").unwrap();

    let result = create_checkpoint(path_str);
    assert!(
        result.is_err(),
        "a corrupt pre-existing index.json must fail create_checkpoint"
    );

    let mut remaining: Vec<std::ffi::OsString> = std::fs::read_dir(&storage_dir)
        .unwrap()
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.file_name())
        .collect();
    remaining.sort();
    assert!(storage_dir.join(".index.json.lock.os").is_file());
    remaining.retain(|name| name != ".index.json.lock.os");
    #[cfg(windows)]
    assert_eq!(remaining, vec![std::ffi::OsString::from("index.json")]);
    #[cfg(unix)]
    {
        assert_eq!(
            remaining.len(),
            2,
            "Unix must retain the unpublished checkpoint directory"
        );
        assert!(remaining.contains(&std::ffi::OsString::from("index.json")));
        let checkpoint_id = remaining
            .iter()
            .find(|name| name.to_string_lossy() != "index.json")
            .expect("retained checkpoint id must be present");
        assert!(checkpoint_id.to_string_lossy().starts_with("ckpt-"));
        assert!(storage_dir
            .join(checkpoint_id)
            .join("metadata.json")
            .is_file());
    }
    assert_eq!(
        std::fs::read(&index_path).unwrap(),
        b"not valid json",
        "the pre-existing (corrupt) index.json itself must be left untouched"
    );
}

#[cfg(windows)]
#[test]
fn test_create_checkpoint_does_not_disclose_symlink_target() {
    // Item 1 (audit #178, surfaced by the #610 gate): `std::fs::copy` FOLLOWS symlinks, so
    // a source-tree symlink pointing OUTSIDE the checkpoint root previously had its
    // TARGET's content copied into the snapshot -- an out-of-root disclosure. Mirrors the
    // Python-side regression test `test_create_checkpoint_does_not_disclose_symlink_target`
    // in tests/unit/test_checkpoint_containment.py (checkpoint_store.py:853-855).
    let repo_parent = tempfile::tempdir().unwrap();
    let repo_path = repo_parent.path().join("repo");
    std::fs::create_dir_all(&repo_path).unwrap();
    std::fs::write(repo_path.join("real.py"), b"in-repo content\n").unwrap();

    let outside_dir = tempfile::tempdir().unwrap();
    let secret = outside_dir.path().join("secret.txt");
    std::fs::write(&secret, b"SECRET-OUT-OF-ROOT\n").unwrap();

    let link_path = repo_path.join("link.txt");
    if let Err(err) = try_symlink_file(&secret, &link_path) {
        eprintln!(
            "skipping test_create_checkpoint_does_not_disclose_symlink_target: cannot create a symlink in this environment: {err}"
        );
        return;
    }

    let path_str = repo_path.to_str().unwrap();
    let result = create_checkpoint(path_str).expect("checkpoint creation must still succeed");

    let scope = detect_checkpoint_scope(path_str);
    let snapshot_dir = checkpoint_snapshot_dir(&scope.root, &result.checkpoint_id);
    let snapshotted_link = snapshot_dir.join("link.txt");

    // The snapshot entry must itself be a symlink -- never a regular file holding the
    // target's bytes -- proving the target's content was never read/copied.
    let snapshotted_type = std::fs::symlink_metadata(&snapshotted_link)
        .expect("snapshotted symlink entry must exist")
        .file_type();
    assert!(
        snapshotted_type.is_symlink(),
        "checkpoint snapshot must store the symlink itself, not its resolved target content"
    );

    // Belt-and-suspenders: no regular file anywhere under the snapshot may contain the
    // out-of-root secret's content.
    for entry in walkdir::WalkDir::new(&snapshot_dir) {
        let entry = entry.unwrap();
        if entry.file_type().is_file() {
            let text = std::fs::read_to_string(entry.path()).unwrap_or_default();
            assert!(
                !text.contains("SECRET-OUT-OF-ROOT"),
                "checkpoint snapshot must not disclose the out-of-root secret's content: {}",
                entry.path().display()
            );
        }
    }
}

#[cfg(windows)]
#[test]
fn test_create_checkpoint_still_copies_regular_files_byte_identical() {
    // Regression guard for the copy_checkpoint_entry symlink fix above: an ordinary
    // (non-symlink) source tree must still be captured with byte-identical content, and a
    // normal (no-symlink) checkpoint create must be entirely unaffected by the new
    // is_symlink() branch.
    let dir = tempfile::tempdir().unwrap();
    let repo_path = dir.path().join("repo");
    std::fs::create_dir_all(repo_path.join("pkg")).unwrap();
    std::fs::write(repo_path.join("a.py"), b"alpha\n").unwrap();
    std::fs::write(repo_path.join("pkg").join("b.py"), b"beta\n").unwrap();

    let path_str = repo_path.to_str().unwrap();
    let result = create_checkpoint(path_str).expect("checkpoint creation must succeed");
    assert_eq!(result.file_count, 2);

    let scope = detect_checkpoint_scope(path_str);
    let snapshot_dir = checkpoint_snapshot_dir(&scope.root, &result.checkpoint_id);
    assert_eq!(
        std::fs::read(snapshot_dir.join("a.py")).unwrap(),
        b"alpha\n"
    );
    assert_eq!(
        std::fs::read(snapshot_dir.join("pkg").join("b.py")).unwrap(),
        b"beta\n"
    );
}

/// Task 276 task 6: the DISCLOSURE and the EXIT CODE must read one predicate.
///
/// `emit_multi_pattern_native_results` exits 2 on `walk_was_incomplete`; the `--json` and
/// `--ndjson` envelopes stamp `result_incomplete` from `incomplete_envelope_fields`. Before
/// task 6 the exit code was not derived from the count at all, which is how that route came
/// to print `result_incomplete: true` and then exit 0 -- or, on a zero-match incomplete
/// scan, exit 1, which reads as an authoritative "no matches exist".
///
/// This asserts the two cannot disagree for ANY input. It fails if a later change
/// re-derives either side independently, which is the specific way this family recurs.
#[test]
fn walk_incompleteness_predicate_agrees_with_the_envelope_it_stamps() {
    for incomplete_paths in [None, Some(0), Some(1), Some(2), Some(usize::MAX)] {
        let (result_incomplete, reason_class, count) = incomplete_envelope_fields(incomplete_paths);
        let exits_two = walk_was_incomplete(incomplete_paths);

        assert_eq!(
            result_incomplete == Some(true),
            exits_two,
            "envelope and exit code disagree for {incomplete_paths:?}: \
             result_incomplete={result_incomplete:?}, walk_was_incomplete={exits_two}"
        );
        if exits_two {
            assert_eq!(reason_class, Some("unreadable_path"));
            assert_eq!(count, incomplete_paths);
        } else {
            assert_eq!(reason_class, None);
            assert_eq!(count, None);
        }
    }

    // CONTROL ARM. Both not-incomplete inputs are in the table above, so the loop cannot
    // pass by only ever exercising the positive case -- and a guard that answered `true`
    // unconditionally would satisfy the agreement assertion while exiting 2 on every clean
    // search. `None` means "this route observed no walk of its own"; `Some(0)` means "a walk
    // ran and hit no errors". Neither is an incompleteness claim.
    assert!(!walk_was_incomplete(None));
    assert!(!walk_was_incomplete(Some(0)));
    assert!(walk_was_incomplete(Some(1)));
}
