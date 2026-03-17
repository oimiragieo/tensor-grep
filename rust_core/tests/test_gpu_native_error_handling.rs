#![cfg(feature = "cuda")]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn parse_json_payload(stdout: &[u8]) -> Value {
    serde_json::from_slice(stdout).unwrap()
}

fn write_basic_corpus(dir: &Path) -> PathBuf {
    let corpus = dir.join("corpus");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("a.log"), "INFO ok\nERROR gpu benchmark sentinel\n").unwrap();
    corpus
}

#[test]
fn test_gpu_native_nvrtc_failure_simulation_is_user_facing() {
    let dir = tempdir().unwrap();
    let corpus = write_basic_corpus(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--fixed-strings")
        .arg("gpu benchmark sentinel")
        .arg(&corpus)
        .env("TG_TEST_CUDA_BEHAVIOR", "nvrtc-failure:simulated NVRTC compile error")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("CUDA kernel compilation failed"), "stderr={stderr}");
    assert!(stderr.contains("simulated NVRTC compile error"), "stderr={stderr}");
    assert!(!stderr.contains("panic"), "stderr={stderr}");
}

#[test]
fn test_gpu_native_invalid_device_lists_available_devices() {
    let dir = tempdir().unwrap();
    let corpus = write_basic_corpus(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("99")
        .arg("--fixed-strings")
        .arg("gpu benchmark sentinel")
        .arg(&corpus)
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("invalid CUDA device id 99"), "stderr={stderr}");
    assert!(stderr.contains("available CUDA devices"), "stderr={stderr}");
}

#[test]
fn test_gpu_native_timeout_simulation_is_user_facing() {
    let dir = tempdir().unwrap();
    let corpus = write_basic_corpus(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--fixed-strings")
        .arg("gpu benchmark sentinel")
        .arg(&corpus)
        .env("TG_TEST_CUDA_BEHAVIOR", "timeout:300ms")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("GPU operation timed out"), "stderr={stderr}");
    assert!(stderr.contains("300ms"), "stderr={stderr}");
    assert!(!stderr.contains("panic"), "stderr={stderr}");
}

#[test]
fn test_gpu_native_oom_simulation_is_user_facing() {
    let dir = tempdir().unwrap();
    let corpus = write_basic_corpus(dir.path());

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--fixed-strings")
        .arg("gpu benchmark sentinel")
        .arg(&corpus)
        .env("TG_TEST_CUDA_BEHAVIOR", "oom:13GiB")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("CUDA out of memory"), "stderr={stderr}");
    assert!(stderr.contains("13GiB"), "stderr={stderr}");
    assert!(!stderr.contains("panic"), "stderr={stderr}");
}

#[test]
fn test_gpu_native_handles_binary_empty_and_invalid_utf8_files() {
    let dir = tempdir().unwrap();
    let corpus = dir.path().join("mixed");
    fs::create_dir(&corpus).unwrap();
    fs::write(corpus.join("good.log"), "INFO ok\nERROR gpu benchmark sentinel\n").unwrap();
    fs::write(corpus.join("empty.log"), "").unwrap();
    fs::write(corpus.join("binary.bin"), b"\x00\x01\x02").unwrap();
    fs::write(
        corpus.join("invalid_utf8.log"),
        b"\xff\xfeERROR gpu benchmark sentinel\n",
    )
    .unwrap();

    let gpu_output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("gpu benchmark sentinel")
        .arg(&corpus)
        .output()
        .unwrap();

    assert!(gpu_output.status.success(), "stderr={}", String::from_utf8_lossy(&gpu_output.stderr));

    let gpu_payload = parse_json_payload(&gpu_output.stdout);
    assert!(gpu_payload["total_matches"].as_u64().unwrap() >= 1);
    assert!(gpu_payload["total_files"].as_u64().unwrap() >= 1);
}
