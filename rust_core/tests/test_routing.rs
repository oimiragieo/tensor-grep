#![allow(unused_imports, dead_code)]
use routing_support::{combined_output, repo_python, repo_root, tg};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};
use tempfile::{tempdir, TempDir};
#[path = "common/routing_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_calibrate_writes_valid_crossover_config_from_mock_results() {
    let dir = tempdir().unwrap();
    let config_path = dir.path().join("crossover.json");
    let mock_results = serde_json::json!({
        "device_name": "Mock RTX 4070",
        "measurements": [
            {
                "size_bytes": 1024_u64 * 1024,
                "cpu_samples_ms": [5.0, 6.0, 5.5],
                "gpu_samples_ms": [20.0, 21.0, 19.0]
            },
            {
                "size_bytes": 10_u64 * 1024 * 1024,
                "cpu_samples_ms": [12.0, 11.5, 12.5],
                "gpu_samples_ms": [14.0, 13.5, 14.5]
            },
            {
                "size_bytes": 100_u64 * 1024 * 1024,
                "cpu_samples_ms": [50.0, 49.0, 51.0],
                "gpu_samples_ms": [39.0, 40.0, 41.0]
            },
            {
                "size_bytes": 500_u64 * 1024 * 1024,
                "cpu_samples_ms": [240.0, 245.0, 250.0],
                "gpu_samples_ms": [145.0, 150.0, 155.0]
            },
            {
                "size_bytes": 1024_u64 * 1024 * 1024,
                "cpu_samples_ms": [500.0, 510.0, 520.0],
                "gpu_samples_ms": [250.0, 255.0, 260.0]
            }
        ]
    });

    let output = tg()
        .arg("calibrate")
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env("TG_TEST_CALIBRATION_RESULTS", mock_results.to_string())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout_payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(stdout_payload["version"], Value::from(1));
    assert_eq!(
        stdout_payload["routing_backend"],
        Value::from("Calibration")
    );
    assert_eq!(
        stdout_payload["routing_reason"],
        Value::from("manual-calibrate")
    );
    assert_eq!(stdout_payload["sidecar_used"], Value::from(false));
    assert_eq!(
        stdout_payload["corpus_size_breakpoint_bytes"],
        Value::from(100_u64 * 1024 * 1024)
    );
    assert_eq!(
        stdout_payload["recommendation"],
        Value::from("gpu_above_100mb")
    );
    assert_eq!(stdout_payload["device_name"], Value::from("Mock RTX 4070"));
    assert_eq!(stdout_payload["measurements"].as_array().unwrap().len(), 5);

    let config_payload: Value = serde_json::from_slice(&fs::read(&config_path).unwrap()).unwrap();
    assert_eq!(config_payload, stdout_payload);
}

#[test]
fn test_repeated_calibrate_overwrites_config_and_keeps_output_contract_stable() {
    let dir = tempdir().unwrap();
    let config_path = dir.path().join("crossover.json");

    let gpu_positive = serde_json::json!({
        "device_name": "Mock RTX 4070",
        "measurements": [
            {
                "size_bytes": 100_u64 * 1024 * 1024,
                "cpu_samples_ms": [50.0, 49.0, 51.0],
                "gpu_samples_ms": [39.0, 40.0, 41.0]
            }
        ]
    });
    let cpu_always = serde_json::json!({
        "device_name": "Mock RTX 4070",
        "measurements": [
            {
                "size_bytes": 100_u64 * 1024 * 1024,
                "cpu_samples_ms": [10.0, 11.0, 12.0],
                "gpu_samples_ms": [20.0, 21.0, 22.0]
            }
        ]
    });

    for (mock_results, expected_recommendation) in [
        (gpu_positive, Value::from("gpu_above_100mb")),
        (cpu_always, Value::from("cpu_always")),
    ] {
        let output = tg()
            .arg("calibrate")
            .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
            .env("TG_TEST_CALIBRATION_RESULTS", mock_results.to_string())
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );

        let stdout_payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(stdout_payload["version"], Value::from(1));
        assert_eq!(
            stdout_payload["routing_backend"],
            Value::from("Calibration")
        );
        assert_eq!(
            stdout_payload["routing_reason"],
            Value::from("manual-calibrate")
        );
        assert_eq!(stdout_payload["sidecar_used"], Value::from(false));
        assert_eq!(stdout_payload["recommendation"], expected_recommendation);

        let config_payload: Value =
            serde_json::from_slice(&fs::read(&config_path).unwrap()).unwrap();
        assert_eq!(config_payload, stdout_payload);
    }
}

