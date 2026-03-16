#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;
use tensor_grep_rs::gpu_native::enumerate_cuda_devices;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn parse_match_tuple(payload: &Value) -> (String, usize, String) {
    let file = payload["file"].as_str().unwrap().to_string();
    let line_number = payload["line"]
        .as_u64()
        .or_else(|| payload["line_number"].as_u64())
        .unwrap() as usize;
    let text = payload["text"].as_str().unwrap().to_string();
    (file, line_number, text)
}

fn parse_json_tuples(stdout: &[u8]) -> Vec<(String, usize, String)> {
    let payload: Value = serde_json::from_slice(stdout).unwrap();
    let mut tuples = payload["matches"]
        .as_array()
        .unwrap()
        .iter()
        .map(parse_match_tuple)
        .collect::<Vec<_>>();
    tuples.sort();
    tuples
}

fn parse_json_payload(stdout: &[u8]) -> Value {
    serde_json::from_slice(stdout).unwrap()
}

fn write_single_file_fixture(dir: &Path) -> PathBuf {
    let file_path = dir.join("single.log");
    fs::write(
        &file_path,
        "INFO boot\nERROR first failure\nWARN retry\nERROR second failure\n",
    )
    .unwrap();
    file_path
}

fn write_multi_file_fixture(dir: &Path) -> PathBuf {
    let corpus = dir.join("corpus");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("a.log"), "INFO ok\nERROR alpha\n").unwrap();
    fs::write(corpus.join("b.log"), "WARN beta\nERROR beta\n").unwrap();
    fs::write(corpus.join("c.log"), "INFO gamma\nERROR gamma\n").unwrap();
    corpus
}

fn write_boundary_fixture(dir: &Path) -> PathBuf {
    let corpus = dir.join("boundary");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("a.txt"), "prefix ABC").unwrap();
    fs::write(corpus.join("b.txt"), "D standalone should not match\nABCD real match\n").unwrap();
    corpus
}

fn write_gpu_parity_corpus(dir: &Path, target_bytes: usize) -> PathBuf {
    let corpus = dir.join("gpu-parity-corpus");
    fs::create_dir(&corpus).unwrap();

    let mut content = String::new();
    let lines = [
        "INFO steady state\n",
        "ERROR critical path failed\n",
        "WARN retry budget exhausted\n",
        "Database connection timeout\n",
        "unicode café line\n",
        "日本語 sentinel line\n",
        "emoji 🔍 marker\n",
    ];

    while content.len() < target_bytes {
        for line in &lines {
            content.push_str(line);
        }
    }

    let bytes = content.into_bytes();
    let chunk_size = bytes.len() / 6;
    for index in 0..6 {
        let start = index * chunk_size;
        let end = if index == 5 {
            bytes.len()
        } else {
            (index + 1) * chunk_size
        };
        fs::write(corpus.join(format!("chunk-{index}.log")), &bytes[start..end]).unwrap();
    }

    corpus
}

#[test]
fn test_gpu_native_single_file_json_routes_to_native_backend() {
    let dir = tempdir().unwrap();
    let file_path = write_single_file_fixture(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ERROR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let payload = parse_json_payload(&output.stdout);
    assert_eq!(payload["routing_backend"], "gpu_native");
    assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit-native");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["total_files"], 1);
    assert_eq!(payload["total_matches"], 2);
}

#[test]
fn test_gpu_native_directory_search_batches_files_and_matches_cpu_output() {
    let dir = tempdir().unwrap();
    let corpus = write_multi_file_fixture(dir.path());

    let gpu_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("--verbose")
        .arg("ERROR")
        .arg(&corpus)
        .output()
        .unwrap();

    let cpu_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--cpu")
        .arg("--json")
        .arg("ERROR")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(gpu_output.status.success(), "stderr={}", String::from_utf8_lossy(&gpu_output.stderr));
    assert!(cpu_output.status.success(), "stderr={}", String::from_utf8_lossy(&cpu_output.stderr));

    let gpu_payload = parse_json_payload(&gpu_output.stdout);
    assert_eq!(gpu_payload["routing_backend"], "gpu_native");
    assert_eq!(gpu_payload["routing_reason"], "gpu-device-ids-explicit-native");
    assert_eq!(gpu_payload["sidecar_used"], false);
    assert_eq!(gpu_payload["total_files"], 3);
    assert_eq!(gpu_payload["total_matches"], 3);

    let stderr = String::from_utf8_lossy(&gpu_output.stderr);
    assert!(stderr.contains("gpu_batch_files=3"), "stderr={stderr}");
    assert!(stderr.contains("gpu_transfer_bytes="), "stderr={stderr}");

    assert_eq!(parse_json_tuples(&gpu_output.stdout), parse_json_tuples(&cpu_output.stdout));
}

#[test]
fn test_gpu_native_search_avoids_cross_file_boundary_matches() {
    let dir = tempdir().unwrap();
    let corpus = write_boundary_fixture(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ABCD")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));

    let payload = parse_json_payload(&output.stdout);
    assert_eq!(payload["total_matches"], 1);
    let matches = payload["matches"].as_array().unwrap();
    assert_eq!(matches.len(), 1);
    let tuple = parse_match_tuple(&matches[0]);
    assert!(tuple.0.ends_with("b.txt"), "match tuple={tuple:?}");
    assert_eq!(tuple.1, 2);
    assert_eq!(tuple.2, "ABCD real match");
}

#[test]
fn test_gpu_native_matches_cpu_for_five_patterns_on_ten_mb_corpus() {
    let dir = tempdir().unwrap();
    let corpus = write_gpu_parity_corpus(dir.path(), 10 * 1024 * 1024);
    let patterns = [
        "ERROR critical path failed",
        "WARN retry budget exhausted",
        "Database connection timeout",
        "café",
        "日本語",
    ];

    for pattern in patterns {
        let gpu_output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--fixed-strings")
            .arg("--gpu-device-ids")
            .arg("0")
            .arg("--json")
            .arg(pattern)
            .arg(&corpus)
            .output()
            .unwrap();
        let cpu_output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--fixed-strings")
            .arg("--cpu")
            .arg("--json")
            .arg(pattern)
            .arg(&corpus)
            .output()
            .unwrap();

        assert!(gpu_output.status.success(), "pattern={pattern} stderr={}", String::from_utf8_lossy(&gpu_output.stderr));
        assert!(cpu_output.status.success(), "pattern={pattern} stderr={}", String::from_utf8_lossy(&cpu_output.stderr));

        assert_eq!(parse_json_tuples(&gpu_output.stdout), parse_json_tuples(&cpu_output.stdout), "pattern={pattern}");
    }
}

#[test]
fn test_gpu_native_verbose_output_reports_selected_devices() {
    let devices = enumerate_cuda_devices().unwrap();
    if devices.len() < 2 {
        return;
    }

    let dir = tempdir().unwrap();
    let file_path = write_single_file_fixture(dir.path());

    for device in devices.iter().take(2) {
        let output = tg()
            .current_dir(repo_root())
            .arg("search")
            .arg("--gpu-device-ids")
            .arg(device.device_id.to_string())
            .arg("--json")
            .arg("--verbose")
            .arg("ERROR")
            .arg(&file_path)
            .output()
            .unwrap();

        assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            stderr.contains(&format!("selected_gpu_device_id={}", device.device_id)),
            "stderr={stderr}"
        );
        assert!(stderr.contains(&device.name), "stderr={stderr}");
    }
}
