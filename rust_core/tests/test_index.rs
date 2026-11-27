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
fn test_tg_search_index_builds_and_returns_results() {
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
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("hello world"), "stdout={stdout}");
    assert!(stdout.contains("hello again"), "stdout={stdout}");
    assert!(
        resolved_index_path(dir.path()).exists(),
        "index file should be created"
    );
}

#[test]
fn test_tg_search_index_count_mode() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    // Audit fix #1 must-fix (PR #541): `--index -c` now emits per-file `path:count` (matching
    // `rg -c`), not the old bare aggregate `2`. hello matches one line each in a.txt and b.txt.
    assert_eq!(
        parse_per_file_counts(&output.stdout),
        hello_per_file_counts()
    );
}

#[test]
fn test_tg_search_index_json_contract() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["version"], 1);
    assert_eq!(result["routing_backend"], "TrigramIndex");
    assert_eq!(result["routing_reason"], "index-accelerated");
    assert_eq!(result["sidecar_used"], false);
    assert_eq!(result["total_matches"], 2);
    assert!(result["query"].is_string());
    assert!(result["path"].is_string());

    let matches = result["matches"].as_array().unwrap();
    assert_eq!(matches.len(), 2);
    for m in matches {
        assert!(m["file"].is_string());
        assert!(m["line"].is_number());
        assert!(m["text"].is_string());
    }
}

#[test]
fn test_tg_search_json_contract_includes_unified_envelope() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["version"], 1);
    assert_eq!(result["routing_backend"], "NativeCpuBackend");
    assert_eq!(result["routing_reason"], "json_output");
    assert_eq!(result["sidecar_used"], false);
    assert_eq!(result["total_matches"], 2);
    assert!(result["query"].is_string());
    assert!(result["path"].is_string());

    let matches = result["matches"].as_array().unwrap();
    assert_eq!(matches.len(), 2);
    for m in matches {
        assert!(m["file"].is_string());
        assert!(m["line"].is_number());
        assert!(m["text"].is_string());
    }
}

#[test]
fn test_tg_search_index_verbose_shows_routing() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("routing_backend=TrigramIndex"),
        "stderr={stderr}"
    );
}

#[test]
fn test_tg_search_index_warm_cache_reuses_index() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let out1 = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(out1.status.success());

    let index_path = resolved_index_path(dir.path());
    assert!(index_path.exists());
    let mtime_1 = index_path.metadata().unwrap().modified().unwrap();

    std::thread::sleep(std::time::Duration::from_millis(50));

    let out2 = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(out2.status.success());

    let mtime_2 = index_path.metadata().unwrap().modified().unwrap();
    assert_eq!(
        mtime_1, mtime_2,
        "index should not be rebuilt when corpus is unchanged"
    );
}

#[test]
fn test_tg_search_index_no_match_returns_zero() {
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

    // Audit fix #1 fold-in (a) + must-fix (PR #541): rg exit-parity AND rg output-shape parity.
    // `rg -c` on a no-match dir search prints NOTHING and exits 1. run_index_query previously
    // always returned Ok(()) (exit 0) and, once the count arm emitted a bare aggregate, printed
    // "0"; it now routes through emit_count_search_matches (empty stdout for an empty match set on
    // a dir target) AND exits 1, matching `rg -c` exactly. See
    // test_tg_search_index_count_no_match_exits_one_rg_parity and
    // test_tg_search_index_plain_no_match_exits_one_rg_parity below for the dedicated coverage.
    assert_eq!(
        output.status.code(),
        Some(1),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.stdout.is_empty(),
        "no-match --index -c must print nothing (rg -c parity), got {:?}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn test_tg_search_index_rebuilds_on_stale() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Build initial index
    let out1 = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(out1.status.success());
    // Audit fix #1 must-fix (PR #541): per-file `path:count`, not a bare aggregate.
    assert_eq!(parse_per_file_counts(&out1.stdout), hello_per_file_counts());

    // Modify corpus
    std::thread::sleep(std::time::Duration::from_millis(50));
    fs::write(dir.path().join("d.txt"), "hello new file\n").unwrap();

    // Index should rebuild and find the new file
    let out2 = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(out2.status.success());
    // The rebuild must surface the new d.txt as an additional per-file count entry.
    let mut expected = hello_per_file_counts();
    expected.insert("d.txt".to_string(), 1);
    assert_eq!(
        parse_per_file_counts(&out2.stdout),
        expected,
        "should find hello in the new file too"
    );
    let stderr = String::from_utf8_lossy(&out2.stderr);
    assert!(
        stderr.contains("stale") || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
}

#[test]
fn test_tg_search_index_handles_corrupt_index() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Write a corrupt index file
    fs::write(dir.path().join(".tg_index"), b"CORRUPT_DATA").unwrap();

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success(), "should recover from corrupt index");
    // Audit fix #1 must-fix (PR #541): per-file `path:count`, not a bare aggregate.
    assert_eq!(
        parse_per_file_counts(&output.stdout),
        hello_per_file_counts()
    );
}

