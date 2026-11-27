#![allow(unused_imports, dead_code)]
use serde_json::Value;
use std::fs;
use std::path::Path;
use std::process::Command;
use tempfile::tempdir;
use tensor_grep_rs::index::TrigramIndex;
#[path = "common/index_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_tg_search_explicit_index_smart_case_uppercase_pattern_stays_case_sensitive() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("a.txt"),
        "foo one\nFOO two\nfoo three\nbar unrelated\n",
    )
    .unwrap();

    // Native smart-case: a pattern containing an uppercase char is case-SENSITIVE, so
    // `FOO` matches only the uppercase line = 1. Regression guard: proves the honor fix
    // does NOT over-broadly make every -S query case-insensitive (which would return 3).
    let native = tg()
        .arg("search")
        .arg("--json")
        .arg("-S")
        .arg("FOO")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(native.status.success());
    let native_json: Value = serde_json::from_slice(&native.stdout).unwrap();
    assert_eq!(
        native_json["total_matches"], 1,
        "sanity: smart-case upper-case pattern is case-sensitive in native"
    );

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--json")
        .arg("-S")
        .arg("FOO")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        indexed.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed.stderr)
    );
    let indexed_json: Value = serde_json::from_slice(&indexed.stdout).unwrap();
    assert_eq!(
        indexed_json["total_matches"], native_json["total_matches"],
        "--index -S (upper-case pattern) must stay case-sensitive like native"
    );
    assert_eq!(match_tuples(&indexed_json), match_tuples(&native_json));
}

#[test]
fn test_tg_search_warm_auto_index_smart_case_lowercase_pattern_matches_native() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("a.txt"),
        "foo one\nFOO two\nfoo three\nbar unrelated\n",
    )
    .unwrap();

    // Compute the native ground truth BEFORE any .tg_index exists -- once a warm index is
    // built, EVERY subsequent query in this dir auto-routes to it, so a "native" query run
    // afterward would be contaminated (it would hit the index too, making the parity check
    // vacuous). With no index present, `--json -S foo` routes to NativeCpu.
    assert!(!resolved_index_path(dir.path()).exists());
    let native = tg()
        .arg("search")
        .arg("--json")
        .arg("-S")
        .arg("foo")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(native.status.success());
    let native_json: Value = serde_json::from_slice(&native.stdout).unwrap();
    assert_eq!(
        native_json["total_matches"], 3,
        "sanity: native smart-case lowercase pattern is case-insensitive = 3"
    );
    assert!(
        !resolved_index_path(dir.path()).exists(),
        "a cold native --json query must not create an index"
    );

    // Now build a warm index (plain, no -S) so the -S query below auto-routes to it.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("foo")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());
    assert!(resolved_index_path(dir.path()).exists());

    // Warm auto-routing (NO --index) with --json -S foo must ALSO honor smart-case -- the
    // query flows through the same run_index_query chokepoint as explicit --index, so a
    // single fix covers both. --verbose lets us confirm it truly routed to the index (else
    // the parity assertion would be vacuous, e.g. if it fell through to native/ripgrep).
    let warm = tg()
        .arg("search")
        .arg("--json")
        .arg("--verbose")
        .arg("-S")
        .arg("foo")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        warm.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&warm.stderr)
    );
    let warm_stderr = String::from_utf8_lossy(&warm.stderr);
    assert!(
        warm_stderr.contains("TrigramIndex"),
        "expected warm auto-routing to the trigram index: stderr={warm_stderr}"
    );
    let warm_json: Value = serde_json::from_slice(&warm.stdout).unwrap();
    assert_eq!(
        warm_json["total_matches"], native_json["total_matches"],
        "warm auto-routed -S (all-lowercase) must honor smart-case like native"
    );
    assert_eq!(match_tuples(&warm_json), match_tuples(&native_json));
}

