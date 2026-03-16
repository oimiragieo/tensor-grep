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

    assert!(output.status.success());
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
        rebuilt[4], 3,
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