#[test]
fn test_tg_search_auto_routes_to_warm_index() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Build index explicitly first
    tg().arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(resolved_index_path(dir.path()).exists());

    // Now search WITHOUT --index but with --verbose to see routing
    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("warm index found") || stderr.contains("TrigramIndex"),
        "should auto-route to index: stderr={stderr}"
    );
    // Audit fix #1 must-fix (PR #541): the whole point of the must-fix -- warm auto-routed `-c`
    // (count is an Honor flag, so it still uses the index) must emit per-file `path:count`
    // matching `rg -c`, NOT the old bare aggregate `2`. Before the fix, a plain `tg search -c`
    // silently changed shape to a bare number the moment a `.tg_index` existed.
    assert_eq!(
        parse_per_file_counts(&output.stdout),
        hello_per_file_counts()
    );
}

#[test]
fn test_tg_search_auto_route_falls_through_for_short_pattern() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Build index
    tg().arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    // Short pattern (<3 chars) should NOT use index
    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("--count")
        .arg("hi")
        .arg(dir.path())
        .output()
        .unwrap();

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stderr.contains("warm index"),
        "short pattern should not auto-route to index: stderr={stderr}"
    );
}

#[test]
fn test_tg_search_auto_route_falls_through_for_invert() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    tg().arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    // Invert match should NOT use index
    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-v")
        .arg("--verbose")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        !stderr.contains("warm index"),
        "invert match should not auto-route to index: stderr={stderr}"
    );
}

#[test]
fn test_tg_search_index_query_result_parity_for_multiple_patterns() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    for (pattern, fixed_strings) in [("hello", true), ("error", true), (r"error:.*failed", false)] {
        let mut plain = tg();
        plain.arg("search");
        if fixed_strings {
            plain.arg("--fixed-strings");
        }
        let plain_output = plain
            .arg("--json")
            .arg(pattern)
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            plain_output.status.success(),
            "stderr={}",
            String::from_utf8_lossy(&plain_output.stderr)
        );
        let plain_json: Value = serde_json::from_slice(&plain_output.stdout).unwrap();

        let mut indexed = tg();
        indexed.arg("search").arg("--index");
        if fixed_strings {
            indexed.arg("--fixed-strings");
        }
        let indexed_output = indexed
            .arg("--json")
            .arg(pattern)
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            indexed_output.status.success(),
            "stderr={}",
            String::from_utf8_lossy(&indexed_output.stderr)
        );
        let indexed_json: Value = serde_json::from_slice(&indexed_output.stdout).unwrap();

        assert_eq!(
            indexed_json["total_matches"], plain_json["total_matches"],
            "pattern={pattern}"
        );
        assert_eq!(
            match_tuples(&indexed_json),
            match_tuples(&plain_json),
            "pattern={pattern}"
        );
    }
}