#[test]
fn test_routing_default_search_prefers_ripgrep_cold_path() {
    // POLICY CHANGE, deliberately re-pinned rather than deleted. A cold plain-text search still
    // prefers ripgrep -- EXCEPT for the one shape `native_can_serve_plain_text` admits (a single
    // explicit regular file, only allow-listed flags, non-terminal stdout), which is now answered
    // in-process. Both arms are asserted here so the original guarantee is still pinned for every
    // shape where it holds, and the new one is pinned where it does not.
    //
    // The output-level question -- whether the native answer is byte-identical to rg's -- is a
    // separate oracle: tests/e2e/test_native_plain_text_parity.py.
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    // A single explicit FILE: admitted, so it now skips the rg subprocess.
    let file_output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path().join("a.txt"))
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        file_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&file_output.stderr)
    );
    let file_stderr = String::from_utf8_lossy(&file_output.stderr);
    assert_verbose_routing(&file_stderr, "NativeCpuBackend", "plain-text-native", false);

    // A DIRECTORY: refused (walking diverges from rg), so the cold path still prefers ripgrep.
    let dir_output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        dir_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&dir_output.stderr)
    );
    let dir_stderr = String::from_utf8_lossy(&dir_output.stderr);
    assert_verbose_routing(&dir_stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_directory_search_promotes_to_native_cpu() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);

    if stderr.contains("routing_backend=RipgrepBackend") {
        assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
    } else if stderr.contains("routing_reason=rg_unavailable") {
        assert_verbose_routing(&stderr, "NativeCpuBackend", "rg_unavailable", false);
    } else {
        // UNREACHABLE ARM -- not coverage of the new route, despite naming its reason string.
        // These tests pass a DIRECTORY, which `native_can_serve_plain_text` refuses (walking
        // diverges from rg on binary-file notices and on emission order), so the plain-text route
        // can never answer them. The arm was already dead on origin/main for a different reason:
        // it needed `rg_available == true`, but reaching it required falling through a branch that
        // with `rg_available` true demands `structured_output == true`, which the line above
        // catches. Only the reason STRING changed here, as part of the
        // `cpu-auto-size-threshold` -> `plain-text-native` rename; no protection was added or
        // removed. Real coverage of the new route lives in
        // `test_routing_default_search_prefers_ripgrep_cold_path` (file arm) and
        // `tests/e2e/test_native_plain_text_parity.py`.
        assert_verbose_routing(&stderr, "NativeCpuBackend", "plain-text-native", false);
    }
}

#[test]
fn test_routing_early_rg_env_preserves_plain_search_contract() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .env("TG_RUST_EARLY_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_top_level_format_rg_fixed_string_routes_before_positional_parser() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("--format")
        .arg("rg")
        .arg("--color")
        .arg("never")
        .arg("--sort")
        .arg("path")
        .arg("-n")
        .arg("-F")
        .arg("Actionable Context")
        .arg(dir.path().join("notes.md"))
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_search_format_rg_json_routes_to_ripgrep_passthrough() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--format")
        .arg("rg")
        .arg("--json")
        .arg("-F")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "native search should not handle explicit rg json",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_top_level_format_rg_json_routes_before_positional_parser() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("--format")
        .arg("rg")
        .arg("--json")
        .arg("-F")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .env(
            "TG_TEST_NATIVE_SEARCH_FORCE_ERROR",
            "native search should not handle explicit root rg json",
        )
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_top_level_type_flag_rewrites_to_search_subcommand() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_required_args_wrapper(dir.path(), &["-t", "js"]);

    let output = tg()
        .arg("-t")
        .arg("js")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_top_level_count_matches_rewrites_to_search_subcommand() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_required_args_wrapper(dir.path(), &["--count-matches"]);

    let output = tg()
        .arg("--count-matches")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_routing_early_positional_rg_env_preserves_plain_search_contract() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .env("TG_RUST_EARLY_POSITIONAL_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_routing_early_positional_rg_env_preserves_max_count_contract() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("-m")
        .arg("1")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RUST_EARLY_POSITIONAL_RG", "1")
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_routing_early_positional_rg_env_preserves_word_regexp_contract() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("-w")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RUST_EARLY_POSITIONAL_RG", "1")
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_routing_early_positional_rg_env_falls_back_for_unsupported_shapes() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("--help")
        .env("TG_RG_PATH", &rg_wrapper)
        .env("TG_RUST_EARLY_POSITIONAL_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!String::from_utf8_lossy(&output.stdout).contains(RG_SENTINEL));
}

#[test]
fn test_fast_search_binary_preserves_plain_search_contract() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg_fast()
        .arg("--no-ignore")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}

#[test]
fn test_routing_small_corpus_prefers_ripgrep_without_calibration() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 10 * 1024 * 1024);
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[cfg(feature = "cuda")]
#[test]
fn test_routing_large_corpus_auto_routes_to_gpu_native() {
    let devices = tensor_grep_rs::gpu_native::enumerate_cuda_devices();
    if devices.as_ref().map_or(true, |devices| devices.is_empty()) {
        return;
    }

    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 100 * 1024 * 1024);
    let config_path = dir.path().join("fresh-crossover.json");
    write_crossover_config(
        &config_path,
        10 * 1024 * 1024,
        90.0,
        40.0,
        "gpu_above_10mb",
        unix_timestamp_now(),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(
        &stderr,
        "NativeGpuBackend",
        "gpu-auto-size-threshold",
        false,
    );
}