// -- Audit fix #1 (2026-07-11): exhaustive --index capability validator -------------------
//
// H1a (above) hand-listed 6 refused flags; `index_flag_violations` (rust_core/src/main.rs)
// replaces that ad-hoc list with an exhaustive per-field classification (Honor / PassthroughSafe
// / Refuse) covering every `SearchArgs` field, shared by both the explicit `--index` gate
// (`handle_index_search`) and the warm auto-routing gate (`detect_warm_index_state`). The
// runtime-backstop exhaustiveness test (`index_flag_policy_table_is_exhaustive_over_search_args_
// clap_ids`) plus several fast direct-call unit tests for `index_flag_violations` itself live in
// rust_core/src/main.rs's own `#[cfg(test)] mod tests`, NOT here -- `SearchArgs` and
// `index_flag_violations` are private to that binary crate, so an external integration test file
// cannot call them directly (house rule: dogfood the real binary; these tests below do that for
// the observable CLI contract).

#[test]
fn test_tg_search_explicit_index_refuses_flags_outside_the_original_six() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Build the index first so a missing/stale index cannot itself explain a non-zero exit.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());

    // --json guarantees reaching handle_index_search for every case: route_search checks
    // explicit_index (set by --index) ahead of everything except `--pcre2 && rg_available`, and
    // none of these cases pass --pcre2. (Several of these flags -- hidden, max_depth, file_type,
    // sort*, column, vimgrep, null, count_matches, replace, only_matching, path_separator,
    // no_ignore_vcs -- are ALSO in search_requires_ripgrep_passthrough's non-json OR-list, so a
    // *plain* (non-json) invocation would instead be intercepted by handle_ripgrep_search's early
    // rg-passthrough branch before route_search ever runs -- see index_flag_violations's doc
    // comment for the same reachability shape as the pre-existing H1e smart-case tests above.)
    //
    // Explicit --index bypasses the Python front door; --require-git has a dedicated sentinel
    // test below. Direct-call unit tests cover the remaining Refuse-classified flags.
    let cases: &[(&[&str], &str)] = &[
        (&["--hidden"], "hidden"),
        (&["--max-depth", "2"], "max-depth"),
        (&["-t", "py"], "type"),
        (&["--sort", "path"], "sort"),
        (&["--sortr", "path"], "sortr"),
        (&["--sort-files"], "sort-files"),
        (&["-o"], "only-matching"),
        (&["-r", "X"], "replace"),
        (&["--max-filesize", "10K"], "max-filesize"),
        (&["--no-ignore-vcs"], "no-ignore-vcs"),
        (&["-L"], "follow"),
        (&["-a"], "text"),
        (&["-l"], "files-with-matches"),
        (&["--files-without-match"], "files-without-match"),
        (&["--column"], "column"),
        (&["--count-matches"], "count-matches"),
        (&["--vimgrep"], "vimgrep"),
        (&["--null"], "null"),
        (&["--path-separator", "/"], "path-separator"),
    ];

    for (extra, needle) in cases {
        let mut cmd = tg();
        cmd.arg("search").arg("--index").arg("--json");
        for arg in *extra {
            cmd.arg(arg);
        }
        let output = cmd.arg("hello").arg(dir.path()).output().unwrap();
        assert!(
            output.status.code() == Some(2),
            "expected --index {extra:?} to fail closed; stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(payload["version"], 1);
        assert_eq!(payload["ok"], false);
        assert_eq!(payload["error"], "backend_error");
        let detail = payload["detail"].as_str().unwrap().to_lowercase();
        assert!(
            detail.contains(needle),
            "error for {extra:?} should name the incompatible flag (expected {needle:?}): detail={detail}"
        );
    }
}

#[test]
fn test_tg_search_explicit_index_refuses_contradictory_engine_flags() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // fold-in (c): --index combined with an explicit alternate engine is contradictory.
    // route_search checks explicit_index ahead of force_cpu/explicit_gpu_device_ids, so without
    // this check the engine flag would be silently dropped (the index would just be used)
    // instead of honored or refused.
    let cpu = tg()
        .arg("search")
        .arg("--index")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        !cpu.status.success(),
        "--index --cpu requests contradictory engines and must fail closed; stdout={} stderr={}",
        String::from_utf8_lossy(&cpu.stdout),
        String::from_utf8_lossy(&cpu.stderr)
    );

    let gpu = tg()
        .arg("search")
        .arg("--index")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--fixed-strings")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        !gpu.status.success(),
        "--index --gpu-device-ids requests contradictory engines and must fail closed; stdout={} stderr={}",
        String::from_utf8_lossy(&gpu.stdout),
        String::from_utf8_lossy(&gpu.stderr)
    );
}