#[test]
fn test_tg_search_index_query_result_parity_for_regex_alternation_classes_and_unicode() {
    let dir = tempdir().unwrap();
    write_regex_prefilter_corpus(dir.path());

    for pattern in [r"(foo|bar)", r"[abc]def", r"de[ab]f", r"(東京|大阪)"] {
        let plain_output = tg()
            .arg("search")
            .arg("--json")
            .arg(pattern)
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            plain_output.status.success(),
            "plain stderr={} pattern={pattern}",
            String::from_utf8_lossy(&plain_output.stderr)
        );
        let plain_json: Value = serde_json::from_slice(&plain_output.stdout).unwrap();

        let indexed_output = tg()
            .arg("search")
            .arg("--index")
            .arg("--json")
            .arg(pattern)
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            indexed_output.status.success(),
            "indexed stderr={} pattern={pattern}",
            String::from_utf8_lossy(&indexed_output.stderr)
        );
        let indexed_json: Value = serde_json::from_slice(&indexed_output.stdout).unwrap();

        assert_eq!(
            indexed_json["total_matches"], plain_json["total_matches"],
            "pattern={pattern}"
        );
        assert_eq!(
            match_tuples(&indexed_json),
            match_tuples(&plain_json),
            "pattern={pattern}"
        );
    }
}

#[test]
fn test_tg_search_index_old_format_triggers_rebuild() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    write_legacy_v1_index(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success(), "should recover from old format");
    // Audit fix #1 must-fix (PR #541): per-file `path:count`, not a bare aggregate.
    assert_eq!(
        parse_per_file_counts(&output.stdout),
        hello_per_file_counts()
    );

    let rebuilt = fs::read(resolved_index_path(dir.path())).unwrap();
    assert_eq!(&rebuilt[0..4], b"TGI\x00");
    assert_eq!(
        rebuilt[4],
        tensor_grep_rs::index::INDEX_FORMAT_VERSION,
        "expected rebuilt index to use the current format"
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("warning")
            || stderr.contains("failed to load")
            || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
}

#[test]
fn test_tg_search_index_case_insensitive() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-i")
        .arg("--count")
        .arg("HELLO")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    // Audit fix #1 must-fix (PR #541): per-file `path:count`, not a bare aggregate. Case-insensitive
    // `HELLO` matches the lowercase `hello` line in a.txt and b.txt (one each).
    assert_eq!(
        parse_per_file_counts(&output.stdout),
        hello_per_file_counts()
    );
}

#[test]
fn test_tg_search_index_verbose_distinguishes_full_and_incremental_rebuilds() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let initial = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        initial.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&initial.stderr)
    );
    let initial_stderr = String::from_utf8_lossy(&initial.stderr);
    assert!(
        initial_stderr.contains("full rebuild"),
        "stderr should identify a full rebuild: {initial_stderr}"
    );

    std::thread::sleep(std::time::Duration::from_millis(50));
    fs::write(dir.path().join("d.txt"), "hello incremental\n").unwrap();

    let incremental = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        incremental.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&incremental.stderr)
    );
    let incremental_stderr = String::from_utf8_lossy(&incremental.stderr);
    assert!(
        incremental_stderr.contains("incremental update"),
        "stderr should identify an incremental update: {incremental_stderr}"
    );
}

#[test]
fn test_tg_search_index_survives_repeated_mutation_cycles() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let expect_count = |expected: usize| {
        let output = tg()
            .arg("search")
            .arg("--index")
            .arg("--fixed-strings")
            .arg("--json")
            .arg("hello")
            .arg(dir.path())
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );

        let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(payload["routing_backend"], "TrigramIndex");
        assert_eq!(
            payload["total_matches"].as_u64().unwrap() as usize,
            expected
        );
    };

    expect_count(2);

    fs::write(dir.path().join("fresh.txt"), "hello fresh file\n").unwrap();
    expect_count(3);

    fs::write(
        dir.path().join("b.txt"),
        "nothing here\nhello again friend\nhello third line\n",
    )
    .unwrap();
    expect_count(4);

    fs::remove_file(dir.path().join("a.txt")).unwrap();
    expect_count(3);
}

