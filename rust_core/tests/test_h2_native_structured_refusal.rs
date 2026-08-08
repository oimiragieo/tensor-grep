//! P5·H2 behavioral arm (end-to-end, against the REAL built `tg` binary via CARGO_BIN_EXE_tg).
//!
//! Pre-fix receipt (before these refusals existed, verified live on the shipped native front
//! door): `tg search <dir> needle --json --count-matches` exited 0 and printed a plain match
//! list -- `search_requires_ripgrep_passthrough`'s hard-flag list is gated behind
//! `!json && !ndjson`, so on the structured route the count/files flags fell through to the
//! native engine (which has no field for them) and were SILENTLY DROPPED. These tests pin the
//! fail-closed replacement: each of those combinations must now exit 2 (not 0) with a
//! refusal message naming the flag, and the honored controls must still exit 0.
//!
//! CI runs this file via `cargo test` (integration tests get `CARGO_BIN_EXE_tg` at compile
//! time); it cannot run on the desktop (CPU-safe forbids `cargo` locally).

use std::fs::File;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Output};

use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn run_tg(args: &[&str], cwd: &Path) -> Output {
    tg().current_dir(cwd).args(args).output().unwrap()
}

fn write_fixture(dir: &Path, name: &str, body: &str) -> String {
    let mut f = File::create(dir.join(name)).unwrap();
    f.write_all(body.as_bytes()).unwrap();
    dir.join(name).to_string_lossy().into_owned()
}

fn fixture_dir() -> (tempfile::TempDir, String) {
    let dir = tempdir().unwrap();
    let path = write_fixture(
        dir.path(),
        "a.txt",
        "line one needle needle needle\nplain line\nneedle last\n",
    );
    (dir, path)
}

/// Asserts a P5·H2 refusal: exit 2, stderr names every spilled flag, and the message says
/// "refusing" (never a silent empty/plain output with exit 0).
fn assert_refused(output: &Output, flags: &[&str]) {
    let code = output.status.code();
    assert_eq!(
        code,
        Some(2),
        "expected exit 2, got {code:?}; stdout: {}; stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.to_ascii_lowercase().contains("refusing"),
        "refusal must say 'refusing'; stderr: {stderr}"
    );
    for flag in flags {
        assert!(
            stderr.contains(flag),
            "refusal must name {flag}; stderr: {stderr}"
        );
    }
}

fn assert_not_refused(output: &Output) {
    assert_eq!(
        output.status.code(),
        Some(0),
        "control must exit 0; stdout: {}; stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn structured_search_hard_refuses_count_and_files_flags() {
    let (dir, path) = fixture_dir();
    let cwd = dir.path();
    // search-command structured door (--json/--ndjson), the ORIGINAL silent-drop combos.
    // argv shape: `search PATTERN PATH [flags]`; `path` is the fixture FILE searched, `cwd`
    // is the temp DIRECTORY the binary runs in (a file cannot be process `current_dir`).
    let out = run_tg(
        &["search", "needle", &path, "--json", "--count-matches"],
        cwd,
    );
    assert_refused(&out, &["--count-matches"]);
    let out = run_tg(
        &["search", "needle", &path, "--ndjson", "--count-matches"],
        cwd,
    );
    assert_refused(&out, &["--count-matches"]);
    let out = run_tg(
        &["search", "needle", &path, "--json", "--files-with-matches"],
        cwd,
    );
    assert_refused(&out, &["--files-with-matches"]);
    let out = run_tg(
        &["search", "needle", &path, "--json", "--files-without-match"],
        cwd,
    );
    assert_refused(&out, &["--files-without-match"]);
}

#[test]
fn positional_doors_hard_refuse_count_matches() {
    let (dir, path) = fixture_dir();
    let cwd = dir.path();
    // positional argv shape: `PATTERN PATH [flags]`. --gpu-device-ids door (reachable
    // unconditionally, no json needed) and the positional --json structured door.
    let out = run_tg(
        &["needle", &path, "--gpu-device-ids", "0", "--count-matches"],
        cwd,
    );
    assert_refused(&out, &["--count-matches"]);
    let out = run_tg(&["needle", &path, "--json", "--count-matches"], cwd);
    assert_refused(&out, &["--count-matches"]);
}

#[test]
fn honored_controls_still_exit_zero() {
    let (dir, path) = fixture_dir();
    let cwd = dir.path();
    // Plain structured search (no count/files flags) must be untouched.
    let out = run_tg(&["search", "needle", &path, "--json"], cwd);
    assert_not_refused(&out);
    // -o/--only-matching IS honored by the native engine and must stay out of the refusal set.
    let out = run_tg(&["search", "needle", &path, "--json", "-o"], cwd);
    assert_not_refused(&out);
    // A matching bare positional search still succeeds.
    let out = run_tg(&["needle", &path], cwd);
    assert_not_refused(&out);
}