#[test]
fn test_tg_search_explicit_index_allows_passthrough_safe_flags_and_matches_baseline() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let baseline = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(baseline.status.success());
    let baseline_json: Value = serde_json::from_slice(&baseline.stdout).unwrap();

    // Deliberately excludes no_fixed_strings/no_invert_match/ignore/no_hidden/pcre2_unicode/
    // messages/no_config/auto_hybrid_regex from this black-box bundle: those PassthroughSafe
    // flags are ALSO in search_format_python_passthrough_args's SEARCH_PYTHON_PASSTHROUGH_FLAGS
    // allowlist (unconditionally, or via its --json-gated third check for no_config/
    // auto_hybrid_regex) -- an earlier, unrelated dispatcher that forwards to the Python sidecar
    // before `--index` is even parsed by clap (see the comment on
    // test_tg_search_explicit_index_refuses_flags_outside_the_original_six above for the same
    // gap). Their PassthroughSafe classification is covered by the direct-call unit test
    // `index_flag_violations_allows_passthrough_safe_bundle` in main.rs's `mod tests` instead.
    let passthrough_safe = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--no-column")
        .arg("--unicode")
        .arg("--color")
        .arg("auto")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        passthrough_safe.status.success(),
        "PassthroughSafe flags must not be refused; stderr={}",
        String::from_utf8_lossy(&passthrough_safe.stderr)
    );
    let passthrough_json: Value = serde_json::from_slice(&passthrough_safe.stdout).unwrap();
    assert_eq!(
        match_tuples(&passthrough_json),
        match_tuples(&baseline_json),
        "PassthroughSafe flags must not change the result set"
    );
}

/// Audit #138/#140 (the gap flagged in the comments on
/// `test_tg_search_explicit_index_refuses_flags_outside_the_original_six` and
/// `test_tg_search_explicit_index_allows_passthrough_safe_flags_and_matches_baseline` above):
/// `search_format_python_passthrough_args` (main.rs) forwarded any invocation carrying a
/// SEARCH_PYTHON_PASSTHROUGH_FLAGS member -- e.g. `--no-hidden`, `--require-git` -- to the Python
/// sidecar BEFORE `--index` was ever parsed by clap, `--index` token included. Python has no such
/// option, so that either crashes ("Error: No such option: --index") or leaks the token into the
/// constructed rg argv, and never reaches this binary's own `index_flag_violations` validator.
/// Points `TG_SIDECAR_PYTHON` at a script that only echoes a sentinel: if the Python passthrough
/// front door is ever reached, the sentinel appears in stdout instead of a native JSON/error
/// envelope, giving a deterministic, environment-independent (no real venv needed) proof of which
/// dispatcher actually ran.
const PYTHON_PASSTHROUGH_SENTINEL: &str = "TG_PYTHON_PASSTHROUGH_FRONTDOOR_SENTINEL";

fn write_python_passthrough_sentinel(dir: &Path) -> std::path::PathBuf {
    #[cfg(windows)]
    {
        let script = dir.join("python-passthrough-sentinel.cmd");
        fs::write(
            &script,
            format!("@echo off\r\necho {PYTHON_PASSTHROUGH_SENTINEL}\r\n"),
        )
        .unwrap();
        script
    }
    #[cfg(not(windows))]
    {
        use std::os::unix::fs::PermissionsExt;
        let script = dir.join("python-passthrough-sentinel.sh");
        fs::write(
            &script,
            format!("#!/bin/sh\necho {PYTHON_PASSTHROUGH_SENTINEL}\n"),
        )
        .unwrap();
        fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
        script
    }
}

#[test]
fn test_tg_search_explicit_index_short_circuits_python_frontdoor_for_json_gated_passthrough_flag() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let python_sentinel = write_python_passthrough_sentinel(dir.path());

    // --no-hidden is a SEARCH_PYTHON_PASSTHROUGH_FLAGS member (and separately PassthroughSafe for
    // the index path, see INDEX_FLAG_POLICY) -- before the fix this alone routed the whole
    // invocation, --index included, to the Python sidecar.
    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--no-hidden")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_SIDECAR_PYTHON", &python_sentinel)
        .output()
        .unwrap();

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stdout.contains(PYTHON_PASSTHROUGH_SENTINEL) && !stderr.contains(PYTHON_PASSTHROUGH_SENTINEL),
        "an explicit --index must never be forwarded to the Python sidecar; stdout={stdout} stderr={stderr}"
    );
    assert!(
        !stderr.to_lowercase().contains("no such option"),
        "must not surface a Click-style unknown-option crash; stderr={stderr}"
    );
    // --no-hidden is PassthroughSafe for the index path -- the request must reach the native
    // trigram-index engine and succeed, not merely avoid crashing.
    assert!(output.status.success(), "stdout={stdout} stderr={stderr}");
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "TrigramIndex");
    assert_eq!(payload["routing_reason"], "index-accelerated");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["total_matches"], 2);
}