// -- H1 audit fail-closed regressions (2026-07-10) --------------------------------------
//
// `--index` used to win routing before any compatibility checks and hand the query
// straight to `run_index_query`, which only ever consulted `pattern`/`ignore_case`/
// `fixed_strings` -- silently dropping every other search flag instead of honoring or
// refusing it. These tests pin the fixed, fail-closed contract for each confirmed gap.

#[test]
fn test_tg_search_explicit_index_invert_match_fails_closed_instead_of_dropping_v() {
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

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-v")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1a (audit #79/#10): route_search() returned TrigramIndex for --index before
    // consulting the same invert_match/context/max_count/word_regexp/glob/multi-pattern
    // compatibility checks detect_warm_index_state already enforces for warm-index
    // auto-routing, so run_index_query() silently ignored -v and printed the
    // NON-inverted count with exit 0. --index combined with -v must fail closed
    // (non-zero exit, error naming the flag) instead of silently returning the exact
    // opposite result set.
    assert!(
        !indexed.status.success(),
        "expected --index -v to fail closed instead of silently ignoring -v; stdout={} stderr={}",
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&indexed.stderr)
    );
    let stderr = String::from_utf8_lossy(&indexed.stderr).to_lowercase();
    assert!(
        stderr.contains("invert") || stderr.contains("-v"),
        "error should name the incompatible flag: stderr={stderr}"
    );
}

#[test]
fn test_tg_search_explicit_index_word_regexp_fails_closed_instead_of_dropping_w() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-w")
        .arg("--count")
        .arg("hell")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1a: -w (word_regexp) is one of the compatibility checks detect_warm_index_state
    // enforces for auto-routing; run_index_query() never consulted it at all, so
    // "hell" (a substring of "hello", not a whole word) would have silently matched via
    // plain substring search instead of either honoring word boundaries or refusing.
    assert!(
        !indexed.status.success(),
        "expected --index -w to fail closed instead of silently ignoring -w; stdout={} stderr={}",
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&indexed.stderr)
    );
}

#[test]
fn test_tg_search_explicit_index_max_count_fails_closed_instead_of_dropping_m() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-m")
        .arg("1")
        .arg("--count")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1a: -m/--max-count is silently dropped by run_index_query today (it never reads
    // args.max_count), so a request to cap matches per file was ignored outright.
    assert!(
        !indexed.status.success(),
        "expected --index -m to fail closed instead of silently ignoring -m; stdout={} stderr={}",
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&indexed.stderr)
    );
}

#[test]
fn test_tg_search_explicit_index_glob_fails_closed_instead_of_dropping_g() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-g")
        .arg("*.log")
        .arg("--count")
        .arg("error")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1a: -g/--glob is silently dropped by run_index_query today (it never reads
    // args.globs), so a request scoped to *.log would have silently searched every
    // indexed file instead.
    assert!(
        !indexed.status.success(),
        "expected --index -g to fail closed instead of silently ignoring -g; stdout={} stderr={}",
        String::from_utf8_lossy(&indexed.stdout),
        String::from_utf8_lossy(&indexed.stderr)
    );
}

#[test]
fn test_tg_search_explicit_index_short_fixed_string_matches_plain_search() {
    let dir = tempdir().unwrap();
    write_corpus(dir.path());

    // Cross-backend parity must go through --json's total_matches/matches, not --count:
    // the plain/native path prints rg-compatible "path:count" per matching file, while
    // run_index_query's --count prints a single aggregate number -- different shapes, not
    // a bug, just not directly comparable (see match_tuples/total_matches usage elsewhere
    // in this file for the established parity-check convention).
    let plain_output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("ok")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(plain_output.status.success());
    let plain_json: Value = serde_json::from_slice(&plain_output.stdout).unwrap();
    assert_eq!(
        plain_json["total_matches"], 1,
        "sanity: fixture should contain exactly one line matching 'ok'"
    );

    let indexed_output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("ok")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1b (audit): index.rs::search() hardwired fixed_strings to
    // RegexCandidateSelection::Indexed unconditionally, even for patterns shorter than
    // TRIGRAM_LEN (3 bytes) whose empty trigram set makes query_candidates_fixed return
    // zero candidates -- producing a false "0 matches" instead of falling back to a full
    // scan the way the regex branch already does via regex_candidate_selection's
    // FullScan arm.
    assert!(
        indexed_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed_output.stderr)
    );
    let indexed_json: Value = serde_json::from_slice(&indexed_output.stdout).unwrap();
    assert_eq!(
        indexed_json["total_matches"], plain_json["total_matches"],
        "short fixed-string --index search must fall back to a full scan, not silently return 0"
    );
    assert_eq!(match_tuples(&indexed_json), match_tuples(&plain_json));
}

