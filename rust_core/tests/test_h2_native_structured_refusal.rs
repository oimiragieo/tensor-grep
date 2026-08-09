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
//! The positional `--gpu-device-ids` + `--count-matches` arm is NOW deterministic in BOTH
//! environments: the front door rewrites it into the search-subcommand form (`--count-matches`
//! is in `SEARCH_OPTION_FIRST_FLAGS`), and the search-form GPU gate in `handle_ripgrep_search`
//! (fired before the rg-passthrough early return) refuses it with the "refusing" message BEFORE
//! the rg-required gate can fire -- rg present (where the combo used to silently drop the GPU
//! request at exit 0, the defect) or rg absent (CI), the same exit 2 + "refusing" + flag names.
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

/// P5·H2 fail-closed contract for the positional `--gpu-device-ids` + `--count-matches` door.
///
/// Deterministic in every environment: `--count-matches` is in `SEARCH_OPTION_FIRST_FLAGS`, so
/// the front door normalizes the combination into the `tg search` subcommand form, where the
/// search-form GPU gate (`rg_passthrough_gpu_dropped_search_flags` in `handle_ripgrep_search`)
/// fires BEFORE the rg-passthrough early return -- and therefore before that block's
/// `require_ripgrep_or_exit` "requires the ripgrep (`rg`) backend" exit -- so the "refusing"
/// message is the only one reachable, with rg present (the old silent-GPU-drop exit 0) or
/// rg absent (CI's test-rust-core lanes).
fn assert_positional_gpu_refused(output: &Output) {
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
        "refusal must say 'refusing' (the search-form GPU gate fires before the rg-required \
         gate in every environment); stderr: {stderr}"
    );
    assert!(
        stderr.contains("--count-matches") && stderr.contains("--gpu-device-ids"),
        "refusal must name both --count-matches and --gpu-device-ids; stderr: {stderr}"
    );
}

#[test]
fn positional_doors_hard_refuse_count_matches() {
    let (dir, path) = fixture_dir();
    let cwd = dir.path();
    // positional argv shape: `PATTERN PATH [flags]`. --gpu-device-ids door (reachable
    // unconditionally, no json needed). The front door rewrites this into the search form
    // (--count-matches is in SEARCH_OPTION_FIRST_FLAGS), where the search-form GPU gate fires
    // deterministically -- strict assertion, no environment split (see
    // assert_positional_gpu_refused).
    let out = run_tg(
        &["needle", &path, "--gpu-device-ids", "0", "--count-matches"],
        cwd,
    );
    assert_positional_gpu_refused(&out);
    // --json short-circuits the rg-required passthrough predicate, so the P5·H2 refusing
    // validator is the ONLY gate for this arm in every environment: keep the strict form.
    let out = run_tg(&["needle", &path, "--json", "--count-matches"], cwd);
    assert_refused(&out, &["--count-matches"]);
}

#[test]
fn search_form_gpu_count_files_combos_hard_refuse() {
    let (dir, path) = fixture_dir();
    let cwd = dir.path();
    // Direct search-form combos (no front-door rewrite needed): an explicit --gpu-device-ids
    // request combined with any count/files flag must refuse, because the rg-passthrough route
    // that honors the count/files flag has no GPU field -- the old shape silently dropped the
    // GPU request at exit 0. Deterministic with rg present or absent: the gate fires before the
    // passthrough block's rg-required exit.
    let out = run_tg(
        &[
            "search",
            "needle",
            &path,
            "--gpu-device-ids",
            "0",
            "--count-matches",
        ],
        cwd,
    );
    assert_refused(&out, &["--count-matches", "--gpu-device-ids"]);
    let out = run_tg(
        &["search", "needle", &path, "--gpu-device-ids", "0", "-l"],
        cwd,
    );
    assert_refused(&out, &["--files-with-matches", "--gpu-device-ids"]);
    let out = run_tg(
        &[
            "search",
            "needle",
            &path,
            "--gpu-device-ids",
            "0",
            "--files-with-matches",
        ],
        cwd,
    );
    assert_refused(&out, &["--files-with-matches", "--gpu-device-ids"]);
    let out = run_tg(
        &[
            "search",
            "needle",
            &path,
            "--gpu-device-ids",
            "0",
            "--files-without-match",
        ],
        cwd,
    );
    assert_refused(&out, &["--files-without-match", "--gpu-device-ids"]);
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
    // Pure --count-matches / -l WITHOUT --gpu-device-ids must NOT hit the new GPU gate: they
    // keep their honored rg-passthrough route (exit 0 when rg is present; on rg-absent CI the
    // PRE-EXISTING rg-required passthrough gate exits 2 with "requires the ripgrep" -- never
    // the new GPU-refusal message, whose fingerprint is naming --gpu-device-ids). This control
    // proves the new predicate requires the GPU flag.
    for args in [
        ["search", "needle", path.as_str(), "--count-matches"].as_slice(),
        ["search", "needle", path.as_str(), "-l"].as_slice(),
    ] {
        let out = run_tg(args, cwd);
        let code = out.status.code();
        assert!(
            code == Some(0) || code == Some(2),
            "pure count/files without --gpu-device-ids must stay on rg passthrough (exit 0 with \
             rg present; pre-existing rg-required exit 2 with rg absent), got {code:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(
            !stderr.contains("--gpu-device-ids"),
            "the new GPU-refusal gate must NOT fire without --gpu-device-ids; stderr: {stderr}"
        );
    }
}