#[test]
fn test_tg_search_explicit_index_short_circuits_python_frontdoor_and_reaches_validator_for_require_git(
) {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let python_sentinel = write_python_passthrough_sentinel(dir.path());

    // --require-git is honored by SEARCH_PYTHON_PASSTHROUGH_FLAGS unconditionally (no --json
    // gate) and is classified Refuse by index_flag_violations. This proves --index now reaches
    // that validator (a real refusal, naming the flag) instead of the Python sidecar.
    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--require-git")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_SIDECAR_PYTHON", &python_sentinel)
        .output()
        .unwrap();

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stdout.contains(PYTHON_PASSTHROUGH_SENTINEL)
            && !stderr.contains(PYTHON_PASSTHROUGH_SENTINEL),
        "an explicit --index must never be forwarded to the Python sidecar even with \
         --require-git riding along; stdout={stdout} stderr={stderr}"
    );
    assert!(
        output.status.code() == Some(2),
        "--index --require-git must fail closed via index_flag_violations, not silently \
         succeed; stdout={stdout} stderr={stderr}"
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["version"], 1);
    assert_eq!(payload["ok"], false);
    assert_eq!(payload["error"], "backend_error");
    let detail = payload["detail"].as_str().unwrap();
    assert!(
        detail.to_lowercase().contains("require-git"),
        "the refusal must name the unsupported flag; detail={detail}"
    );
}

#[test]
fn test_tg_search_explicit_index_still_works_with_case_insensitive_flag() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("-i")
        .arg("--json")
        .arg("HELLO")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "TrigramIndex");
    assert_eq!(payload["routing_reason"], "index-accelerated");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["total_matches"], 2);
}

#[test]
fn test_tg_search_non_index_passthrough_flag_still_routes_to_python_sidecar() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    let python_sentinel = write_python_passthrough_sentinel(dir.path());

    // TRAP guard: the identical --no-hidden/--json combination as the short-circuit test above,
    // MINUS --index, must still be forwarded to the Python sidecar exactly as before the fix.
    let output = tg()
        .arg("search")
        .arg("--no-hidden")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_SIDECAR_PYTHON", &python_sentinel)
        .output()
        .unwrap();

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains(PYTHON_PASSTHROUGH_SENTINEL),
        "a non-index passthrough-flag invocation must still route to the Python sidecar \
         unchanged; stdout={stdout} stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_tg_search_explicit_index_color_always_is_refused_but_never_and_auto_are_not() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    for mode in ["never", "auto"] {
        let output = tg()
            .arg("search")
            .arg("--index")
            .arg("--fixed-strings")
            .arg("--count")
            .arg("--color")
            .arg(mode)
            .arg("hello")
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "--color {mode} must not be refused; stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    let always = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--color")
        .arg("always")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        !always.status.success(),
        "--color always asks for output the index path cannot produce and must be refused"
    );
}

