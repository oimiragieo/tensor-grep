use std::fs;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

#[test]
fn multi_pattern_ndjson_text_keeps_line_order_and_fast_path() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("text.txt");
    fs::write(&path, "beta\nalpha\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .args([
            "search",
            "--cpu",
            "--fixed-strings",
            "--ndjson",
            "-e",
            "alpha",
            "-e",
            "beta",
        ])
        .arg(&path)
        .env("TG_TEST_NATIVE_SEARCH_FORCE_ERROR", "sequential path used")
        .output()
        .unwrap();

    assert!(output.status.success());
    let records: Vec<Value> = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(records[0]["line"], 1);
    assert_eq!(records[0]["pattern_id"], 1);
    assert_eq!(records[1]["line"], 2);
    assert_eq!(records[1]["pattern_id"], 0);
}

#[test]
fn multi_pattern_binary_json_and_ndjson_report_count_and_routing() {
    let dir = tempdir().unwrap();
    let binary_path = dir.path().join("binary.bin");
    fs::write(&binary_path, b"\0needle other\0").unwrap();

    for format in ["--json", "--ndjson"] {
        let output = Command::new(env!("CARGO_BIN_EXE_tg"))
            .arg("search")
            .arg("--cpu")
            .arg("--fixed-strings")
            .arg(format)
            .arg("-e")
            .arg("needle")
            .arg("-e")
            .arg("other")
            .arg(&binary_path)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "format={format} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(payload["binary_match_files"], 1);
        assert_eq!(payload["routing_backend"], "NativeCpuBackend");
        assert_eq!(payload["routing_reason"], "force_cpu");
        assert_eq!(payload["sidecar_used"], false);
        if format == "--json" {
            assert_eq!(payload["total_matches"], 0);
        } else {
            assert_eq!(payload["type"], "summary");
            assert_eq!(payload["total_matches"], 0);
        }
    }
}
