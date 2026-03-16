#![cfg(windows)]

use std::fs;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn write_corpus(dir: &std::path::Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(
        dir.join("b.txt"),
        "nothing here\nhello again friend\nend\n",
    )
    .unwrap();
    fs::write(dir.join("c.log"), "error: something failed\nok\nerror: again\n").unwrap();
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

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("hello world"), "stdout={stdout}");
    assert!(stdout.contains("hello again"), "stdout={stdout}");
    assert!(dir.path().join(".tg_index").exists(), "index file should be created");
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
    let count: usize = String::from_utf8_lossy(&output.stdout).trim().parse().unwrap();
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
    assert!(stderr.contains("routing_backend=TrigramIndex"), "stderr={stderr}");
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
    assert_eq!(mtime_1, mtime_2, "index should not be rebuilt when corpus is unchanged");
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
    let count: usize = String::from_utf8_lossy(&output.stdout).trim().parse().unwrap();
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
    let count1: usize = String::from_utf8_lossy(&out1.stdout).trim().parse().unwrap();
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
    let count2: usize = String::from_utf8_lossy(&out2.stdout).trim().parse().unwrap();
    assert_eq!(count2, 3, "should find hello in the new file too");
    let stderr = String::from_utf8_lossy(&out2.stderr);
    assert!(stderr.contains("stale") || stderr.contains("rebuilding"), "stderr={stderr}");
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
    let count: usize = String::from_utf8_lossy(&output.stdout).trim().parse().unwrap();
    assert_eq!(count, 2);
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
    let count: usize = String::from_utf8_lossy(&output.stdout).trim().parse().unwrap();
    assert_eq!(count, 2);
}