#[test]
fn test_tg_search_warm_auto_index_reroutes_instead_of_dropping_hidden_files() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    fs::write(dir.path().join(".hidden_secret.txt"), "hello hidden\n").unwrap();

    // Build a warm index. The trigram build walker hardcodes hidden-file exclusion
    // (`WalkBuilder::hidden(true)` in index.rs), so .hidden_secret.txt is never indexed
    // regardless of this build's own flags.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());
    assert!(resolved_index_path(dir.path()).exists());

    // NO --index: --hidden combined with --json reaches detect_warm_index_state's gate the same
    // way the pre-existing H1e smart-case tests above reach handle_index_search's (a plain/non-
    // json --hidden invocation is instead intercepted earlier by search_prefers_ripgrep_
    // passthrough's rg-passthrough branch in handle_ripgrep_search). --json also makes the
    // eventual reroute destination deterministic regardless of whether `rg` happens to be on
    // PATH: once detect_warm_index_state denies warm-index eligibility, route_search's
    // `structured_output` branch sends it to native_cpu_json unconditionally.
    //
    // Before audit fix #1, detect_warm_index_state's 6-flag gate didn't cover --hidden, so this
    // query would have silently auto-routed to the (hidden-file-blind) warm index and missed
    // .hidden_secret.txt.
    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg("--hidden")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stderr.contains("routing_backend=TrigramIndex"),
        "--hidden must reroute past the warm index (which cannot see hidden files), not use it: stderr={stderr}"
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    let files = match_tuples(&payload)
        .into_iter()
        .map(|(file, _, _)| file)
        .collect::<Vec<_>>();
    assert!(
        files.iter().any(|f| f.contains(".hidden_secret.txt")),
        "warm auto-routing must reroute to an engine that honors --hidden, not silently drop it: matches={files:?}"
    );
}

#[test]
fn test_tg_search_index_count_no_match_exits_one_rg_parity() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("zzzzzznotfound")
        .arg(dir.path())
        .output()
        .unwrap();

    // fold-in (a) + must-fix (PR #541): native CPU/GPU already exit(1) on zero matches;
    // run_index_query previously always returned Ok(()) (exit 0). And with the count arm now
    // routed through emit_count_search_matches, a no-match dir search prints NOTHING (an empty
    // match set yields no per-file lines), byte-matching `rg -c`'s empty-stdout + exit-1 contract
    // -- not the old bare `0`.
    assert_eq!(output.status.code(), Some(1));
    assert!(
        output.stdout.is_empty(),
        "no-match --index -c must print nothing (rg -c parity), got {:?}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn test_tg_search_index_plain_no_match_exits_one_rg_parity() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("zzzzzznotfound")
        .arg(dir.path())
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&output.stdout).is_empty());
}

#[test]
fn test_tg_search_index_plain_match_still_exits_zero() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "a real match must still exit 0; stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

// -- Audit fix #1 MUST-FIX : --index/warm `-c` per-file output shape -------
//
// The index count arm used to emit a bare aggregate total (`println!("{unique_count}")`, e.g.
// `3`) while `rg -c`, the native CPU engine, and the sibling native aggregate emitter all print
// per-file `path:count`. Because `count` is an Honor flag (correctly -- the index HAS the matches,
// so refusing would needlessly disable a fast count), the WARM auto-index path reaches the same
// arm; so a plain `tg search -c <pat> <dir>` silently changed output shape the moment a `.tg_index`
// existed -- the silent-wrong-shape-with-exit-0 this validator exists to prevent. Fixed by routing
// the arm through emit_count_search_matches (rg-compatible per-file counts, zero-count files
// omitted). These tests pin the fixed shape end to end through the real binary.

#[test]
fn test_tg_search_explicit_index_count_emits_per_file_not_bare_aggregate() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        indexed.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed.stderr)
    );

    // Deterministic (the index count path is rg-independent): one `path:count` line per MATCHED
    // file, zero-count c.log omitted -- exactly `rg -c`'s shape.
    assert_eq!(
        parse_per_file_counts(&indexed.stdout),
        hello_per_file_counts()
    );

    // Regression guard against the old bare-aggregate emitter: the output must NOT be a single
    // parseable number (it printed `2` before the fix). Two per-file lines can't parse as one usize.
    let stdout = String::from_utf8_lossy(&indexed.stdout);
    assert!(
        stdout.trim().parse::<usize>().is_err(),
        "--index -c must be per-file `path:count`, not a bare aggregate number: {stdout:?}"
    );
    assert_eq!(
        stdout.lines().count(),
        2,
        "one line per matched file: {stdout:?}"
    );
}

