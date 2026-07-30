//! Task #17 -- cross the broad-scan refusal envelope to the standalone native binary.
//!
//! #851 gave the Python CLI's front door a machine-readable `error.code: "broad_scan_refused"`
//! JSON envelope under `--json` for the shared implicit-walk-ceiling refusal
//! (`tests/unit/test_broad_scan_refusal_json_envelope.py`). The NATIVE front door (this binary --
//! the one standalone-binary/Homebrew/winget users actually run) still emitted a bare `eprintln!`
//! and 0 stdout bytes for the exact same refusal, documented in `rust_core/src/main.rs` as a
//! DELIBERATE choice that #851 broke the symmetry of. This file exercises the REAL compiled `tg`
//! binary (not `CliRunner`, not an in-process unit test) end to end, mirroring the Python file's
//! coverage field for field.
//!
//! Deterministic without walking >1500 real files: `TG_TEST_NATIVE_SEARCH_FORCE_ERROR` (an
//! existing test-only hook, `execute_native_search` in `main.rs`) forces the exact ceiling-refusal
//! message text into the native engine's `Err` path, which `is_unbounded_implicit_search_walk_
//! refusal` (a substring match) recognizes exactly as it would the real thing.

use std::path::Path;
use std::process::{Command, Output};

use serde_json::Value;
use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn forced_ceiling_refusal_message() -> String {
    tensor_grep_rs::rg_passthrough::format_unbounded_implicit_search_walk_error(
        tensor_grep_rs::rg_passthrough::IMPLICIT_SEARCH_WALK_FILE_CEILING,
    )
}

/// Forces the single-pattern native route (`run_native_search_with_optional_rg_fallback`, the
/// shared chokepoint for both `tg search PATTERN` and the bare positional `tg PATTERN`) to fail
/// with the shared implicit-walk-ceiling refusal message. `TG_DISABLE_RG=1` + `PATH=""` force
/// native-CPU routing for the non-`--json` arm too -- otherwise a plain-text search with `rg` on
/// `PATH` would route to rg passthrough and never touch the code this task changed at all.
fn run_forced_single_pattern_refusal(dir: &Path, extra_args: &[&str]) -> Output {
    tg().current_dir(dir)
        .arg("search")
        .arg("needle")
        .args(extra_args)
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            forced_ceiling_refusal_message(),
        )
        .output()
        .unwrap()
}

#[test]
fn text_mode_stdout_stays_empty_on_the_single_pattern_route() {
    // CONTROL ARM: without it, an emitter that always printed the envelope regardless of `--json`
    // would satisfy the json-mode test below while polluting the plain-text surface.
    let dir = tempdir().unwrap();
    let output = run_forced_single_pattern_refusal(dir.path(), &[]);

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.stdout.is_empty(),
        "text mode must still print nothing to stdout, got {:?}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn json_mode_emits_the_envelope_and_stderr_stays_byte_identical_to_text_mode() {
    // CRITICAL CONSTRAINT: text-mode stderr must stay byte-identical whether or not `--json` is
    // set -- the Rust twin (`exit_on_native_multi_pattern_ceiling_refusal`'s sibling in
    // `run_native_search_with_optional_rg_fallback`) deliberately mirrors `execute_ripgrep_
    // search`'s OWN refusal text; if `--json` altered that stderr line, the two would drift from
    // each other even though this task never touched `execute_ripgrep_search` at all.
    let text_dir = tempdir().unwrap();
    let json_dir = tempdir().unwrap();
    let text_output = run_forced_single_pattern_refusal(text_dir.path(), &[]);
    let json_output = run_forced_single_pattern_refusal(json_dir.path(), &["--json"]);

    assert_eq!(json_output.status.code(), Some(2));
    assert_eq!(
        json_output.stderr,
        text_output.stderr,
        "text-mode stderr must be byte-identical to --json stderr\njson stderr={}\ntext stderr={}",
        String::from_utf8_lossy(&json_output.stderr),
        String::from_utf8_lossy(&text_output.stderr),
    );
    assert_eq!(
        String::from_utf8_lossy(&text_output.stderr),
        format!("{}\n", forced_ceiling_refusal_message()),
        "stderr text itself must be exactly the shared refusal message, unchanged by this task"
    );

    assert!(
        !json_output.stdout.is_empty(),
        "a --json consumer must not get 0 stdout bytes on this refusal (the #851 gap, now on \
         the standalone binary)"
    );
    let payload: Value = serde_json::from_slice(&json_output.stdout).unwrap_or_else(|err| {
        panic!(
            "stdout was not valid JSON ({err}): {}",
            String::from_utf8_lossy(&json_output.stdout)
        )
    });
    // Field-for-field parity with `_emit_broad_scan_refusal` (cli/main.py) and
    // `test_broad_scan_refusal_json_envelope.py`.
    assert_eq!(payload["total_matches"], 0);
    assert_eq!(payload["total_files"], 0);
    assert_eq!(payload["matches"], serde_json::json!([]));
    assert_eq!(payload["truncated"], true);
    assert_eq!(payload["result_incomplete"], true);
    assert_eq!(payload["incomplete_reason_class"], "scan_limit");
    assert_eq!(
        payload["incomplete_reason"],
        forced_ceiling_refusal_message()
    );
    assert_eq!(payload["error"]["code"], "broad_scan_refused");
    assert_eq!(payload["error"]["retryable"], false);
    assert_eq!(
        payload["error"]["message"],
        forced_ceiling_refusal_message()
    );
}

#[test]
fn multi_pattern_route_also_emits_the_envelope_under_json() {
    // Sibling coverage for `exit_on_native_multi_pattern_ceiling_refusal` (the helper the task
    // description names directly) -- the multi-`-e` regex route, a DIFFERENT call site than the
    // single-pattern test above. Deliberately NOT `--fixed-strings`: that would route through the
    // AhoCorasick fast path (`run_native_fixed_multi_pattern_search`), which never calls
    // `execute_native_search` and so never consults `TG_TEST_NATIVE_SEARCH_FORCE_ERROR`.
    let dir = tempdir().unwrap();
    let output = tg()
        .current_dir(dir.path())
        .arg("search")
        .arg("--json")
        .arg("-e")
        .arg("alpha")
        .arg("-e")
        .arg("beta")
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            forced_ceiling_refusal_message(),
        )
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|err| {
        panic!(
            "stdout was not valid JSON ({err}): {}",
            String::from_utf8_lossy(&output.stdout)
        )
    });
    assert_eq!(payload["error"]["code"], "broad_scan_refused");
    assert_eq!(payload["result_incomplete"], true);
    assert_eq!(payload["incomplete_reason_class"], "scan_limit");
}

#[test]
fn an_unrelated_native_error_is_not_reported_as_broad_scan_refused() {
    // CONTROL ARM: a native-search error that is NOT the shared ceiling refusal must NOT gain
    // this envelope. Without this, `emit_broad_scan_refusal_json_if_needed` firing for every
    // `--json` error (not just this one) would misreport bad-pattern/bad-path failures as a
    // scan-policy refusal they never were.
    let dir = tempdir().unwrap();
    let output = tg()
        .current_dir(dir.path())
        .arg("search")
        .arg("needle")
        .arg("--json")
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "")
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "native search: an unrelated forced failure",
        )
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(2));
    // Pre-existing (out-of-scope for #17) behavior for an UNRECOGNIZED native error under
    // `--json` is 0 stdout bytes -- `emit_broad_scan_refusal_json_if_needed` must not change
    // that by over-firing on an error it does not recognize.
    assert!(
        output.stdout.is_empty(),
        "an unrelated error must not gain the broad_scan_refused envelope: stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
}