#[test]
fn test_tg_search_explicit_index_unicode_ignore_case_matches_plain_search() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("unicode.txt"),
        "Bienvenue au CAFÉ du coin\n",
    )
    .unwrap();

    let plain_output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-i")
        .arg("--json")
        .arg("café")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(plain_output.status.success());
    let plain_json: Value = serde_json::from_slice(&plain_output.stdout).unwrap();
    assert_eq!(
        plain_json["total_matches"], 1,
        "sanity: fixture line should case-insensitively contain 'café'"
    );

    let indexed_output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("-i")
        .arg("--json")
        .arg("café")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1c (audit): extract_file_trigrams (build side) lowercases with to_ascii_lowercase,
    // a no-op on multi-byte UTF-8, but query_candidates_fixed (query side) lowercases
    // with Unicode-aware str::to_lowercase -- so a non-ASCII ignore-case fixed string's
    // query trigrams can never line up with the index's build-time trigrams for "CAFÉ",
    // producing a false "0 matches".
    assert!(
        indexed_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed_output.stderr)
    );
    let indexed_json: Value = serde_json::from_slice(&indexed_output.stdout).unwrap();
    assert_eq!(
        indexed_json["total_matches"], plain_json["total_matches"],
        "non-ASCII ignore-case --index search must fall back to a full scan, not silently return 0"
    );
    assert_eq!(match_tuples(&indexed_json), match_tuples(&plain_json));
}

#[test]
fn test_tg_search_explicit_index_no_ignore_flip_off_to_on_finds_newly_included_files() {
    let dir = tempdir().unwrap();
    if !init_git_repo(dir.path()) {
        return;
    }
    fs::write(dir.path().join(".gitignore"), "secret.txt\n").unwrap();
    fs::write(dir.path().join("visible.txt"), "shared_needle visible\n").unwrap();
    fs::write(dir.path().join("secret.txt"), "shared_needle secret\n").unwrap();

    // Build the index WITHOUT --no-ignore: secret.txt must not be indexed.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("shared_needle")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());
    // Audit fix #1 must-fix (PR #541): `-c` is now per-file `path:count`. The invariant this test
    // guards is the FILE SET (is secret.txt indexed?), captured order-independently by the count
    // map's keys. Default build: only visible.txt (secret.txt gitignored).
    assert_eq!(
        parse_per_file_counts(&build.stdout),
        std::collections::BTreeMap::from([("visible.txt".to_string(), 1)]),
        "sanity: only visible.txt should be indexed by default"
    );

    // Re-query the SAME index directory, now WITH --no-ignore.
    let requeried = tg()
        .arg("search")
        .arg("--index")
        .arg("--no-ignore")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("shared_needle")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1d/D1 (audit): the on-disk index format never recorded the no_ignore mode it was
    // built with, and staleness_reason()'s new-file scan was hardcoded .git_ignore(true)
    // regardless of the current query's --no-ignore request -- so a --no-ignore query
    // against an index built WITHOUT --no-ignore silently reused the stale index and
    // MISSED secret.txt instead of rebuilding to include it.
    assert!(
        requeried.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&requeried.stderr)
    );
    assert_eq!(
        parse_per_file_counts(&requeried.stdout),
        std::collections::BTreeMap::from([
            ("secret.txt".to_string(), 1),
            ("visible.txt".to_string(), 1),
        ]),
        "--no-ignore query must rebuild the index and include the gitignored file"
    );
}