#[test]
fn test_tg_search_explicit_index_count_matches_rg_oracle_per_file_when_rg_present() {
    // The coordinator's requested parity: `tg search --index -c` must match `tg search -c`
    // (no --index). Compared by per-file COUNT MAP, not raw bytes: `rg -c` emits files in
    // nondeterministic parallel-walk order (observed b,a in one run and a,b in another), whereas
    // the index path's emit_count_search_matches is BTreeMap-sorted -- so a raw byte-match against
    // rg is inherently flaky (the "golden-test rg-backend sensitivity" trap this repo has hit
    // before). The order-independent count map is the meaningful, deterministic invariant. When rg
    // is ABSENT the oracle falls to the native CPU engine, which additionally emits grep-style
    // `<file>:0` zero-count lines (a pre-existing native-vs-rg divergence out of scope here) -- so
    // the oracle comparison is asserted ONLY when the oracle used rg, detected by the absence of
    // any `:0` line (which rg never emits). The deterministic index-side shape is asserted
    // unconditionally.
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let oracle = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        oracle.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&oracle.stderr)
    );
    let oracle_used_rg = !String::from_utf8_lossy(&oracle.stdout)
        .lines()
        .any(|line| line.ends_with(":0"));

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(indexed.status.success());

    // Always-true deterministic invariant: rg-compatible per-file counts (zero-count c.log omitted).
    assert_eq!(
        parse_per_file_counts(&indexed.stdout),
        hello_per_file_counts()
    );

    if oracle_used_rg {
        assert_eq!(
            parse_per_file_counts(&indexed.stdout),
            parse_per_file_counts(&oracle.stdout),
            "--index -c must match the `rg -c` oracle's per-file counts. indexed={:?} oracle={:?}",
            String::from_utf8_lossy(&indexed.stdout),
            String::from_utf8_lossy(&oracle.stdout)
        );
    }
}

#[test]
fn test_tg_search_warm_index_count_shape_does_not_depend_on_hidden_index_file() {
    // The coordinator's CORE correctness concern: a plain `tg search -c <pat> <dir>` must NOT
    // silently change output SHAPE when a `.tg_index` appears on disk. Capture the cold oracle
    // BEFORE the index exists, build the index, then re-run the SAME plain `-c` (now warm
    // auto-routed to the index) and require the same per-file counts. Compared by count MAP, not
    // raw bytes, for the same rg-nondeterministic-order reason as the sibling test above.
    let dir = tempdir().unwrap();
    write_corpus(dir.path());
    assert!(!resolved_index_path(dir.path()).exists());

    let cold = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(cold.status.success());
    let cold_used_rg = !String::from_utf8_lossy(&cold.stdout)
        .lines()
        .any(|line| line.ends_with(":0"));

    // Build the warm index (this run ALSO now emits per-file, which is fine).
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());
    assert!(resolved_index_path(dir.path()).exists());

    // Same plain `-c` again -- now warm auto-routes to the index (count is an Honor flag).
    let warm = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("-c")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        warm.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&warm.stderr)
    );
    // Confirm it actually used the index (else the "shape unchanged" assertion would be vacuous).
    assert!(
        String::from_utf8_lossy(&warm.stderr).contains("TrigramIndex"),
        "warm `-c` should auto-route to the trigram index: stderr={}",
        String::from_utf8_lossy(&warm.stderr)
    );
    // Deterministic: warm `-c` emits per-file counts, NOT the old bare aggregate `3`.
    assert_eq!(parse_per_file_counts(&warm.stdout), hello_per_file_counts());
    // The whole point: per-file counts must not depend on whether a hidden `.tg_index` exists.
    if cold_used_rg {
        assert_eq!(
            parse_per_file_counts(&warm.stdout),
            parse_per_file_counts(&cold.stdout),
            "warm-index `-c` must match the pre-index (rg) `-c` per-file counts; a hidden \
             `.tg_index` must not change output shape. warm={:?} cold={:?}",
            String::from_utf8_lossy(&warm.stdout),
            String::from_utf8_lossy(&cold.stdout)
        );
    }
}

