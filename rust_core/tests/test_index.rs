#![cfg(windows)]

use std::fs;
use std::path::Path;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::index::TrigramIndex;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn write_corpus(dir: &std::path::Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(dir.join("b.txt"), "nothing here\nhello again friend\nend\n").unwrap();
    fs::write(
        dir.join("c.log"),
        "error: something failed\nok\nerror: again\n",
    )
    .unwrap();
}

/// `TrigramIndex`'s file walker (`collect_file_entries` / `staleness_reason`) does not set
/// `.require_git(false)`, so it inherits the `ignore` crate's default of only honoring
/// `.gitignore` inside a directory the crate recognizes as an actual git repository. A bare
/// `tempdir()` has no `.git`, so `.gitignore` is silently a no-op there regardless of
/// `--no-ignore` -- returns `false` (and the caller should skip the test) when `git` itself
/// isn't on PATH, mirroring the existing precedent in test_ast_rewrite.rs.
fn init_git_repo(dir: &std::path::Path) -> bool {
    if Command::new("git").arg("--version").output().is_err() {
        return false;
    }
    let status = Command::new("git")
        .arg("init")
        .current_dir(dir)
        .output()
        .unwrap();
    status.status.success()
}

fn write_regex_prefilter_corpus(dir: &std::path::Path) {
    fs::write(
        dir.join("alternation.txt"),
        "foo branch\nbar branch\nbaz branch\n",
    )
    .unwrap();
    fs::write(
        dir.join("classes.txt"),
        "adef branch\nbdef branch\ncdef branch\ndeaf branch\ndebf branch\ndecf branch\nzzzdef noise\n",
    )
    .unwrap();
    fs::write(
        dir.join("unicode.txt"),
        "東京 terminal\n大阪 station\n京都 garden\n",
    )
    .unwrap();
}

fn decode_hex_trigram(key: &str) -> [u8; 3] {
    fn byte_at(key: &str, offset: usize) -> u8 {
        u8::from_str_radix(&key[offset..offset + 2], 16).unwrap()
    }

    [byte_at(key, 0), byte_at(key, 2), byte_at(key, 4)]
}

fn write_legacy_v1_index(dir: &Path) {
    let index = TrigramIndex::build(dir).unwrap();
    let json_path = dir.join("legacy-index.json");
    index.save_json(&json_path).unwrap();

    let payload: Value = serde_json::from_slice(&fs::read(&json_path).unwrap()).unwrap();
    fs::remove_file(&json_path).unwrap();

    let mut buf = Vec::new();
    buf.extend_from_slice(b"TGI\x00");
    buf.push(1);

    let root_bytes = dir.to_string_lossy().as_bytes().to_vec();
    buf.extend_from_slice(&(root_bytes.len() as u32).to_le_bytes());
    buf.extend_from_slice(&root_bytes);

    let files = payload["files"].as_array().unwrap();
    buf.extend_from_slice(&(files.len() as u32).to_le_bytes());
    for entry in files {
        let path = entry["path"].as_str().unwrap().as_bytes().to_vec();
        buf.extend_from_slice(&(path.len() as u32).to_le_bytes());
        buf.extend_from_slice(&path);
        buf.extend_from_slice(&(entry["mtime_ns"].as_u64().unwrap() as u128).to_le_bytes());
        buf.extend_from_slice(&entry["size"].as_u64().unwrap().to_le_bytes());
    }

    let postings = payload["postings"].as_object().unwrap();
    buf.extend_from_slice(&(postings.len() as u32).to_le_bytes());
    for (key, value) in postings {
        buf.extend_from_slice(&decode_hex_trigram(key));
        let entries = value.as_array().unwrap();
        buf.extend_from_slice(&(entries.len() as u32).to_le_bytes());
        for entry in entries {
            buf.extend_from_slice(&(entry["file_id"].as_u64().unwrap() as u32).to_le_bytes());
            buf.extend_from_slice(&(entry["line"].as_u64().unwrap() as u32).to_le_bytes());
        }
    }

    fs::write(dir.join(".tg_index"), buf).unwrap();
}