#[test]
fn test_tg_search_explicit_index_no_ignore_flip_on_to_off_does_not_leak_gitignored_matches() {
    let dir = tempdir().unwrap();
    if !init_git_repo(dir.path()) {
        return;
    }
    fs::write(dir.path().join(".gitignore"), "secret.txt\n").unwrap();
    fs::write(dir.path().join("visible.txt"), "shared_needle visible\n").unwrap();
    fs::write(dir.path().join("secret.txt"), "shared_needle secret\n").unwrap();

    // Build the index WITH --no-ignore: secret.txt is deliberately indexed.
    let build = tg()
        .arg("search")
        .arg("--index")
        .arg("--no-ignore")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("shared_needle")
        .arg(dir.path())
        .output()
        .unwrap();
    assert!(build.status.success());
    // Audit fix #1 must-fix (PR #541): per-file count map; the FILE SET is the invariant.
    assert_eq!(
        parse_per_file_counts(&build.stdout),
        std::collections::BTreeMap::from([
            ("secret.txt".to_string(), 1),
            ("visible.txt".to_string(), 1),
        ]),
        "sanity: --no-ignore build should index both visible.txt and secret.txt"
    );

    // Re-query the SAME index directory, now WITHOUT --no-ignore (the default).
    let requeried = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("shared_needle")
        .arg(dir.path())
        .output()
        .unwrap();

    // H1d/D2 (audit, info-disclosure): a default query (no --no-ignore) against an
    // index built WITH --no-ignore silently reused the stale index and LEAKED
    // secret.txt's gitignored content instead of rebuilding to exclude it.
    assert!(
        requeried.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&requeried.stderr)
    );
    assert_eq!(
        parse_per_file_counts(&requeried.stdout),
        std::collections::BTreeMap::from([("visible.txt".to_string(), 1)]),
        "default query must rebuild the index and exclude the gitignored file, not leak it"
    );
}

// -- H1e: smart-case (-S) silently dropped on --index --json/--ndjson (2026-07-10) --------
//
// The 5th silent-false-negative of the same class as H1a-d, found by the adversarial gate.
// In JSON/ndjson mode `search_requires_ripgrep_passthrough` gates `smart_case` behind
// `!json && !ndjson` (main.rs), so `-S` is NOT diverted to ripgrep; route_search picks
// TrigramIndex; run_index_query passed only `args.ignore_case` (=false for `-S`) to
// index.search -> case-SENSITIVE -> an all-lowercase `-S` pattern silently missed
// uppercase matches (exit 0). Fixed by HONORING smart-case (it is index-doable): resolve
// case-sensitivity per-pattern before calling index.search.

#[test]
fn test_tg_search_explicit_index_smart_case_lowercase_pattern_matches_native() {
    let dir = tempdir().unwrap();
    fs::write(
        dir.path().join("a.txt"),
        "foo one\nFOO two\nfoo three\nbar unrelated\n",
    )
    .unwrap();

    // Native smart-case: an all-lowercase pattern is case-INSENSITIVE, so `foo` matches
    // foo/FOO/foo = 3 lines. This is the ground truth --index must match.
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
        "sanity: smart-case lowercase pattern is case-insensitive in native"
    );

    let indexed = tg()
        .arg("search")
        .arg("--index")
        .arg("--json")
        .arg("-S")
        .arg("foo")
        .arg(dir.path())
        .output()
        .unwrap();

    // BEFORE fix: run_index_query passed ignore_case=false -> case-sensitive -> 2 matches
    // (silently drops the uppercase FOO line). AFTER fix: honors smart-case -> 3, == native.
    assert!(
        indexed.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&indexed.stderr)
    );
    let indexed_json: Value = serde_json::from_slice(&indexed.stdout).unwrap();
    assert_eq!(
        indexed_json["total_matches"], native_json["total_matches"],
        "--index -S (all-lowercase pattern) must honor smart-case case-insensitivity like native, not silently return fewer matches"
    );
    assert_eq!(match_tuples(&indexed_json), match_tuples(&native_json));
}