// NOTE on -N/--no-line-number specifically: `-N`/`--no-line-number` cannot be exercised via the
// top-level `tg search --index -N ...` CLI in an environment with a working Python sidecar on
// PATH. `-N` is unconditionally in `SEARCH_PYTHON_PASSTHROUGH_FLAGS` (main.rs), so
// `search_format_python_passthrough_args` forwards the ENTIRE invocation to the Python sidecar
// before `--index` is even parsed by this binary's own clap `SearchArgs` -- and Python has no
// `--index` concept, so it errors ("No such option '--index'") instead of ever reaching
// `run_index_query`. That is a real, separate, pre-existing gap in the Rust/Python dispatch
// layer (confirmed present on main before this fix too), not something `index_flag_violations`
// or the fold-in (b) line-number threading can address -- fixing it would mean touching
// `search_format_python_passthrough_args`'s allowlist or the Python CLI itself, well outside
// this audit item's scope. `-N`'s Honor classification (not refused) is covered by the
// direct-call unit test `index_flag_violations_honors_original_six_plus_no_line_number` in
// main.rs's `mod tests`, which calls `index_flag_violations` directly and is unaffected by CLI
// dispatch. The test below instead exercises the SAME `line_number && !no_line_number`
// expression's "numbers shown" branch via `-n` (which is NOT in any Python-dispatch allowlist),
// proving the threading fold-in (b) added end to end -- through the real shipped binary.
#[test]
fn test_tg_search_explicit_index_line_number_shown_via_n_matches_native() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Single-match query ("goodbye" appears exactly once, in a.txt only) so the comparison
    // isn't confounded by the trigram-index engine and the native-CPU engine returning
    // multi-file matches in different (each internally consistent, but mutually different)
    // orders -- an orthogonal, pre-existing characteristic unrelated to line-number rendering
    // (TrigramIndex::search's postings order vs native's explicit sort_search_matches).
    //
    // TG_DISABLE_RG=1 pins the no-index baseline to the native CPU engine deterministically --
    // without it, route_search sends a plain (non-json) query to rg passthrough whenever `rg`
    // happens to be on PATH, which would make this comparison's baseline engine (and thus its
    // exact byte format) depend on the test machine (the "golden test rg-backend sensitivity"
    // trap this repo has hit before).
    let native = tg()
        .env("TG_DISABLE_RG", "1")
        .arg("search")
        .arg("--fixed-strings")
        .arg("-n")
        .arg("goodbye")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(native.status.success());
    assert!(
        String::from_utf8_lossy(&native.stdout).contains(":3:"),
        "sanity: 'goodbye world' is line 3 of a.txt; stdout={}",
        String::from_utf8_lossy(&native.stdout)
    );

    let indexed = tg()
        .env("TG_DISABLE_RG", "1")
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-n")
        .arg("goodbye")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        indexed.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed.stderr)
    );

    assert_eq!(
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&native.stdout),
        "-n output via --index must byte-match the no-index route"
    );
}

#[test]
fn test_tg_search_explicit_index_default_line_number_behavior_matches_native() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // No -n/-N: the index path's default must now byte-match the no-index route's default,
    // since both compute the same `line_number && !no_line_number` expression (fold-in b)
    // instead of the index path's old hardcoded `true`. Single-match query + TG_DISABLE_RG=1:
    // see the ordering/rg-sensitivity notes above.
    let native = tg()
        .env("TG_DISABLE_RG", "1")
        .arg("search")
        .arg("--fixed-strings")
        .arg("goodbye")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(native.status.success());

    let indexed = tg()
        .env("TG_DISABLE_RG", "1")
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("goodbye")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(
        indexed.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed.stderr)
    );

    assert_eq!(
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&native.stdout),
        "default (no -n/-N) output via --index must byte-match the no-index route"
    );
}