#[test]
fn test_routing_large_corpus_without_calibration_prefers_ripgrep() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 60 * 1024 * 1024);
    let config_path = dir.path().join("missing-crossover.json");
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_stale_crossover_config_falls_back_to_ripgrep() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 60 * 1024 * 1024);
    let config_path = dir.path().join("stale-crossover.json");
    let rg_wrapper = write_rg_wrapper(dir.path());
    write_crossover_config(
        &config_path,
        10 * 1024 * 1024,
        80.0,
        40.0,
        "gpu_above_10mb",
        unix_timestamp_now() - (8 * 24 * 60 * 60),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_cpu_always_crossover_config_prefers_ripgrep() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 60 * 1024 * 1024);
    let config_path = dir.path().join("cpu-always-crossover.json");
    let rg_wrapper = write_rg_wrapper(dir.path());
    write_crossover_config(
        &config_path,
        1024 * 1024 * 1024,
        500.0,
        650.0,
        "cpu_always",
        unix_timestamp_now(),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[cfg(feature = "cuda")]
#[test]
fn test_routing_fresh_crossover_config_uses_calibrated_gpu_breakpoint() {
    let devices = tensor_grep_rs::gpu_native::enumerate_cuda_devices();
    if devices.as_ref().map_or(true, |devices| devices.is_empty()) {
        return;
    }

    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 20 * 1024 * 1024);
    let config_path = dir.path().join("fresh-crossover.json");
    write_crossover_config(
        &config_path,
        10 * 1024 * 1024,
        90.0,
        40.0,
        "gpu_above_10mb",
        unix_timestamp_now(),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("--verbose")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(
        &stderr,
        "NativeGpuBackend",
        "gpu-auto-size-threshold",
        false,
    );
}

#[cfg(feature = "cuda")]
#[test]
fn test_routing_large_corpus_falls_back_to_cpu_when_cuda_is_unavailable() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 100 * 1024 * 1024);
    let config_path = dir.path().join("fresh-crossover.json");
    write_crossover_config(
        &config_path,
        10 * 1024 * 1024,
        90.0,
        40.0,
        "gpu_above_10mb",
        unix_timestamp_now(),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env("TG_TEST_CUDA_BEHAVIOR", "no-devices")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "NativeCpuBackend");
    assert_eq!(payload["routing_reason"], "gpu-auto-fallback-cpu");
    assert_eq!(payload["sidecar_used"], false);

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("warning:"), "stderr={stderr}");
    assert!(stderr.contains("CUDA is unavailable"), "stderr={stderr}");
    assert!(!stderr.contains("CUDA_ERROR"), "stderr={stderr}");
}

#[cfg(feature = "cuda")]
#[test]
fn test_routing_large_corpus_gpu_init_failure_is_user_facing() {
    let dir = tempdir().unwrap();
    let corpus = write_sized_routing_corpus(dir.path(), 100 * 1024 * 1024);
    let config_path = dir.path().join("fresh-crossover.json");
    write_crossover_config(
        &config_path,
        10 * 1024 * 1024,
        90.0,
        40.0,
        "gpu_above_10mb",
        unix_timestamp_now(),
    );

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("ERROR gpu auto route")
        .arg(&corpus)
        .env("TG_CROSSOVER_CONFIG_PATH", &config_path)
        .env(
            "TG_TEST_CUDA_BEHAVIOR",
            "init-failure:driver version is too old",
        )
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
    assert!(
        stderr.contains("CUDA initialization failed"),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains("driver version is too old"),
        "stderr={stderr}"
    );
    assert!(!stderr.contains("CUDA_ERROR"), "stderr={stderr}");
    assert!(!stderr.contains("DriverError"), "stderr={stderr}");
}