fn match_tuples(payload: &Value) -> Vec<(String, u64, String)> {
    payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| {
            (
                entry["file"].as_str().unwrap().to_string(),
                entry["line"].as_u64().unwrap(),
                entry["text"].as_str().unwrap().to_string(),
            )
        })
        .collect()
}

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
        dir.path().join(".tg_index").exists(),
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
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 2);
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

    let index_path = dir.path().join(".tg_index");
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

    // Audit fix #1 fold-in (a): rg exit-parity. --count still prints "0", but the process must
    // now exit 1 (not 0) on zero matches, matching the native CPU/GPU engines
    // (run_native_search_with_optional_rg_fallback / emit_multi_pattern_native_results), which
    // already did this -- run_index_query previously always returned Ok(()) (exit 0) regardless
    // of match count. See test_tg_search_index_count_no_match_exits_one_rg_parity and
    // test_tg_search_index_plain_no_match_exits_one_rg_parity below for the dedicated coverage.
    assert_eq!(
        output.status.code(),
        Some(1),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 0);
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
    let count1: usize = String::from_utf8_lossy(&out1.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count1, 2);

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
    let count2: usize = String::from_utf8_lossy(&out2.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count2, 3, "should find hello in the new file too");
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
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 2);
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
    assert!(dir.path().join(".tg_index").exists());

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
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 2);
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
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 2);

    let rebuilt = fs::read(dir.path().join(".tg_index")).unwrap();
    assert_eq!(&rebuilt[0..4], b"TGI\x00");
    assert_eq!(
        rebuilt[4], 4,
        "expected rebuilt index to use the new format"
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
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(count, 2);
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
    let build_count: usize = String::from_utf8_lossy(&build.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        build_count, 1,
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
    let requeried_count: usize = String::from_utf8_lossy(&requeried.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        requeried_count, 2,
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
    let build_count: usize = String::from_utf8_lossy(&build.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        build_count, 2,
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
    let requeried_count: usize = String::from_utf8_lossy(&requeried.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        requeried_count, 1,
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
    assert!(!dir.path().join(".tg_index").exists());
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
        !dir.path().join(".tg_index").exists(),
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
    assert!(dir.path().join(".tg_index").exists());

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
    // Deliberately EXCLUDES require_git, passthru, null_data, multiline(+dotall), and the
    // no_ignore_dot/exclude/files/global/parent family: these are all separately intercepted
    // even earlier, by `search_format_python_passthrough_args`'s SEARCH_PYTHON_PASSTHROUGH_FLAGS
    // allowlist (require_git, unconditionally) or its `--json`-gated third/fourth checks
    // (passthru/no_ignore_dot/.../no_config; -U/multiline/multiline-dotall/null-data) -- BEFORE
    // `--index` is even parsed by clap. In an environment with a working Python sidecar on PATH,
    // that pre-existing, unrelated dispatcher forwards the raw args to Python, which has no
    // `--index` concept at all and errors out ("No such option '--index'") rather than reaching
    // this binary's own routing. That is a real, separate gap in
    // `search_format_python_passthrough_args` (main.rs) worth its own audit item; it is NOT
    // something `index_flag_violations` can address (it never runs). These 9 flags' Refuse
    // classification is still covered by the direct-call unit tests in main.rs's `mod tests`
    // (`index_flag_violations_catches_flags_outside_the_original_six`), which call the function
    // directly and are unaffected by CLI/Python dispatch.
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
            !output.status.success(),
            "expected --index {extra:?} to fail closed; stdout={} stderr={}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
        assert!(
            stderr.contains(needle),
            "error for {extra:?} should name the incompatible flag (expected {needle:?}): stderr={stderr}"
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
    assert!(dir.path().join(".tg_index").exists());

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

    // fold-in (a): native CPU/GPU already exit(1) on zero matches; run_index_query previously
    // always returned Ok(()) (exit 0) regardless of match count.
    assert_eq!(output.status.code(), Some(1));
    let count: usize = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        count, 0,
        "the count itself is unaffected, only the exit code"
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