/// M17 F2 (audit-m17 gate): an index built from a RELATIVE root must never be dereferenced
/// against a LATER query's cwd. Build from cwd A with a relative `tree` root; a decoy tree
/// with IDENTICAL name/size/mtime lives at cwd B. Query A's tree BY ITS ABSOLUTE PATH from
/// cwd B: the canonical-root check legitimately passes (same tree), so the only thing
/// standing between the query and B's decoy is the deref-through-verified-canonical-root
/// rule. Pre-fix, stored entries kept the raw relative spelling (`tree/a.txt`), staleness
/// and search resolved it against cwd B, read the metadata-identical decoy, found no match,
/// and exited 1; post-fix everything dereferences through the canonical root, reads A's real
/// file, and exits 0 with the match. Structural argument: the decoy is byte-for-byte
/// metadata-identical (same size, same mtime via FileTimes), so the ONLY discriminator is
/// which root the entry paths are joined to.
#[test]
fn test_m17_f2_relative_root_index_never_reads_other_cwd_tree() {
    let outer = tempdir().unwrap();
    let cwd_a = outer.path().join("A");
    let cwd_b = outer.path().join("B");
    fs::create_dir(&cwd_a).unwrap();
    fs::create_dir(&cwd_b).unwrap();
    fs::create_dir(cwd_a.join("tree")).unwrap();
    fs::create_dir(cwd_b.join("tree")).unwrap();

    fs::write(cwd_a.join("tree/a.txt"), "hello world\n").unwrap();
    fs::write(cwd_b.join("tree/a.txt"), "yyyyyyyyyyy\n").unwrap(); // same 12 bytes, no match

    let mtime_a = fs::metadata(cwd_a.join("tree/a.txt"))
        .unwrap()
        .modified()
        .unwrap();
    let decoy = std::fs::OpenOptions::new()
        .write(true)
        .open(cwd_b.join("tree/a.txt"))
        .unwrap();
    decoy
        .set_times(std::fs::FileTimes::new().set_modified(mtime_a))
        .unwrap();
    drop(decoy);
    assert_eq!(
        fs::metadata(cwd_b.join("tree/a.txt"))
            .unwrap()
            .modified()
            .unwrap(),
        mtime_a,
        "decoy premise: B's tree must be metadata-identical to A's tree"
    );
    assert_eq!(
        fs::metadata(cwd_b.join("tree/a.txt")).unwrap().len(),
        fs::metadata(cwd_a.join("tree/a.txt")).unwrap().len(),
        "decoy premise: sizes must match"
    );

    // Build the index with a RELATIVE root from cwd A.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg("tree")
        .current_dir(&cwd_a)
        .output()
        .unwrap();
    assert!(
        build.status.success(),
        "build stderr={}",
        String::from_utf8_lossy(&build.stderr)
    );

    // Query A's tree by ABSOLUTE path FROM cwd B -- the cross-cwd deref test.
    let query = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg(cwd_a.join("tree"))
        .current_dir(&cwd_b)
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&query.stdout);
    assert!(
        query.status.success(),
        "cross-cwd index search must exit 0; stderr={} stdout={stdout}",
        String::from_utf8_lossy(&query.stderr)
    );
    assert!(
        stdout.contains("hello world"),
        "the index must read the REAL tree through the canonical root, not cwd B's decoy: stdout={stdout}"
    );
}

/// M17 F3 (gate round 2): index output must carry the QUERY's path SPELLING, not the
/// canonical absolute path -- dereference stays canonical (sound), but emission
/// re-projects through the query root's original spelling. A relative `tree` root must
/// emit `tree<sep>a.txt` (platform separator), never the canonical absolute path.
#[test]
fn test_m17_f3_index_output_uses_query_root_spelling() {
    let outer = tempdir().unwrap();
    let cwd = outer.path().join("A");
    fs::create_dir(&cwd).unwrap();
    fs::create_dir(cwd.join("tree")).unwrap();
    fs::write(cwd.join("tree/a.txt"), "hello world\n").unwrap();

    // Build and query with the SAME relative `tree` root, all from one cwd.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg("tree")
        .current_dir(&cwd)
        .output()
        .unwrap();
    assert!(
        build.status.success(),
        "build stderr={}",
        String::from_utf8_lossy(&build.stderr)
    );

    let query = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("hello")
        .arg("tree")
        .current_dir(&cwd)
        .output()
        .unwrap();
    assert!(
        query.status.success(),
        "query stderr={}",
        String::from_utf8_lossy(&query.stderr)
    );
    let stdout = String::from_utf8_lossy(&query.stdout);
    assert!(
        stdout.contains("hello world"),
        "the match content must be present: stdout={stdout}"
    );
    // Portable separator: Path::new("tree").join("a.txt") yields `tree\a.txt` on Windows
    // and `tree/a.txt` on POSIX, so this assertion holds on every platform if any.
    let relative_spelling = Path::new("tree")
        .join("a.txt")
        .to_string_lossy()
        .to_string();
    assert!(
        stdout.contains(&relative_spelling),
        "the emitted path must use the QUERY's relative spelling ({relative_spelling}): stdout={stdout}"
    );
    assert!(
        !stdout.contains(&cwd.join("tree").to_string_lossy().to_string()),
        "the canonical absolute tree path must not leak into output: stdout={stdout}"
    );
}