#[test]
fn test_search_ndjson_emits_one_parseable_json_object_per_match() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--ndjson")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payloads = assert_ndjson_routing(&output, "NativeCpuBackend", "json_output", false);
    assert_eq!(payloads.len(), 3);

    let mut actual = payloads
        .iter()
        .map(|payload| {
            let object = payload.as_object().unwrap();
            assert!(object.contains_key("query"));
            assert!(object.contains_key("path"));
            assert!(object.contains_key("file"));
            assert!(object.contains_key("line"));
            assert!(object.contains_key("text"));
            assert!(!object.contains_key("matches"));
            assert!(!object.contains_key("total_matches"));
            (
                payload["file"].as_str().unwrap().to_owned(),
                payload["line"].as_u64().unwrap(),
                payload["text"].as_str().unwrap().to_owned(),
            )
        })
        .collect::<Vec<_>>();
    actual.sort();

    let mut expected = vec![
        (
            dir.path().join("a.txt").display().to_string(),
            1,
            "hello world".to_string(),
        ),
        (
            dir.path().join("b.txt").display().to_string(),
            2,
            "hello again friend".to_string(),
        ),
        (
            dir.path().join("notes.md").display().to_string(),
            1,
            "hello from markdown".to_string(),
        ),
    ];
    expected.sort();

    assert_eq!(actual, expected);
}

#[test]
fn test_search_ndjson_suppresses_binary_warning_for_agent_contract() {
    let dir = tempdir().unwrap();
    let text_path = dir.path().join("text.log");
    let binary_path = dir.path().join("binary.bin");
    fs::write(&text_path, "ERROR visible\n").unwrap();
    fs::write(&binary_path, b"\0ERROR hidden\0").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--ndjson")
        .arg("ERROR")
        .arg(dir.path())
        .output()
        .unwrap();

    let payloads = assert_ndjson_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(
        payloads.len(),
        2,
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert_eq!(payloads[0]["file"], text_path.display().to_string());
    assert_eq!(payloads[1]["binary_match_files"], 1);
    assert_eq!("", String::from_utf8_lossy(&output.stderr));
}

#[test]
fn test_search_single_binary_file_emits_stderr_warning_and_exit_zero() {
    let dir = tempdir().unwrap();
    let binary_path = dir.path().join("binary.bin");
    fs::write(&binary_path, b"\0ERROR hidden\0").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("ERROR")
        .arg(&binary_path)
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stdout).trim()
            == "binary file matches (found \"\\0\" byte around offset 0)",
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.trim().is_empty(), "stderr={stderr}");
}

#[test]
fn test_search_multiple_binary_files_emit_distinguishable_per_file_stderr_warnings() {
    // task #263's first defect: `emit_binary_match_warning` used to accept `path` and never use
    // it, so multiple binary files hit during one walk produced IDENTICAL, unattributed notices
    // -- there was no way to tell which file each line was about. Verified against `rg.exe`
    // 15.1.0: `rg needle bin.dat bin2.dat` prefixes each notice with `"<path>: "` (with_filename
    // is implied whenever more than one file is in play, or -- as here -- a directory is
    // walked). Deliberately does not assert an exact absolute-path string (that varies by
    // platform/temp-dir layout); it asserts the actual claim task #263 fixes: the two files'
    // notices are no longer identical, and each is attributable to its own filename.
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.bin"), b"\0ERROR hidden a\0").unwrap();
    fs::write(dir.path().join("b.bin"), b"\0ERROR hidden b\0").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("ERROR")
        .arg(dir.path())
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let lines = stdout.lines().collect::<Vec<_>>();
    assert_eq!(
        lines.len(),
        2,
        "expected one notice per binary file, got stdout={stdout}"
    );
    for line in &lines {
        assert!(
            line.contains("binary file matches (found \"\\0\" byte around offset 0)"),
            "line={line}"
        );
    }
    assert_ne!(
        lines[0], lines[1],
        "the two notices must be distinguishable (task #263), got stdout={stdout}"
    );
    assert!(
        lines.iter().any(|line| line.contains("a.bin: ")),
        "expected a notice attributed to a.bin, got stdout={stdout}"
    );
    assert!(
        lines.iter().any(|line| line.contains("b.bin: ")),
        "expected a notice attributed to b.bin, got stdout={stdout}"
    );
}

#[test]
fn test_routing_force_cpu_routes_to_native_search_even_when_rg_is_available() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 3);
    assert_ne!(String::from_utf8_lossy(&output.stdout).trim(), RG_SENTINEL);
}

