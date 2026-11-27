use std::fs;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

#[test]
fn test_gpu_device_ids_json_cpu_fallback_emits_valid_json_envelope() {
    let dir = tempdir().unwrap();
    let sample = dir.path().join("sample.rs");
    fs::write(&sample, "fn gpu_routing_test() {}\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .args([
            "search",
            "gpu_routing_test",
            sample.to_str().unwrap(),
            "--gpu-device-ids",
            "0",
            "--json",
        ])
        .env_remove("TG_SIDECAR_PYTHON")
        .env_remove("TG_SIDECAR_SCRIPT")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!output.stdout.is_empty(), "stdout must never be 0 B");
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["version"], 1);
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
    assert_eq!(payload["gpu_proof"], false);
}