#[test]
fn test_routing_json_hidden_flag_reaches_native_search() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".hidden.log"), "SECRET hidden match\n").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--json")
        .arg("--hidden")
        .arg("--fixed-strings")
        .arg("SECRET")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["matches"][0]["text"], "SECRET hidden match");
}

#[test]
fn test_routing_json_max_depth_reaches_native_search() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("root.log"), "SECRET root match\n").unwrap();
    let nested = dir.path().join("nested");
    fs::create_dir(&nested).unwrap();
    fs::write(nested.join("deep.log"), "SECRET nested match\n").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--json")
        .arg("--max-depth")
        .arg("1")
        .arg("--fixed-strings")
        .arg("SECRET")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["matches"][0]["text"], "SECRET root match");
}

#[test]
fn test_routing_json_text_flag_reaches_native_search() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("binary.dat"), b"SECRET\0binary match\n").unwrap();

    let output = tg()
        .arg("search")
        .arg("--cpu")
        .arg("--json")
        .arg("--text")
        .arg("--fixed-strings")
        .arg("SECRET")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["matches"][0]["text"], "SECRET\u{0}binary match");
}

#[test]
fn test_routing_force_cpu_alias_is_accepted() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--force-cpu")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "NativeCpuBackend", "force_cpu", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_falls_back_to_native_when_ripgrep_is_unavailable() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "NativeCpuBackend", "rg_unavailable", false);
    assert!(stdout.contains("hello world"), "stdout={stdout}");
    assert!(!stdout.contains(RG_SENTINEL), "stdout={stdout}");
}

#[test]
fn test_default_frontdoor_falls_back_to_native_when_ripgrep_is_unavailable() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("hello world"), "stdout={stdout}");
}

// --- audit #81 #7/#9: rg-unavailable must fail closed with exit 2, never a masked exit 1 or a
// silent engine swap. See rust_core/src/main.rs::require_ripgrep_or_exit and its call sites. ---

#[test]
fn test_search_required_passthrough_flag_without_ripgrep_exits_2_not_1() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    // --max-depth is in search_requires_ripgrep_passthrough's unconditional flag list, so
    // search_prefers_ripgrep_passthrough returns true regardless of rg_available. Before the fix,
    // execute_ripgrep_search's Err bubbled via `?` to main()'s default Result termination, which
    // exits 1 -- indistinguishable from a genuine no-match. It must now exit 2.
    let output = tg()
        .arg("search")
        .arg("--max-depth")
        .arg("2")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "expected exit 2 (backend unavailable), not 1 (no-match-shaped); status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.to_lowercase().contains("rg"), "stderr={stderr}");
    assert!(
        String::from_utf8_lossy(&output.stdout).trim().is_empty(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn test_search_pcre2_without_ripgrep_fails_closed_instead_of_native_swap() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    // Before the fix: search_requires_ripgrep_passthrough does not list plain `pcre2`, so
    // search_prefers_ripgrep_passthrough returns false and route_search's
    // `config.pcre2 && config.rg_available` gate silently falls through to
    // NativeCpu/"rg_unavailable" -- running the NATIVE regex engine (not PCRE2) for a `--pcre2`
    // request with no signal that PCRE2 semantics were dropped. It must now fail closed (exit 2).
    let output = tg()
        .arg("search")
        .arg("--pcre2")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--pcre2"), "stderr={stderr}");
    assert!(stderr.to_lowercase().contains("rg"), "stderr={stderr}");
    // Must NOT have silently run a native-regex search and printed matches.
    assert!(
        String::from_utf8_lossy(&output.stdout).trim().is_empty(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn test_positional_pcre2_without_ripgrep_fails_closed() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    // The root/positional shortcut (`tg PATTERN PATH`) threads --pcre2 into the same route_search
    // call as `tg search --pcre2`; it must fail the same way.
    let output = tg()
        .arg("--pcre2")
        .arg("hello")
        .arg(dir.path())
        .env("PATH", "")
        .env("TG_DISABLE_RG", "1")
        .output()
        .unwrap();

    assert_eq!(
        output.status.code(),
        Some(2),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--pcre2"), "stderr={stderr}");
}

#[test]
fn test_routing_pcre2_still_routes_to_ripgrep_when_available() {
    // Regression guard: rg-PRESENT behavior for --pcre2 must be unchanged by the audit #81 #9
    // fail-closed fix -- still routes to RipgrepBackend with reason "pcre2-required".
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--pcre2")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "pcre2-required", false);
    assert_eq!(
        normalize_newlines(&String::from_utf8_lossy(&output.stdout)),
        format!("{RG_SENTINEL}\n")
    );
}
