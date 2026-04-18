use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::thread;
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;
use tempfile::{tempdir, TempDir};

const RG_SENTINEL: &str = "TG_RG_ROUTING_SENTINEL";

fn normalize_newlines(text: &str) -> String {
    text.replace("\r\n", "\n")
}

fn combined_output(output: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn tg_fast() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg-search-fast"))
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn repo_python() -> PathBuf {
    let windows = repo_root().join(".venv").join("Scripts").join("python.exe");
    if windows.exists() {
        return windows;
    }

    repo_root().join(".venv").join("bin").join("python")
}

fn write_text_corpus(dir: &Path) {
    fs::write(
        dir.join("a.txt"),
        "hello world\nfoo bar baz\ngoodbye world\n",
    )
    .unwrap();
    fs::write(dir.join("b.txt"), "nothing here\nhello again friend\nend\n").unwrap();
    fs::write(dir.join("notes.md"), "hello from markdown\n").unwrap();
}

fn write_sized_routing_corpus(dir: &Path, target_bytes: usize) -> PathBuf {
    let corpus = dir.join(format!("corpus-{target_bytes}"));
    fs::create_dir(&corpus).unwrap();

    let chunk = b"INFO steady state\nERROR gpu auto route\nWARN retry later\n";
    let mut bytes = Vec::new();
    while bytes.len() < target_bytes {
        bytes.extend_from_slice(chunk);
    }

    let file_count = 4usize;
    let chunk_size = bytes.len().div_ceil(file_count);
    for index in 0..file_count {
        let start = index * chunk_size;
        if start >= bytes.len() {
            break;
        }
        let end = ((index + 1) * chunk_size).min(bytes.len());
        fs::write(
            corpus.join(format!("chunk-{index}.log")),
            &bytes[start..end],
        )
        .unwrap();
    }

    corpus
}

fn unix_timestamp_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

fn write_crossover_config(
    path: &Path,
    breakpoint_bytes: u64,
    cpu_median_ms: f64,
    gpu_median_ms: f64,
    recommendation: &str,
    calibration_timestamp: u64,
) {
    let payload = serde_json::json!({
        "version": 1,
        "routing_backend": "Calibration",
        "routing_reason": "manual-calibrate",
        "sidecar_used": false,
        "corpus_size_breakpoint_bytes": breakpoint_bytes,
        "cpu_median_ms": cpu_median_ms,
        "gpu_median_ms": gpu_median_ms,
        "recommendation": recommendation,
        "calibration_timestamp": calibration_timestamp,
        "device_name": "Mock RTX 4070",
        "measurements": [
            {
                "size_bytes": breakpoint_bytes,
                "cpu_median_ms": cpu_median_ms,
                "gpu_median_ms": gpu_median_ms,
                "cpu_samples_ms": [cpu_median_ms],
                "gpu_samples_ms": [gpu_median_ms]
            }
        ]
    });

    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, serde_json::to_vec_pretty(&payload).unwrap()).unwrap();
}

fn write_python_source() -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(&file_path, "def add(a, b):\n    return a + b\n").unwrap();
    (dir, file_path)
}

fn write_python_wrapper(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("python-wrapper.cmd");
        fs::write(&script, "@echo off\r\necho %*\r\n").unwrap();
        script
    } else {
        let script = dir.join("python-wrapper.sh");
        fs::write(&script, "#!/bin/sh\nprintf '%s\\n' \"$*\"\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            let mut permissions = fs::metadata(&script).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&script, permissions).unwrap();
        }
        script
    }
}

fn build_index(dir: &Path) {
    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--count")
        .arg("hello")
        .arg(dir)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn write_rg_wrapper(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("rg-wrapper.cmd");
        fs::write(&script, format!("@echo off\r\necho {RG_SENTINEL}\r\n")).unwrap();
        script
    } else {
        let script = dir.join("rg-wrapper.sh");
        fs::write(&script, format!("#!/bin/sh\nprintf '{RG_SENTINEL}\\n'\n")).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            let mut permissions = fs::metadata(&script).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&script, permissions).unwrap();
        }
        script
    }
}

fn write_mock_gpu_sidecar_script(dir: &Path, matched_file: &Path, marker: &Path) -> PathBuf {
    let script = dir.join("mock_gpu_sidecar.py");
    fs::write(
        &script,
        format!(
            "import json\nimport os\nimport pathlib\nimport sys\nrequest = json.loads(sys.stdin.buffer.read())\npathlib.Path(r\"{}\").write_text('invoked', encoding='utf-8')\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"matches\": [{{\"file\": {:?}, \"line_number\": 1, \"text\": \"hello world\"}}]}}) + '\\n', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
            marker.display(),
            matched_file.display().to_string(),
        ),
    )
    .unwrap();
    script
}

fn assert_verbose_routing(stderr: &str, backend: &str, reason: &str, sidecar_used: bool) {
    assert!(
        stderr.contains(&format!("routing_backend={backend}")),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains(&format!("routing_reason={reason}")),
        "stderr={stderr}"
    );
    assert!(
        stderr.contains(&format!("sidecar_used={sidecar_used}")),
        "stderr={stderr}"
    );
}

fn assert_json_routing(output: &Output, backend: &str, reason: &str, sidecar_used: bool) -> Value {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], backend);
    assert_eq!(payload["routing_reason"], reason);
    assert_eq!(payload["sidecar_used"], sidecar_used);
    payload
}

fn assert_ndjson_routing(
    output: &Output,
    backend: &str,
    reason: &str,
    sidecar_used: bool,
) -> Vec<Value> {
    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    let payloads = stdout
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();

    assert!(!payloads.is_empty(), "stdout={stdout}");

    for payload in &payloads {
        assert_eq!(payload["routing_backend"], backend);
        assert_eq!(payload["routing_reason"], reason);
        assert_eq!(payload["sidecar_used"], sidecar_used);
    }

    payloads
}

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
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path().join("a.txt"))
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
        assert_verbose_routing(
            &stderr,
            "NativeCpuBackend",
            "cpu-auto-size-threshold",
            false,
        );
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
fn test_search_ndjson_keeps_stdout_json_when_binary_warning_is_emitted() {
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
        1,
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert_eq!(payloads[0]["file"], text_path.display().to_string());

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains(&format!("Binary file {} matches", binary_path.display())),
        "stderr={stderr}"
    );
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
        output.stdout.is_empty(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(
        stderr.trim(),
        format!("Binary file {} matches", binary_path.display())
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

#[test]
fn test_search_json_and_ndjson_are_mutually_exclusive() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--json")
        .arg("--ndjson")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--json"), "stderr={stderr}");
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
}

#[test]
fn test_routing_explicit_index_uses_trigram_index_json() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_json_prefers_warm_index_even_when_json_is_requested() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 3);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_invert_match() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-v")
        .arg("--verbose")
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_context_lines() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-C")
        .arg("1")
        .arg("--verbose")
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_explicit_gpu_device_ids_use_gpu_sidecar() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let marker = dir.path().join("gpu-sidecar-marker.txt");
    let matched_file = dir.path().join("a.txt");
    let sidecar_script = write_mock_gpu_sidecar_script(dir.path(), &matched_file, &marker);

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("hello")
        .arg(dir.path())
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &sidecar_script)
        .output()
        .unwrap();

    if cfg!(feature = "cuda") {
        let payload = assert_json_routing(
            &output,
            "NativeGpuBackend",
            "gpu-device-ids-explicit-native",
            false,
        );
        assert_eq!(payload["total_matches"], 4);
        assert!(
            !marker.exists(),
            "native GPU routing should not invoke the Python sidecar"
        );
    } else {
        let payload = assert_json_routing(&output, "GpuSidecar", "gpu-device-ids-explicit", true);
        assert_eq!(payload["total_matches"], 1);
        assert!(marker.exists(), "expected mock GPU sidecar invocation");
    }
}

#[test]
fn test_routing_tg_run_uses_ast_backend() {
    let (_dir, file_path) = write_python_source();

    let output = tg()
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--json")
        .arg("def $F($$$ARGS): $$$BODY")
        .arg(&file_path)
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "AstBackend", "ast-native", false);
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_tg_run_rewrite_rejects_ndjson_without_python() {
    let bogus_python_home = tempdir().unwrap();
    let (_dir, file_path) = write_python_source();

    let output = tg()
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--ndjson")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .env("PYTHONHOME", bogus_python_home.path())
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("--ndjson"), "stderr={stderr}");
    assert!(
        stderr.contains("unexpected")
            || stderr.contains("unknown")
            || stderr.contains("found argument"),
        "stderr={stderr}"
    );
}

#[test]
fn test_routing_warm_index_is_bypassed_by_short_pattern() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--verbose")
        .arg("he")
        .arg(dir.path().join("a.txt"))
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
fn test_routing_warm_index_is_bypassed_by_word_regexp() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-w")
        .arg("--verbose")
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_glob_filter() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("-g")
        .arg("*.txt")
        .arg("--verbose")
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_warm_index_is_bypassed_by_max_count() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--fixed-strings")
        .arg("--max-count")
        .arg("1")
        .arg("--verbose")
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_verbose_routing(&stderr, "RipgrepBackend", "rg_passthrough", false);
}

#[test]
fn test_routing_stale_index_with_explicit_index_rebuilds() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    build_index(dir.path());

    let index_path = dir.path().join(".tg_index");
    let before = index_path.metadata().unwrap().modified().unwrap();

    thread::sleep(Duration::from_millis(50));
    fs::write(dir.path().join("fresh.txt"), "hello from rebuilt index\n").unwrap();

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 4);

    let after = index_path.metadata().unwrap().modified().unwrap();
    assert!(
        after > before,
        "expected stale index rebuild to update mtime"
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("stale") || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
}

#[test]
fn test_routing_explicit_index_rebuilds_corrupt_index() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    fs::write(dir.path().join(".tg_index"), b"corrupt-index").unwrap();

    let output = tg()
        .arg("search")
        .arg("--index")
        .arg("--fixed-strings")
        .arg("--json")
        .arg("--verbose")
        .arg("hello")
        .arg(dir.path())
        .output()
        .unwrap();

    let payload = assert_json_routing(&output, "TrigramIndex", "index-accelerated", false);
    assert_eq!(payload["total_matches"], 3);

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("failed to load index") || stderr.contains("rebuilding"),
        "stderr={stderr}"
    );
}

#[test]
fn test_routing_directory_count_search_uses_native_cpu_without_fallback() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("search")
        .arg("--verbose")
        .arg("-c")
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
        assert_verbose_routing(
            &stderr,
            "NativeCpuBackend",
            "cpu-auto-size-threshold",
            false,
        );
    }

    // Should NOT contain the fallback warning
    assert!(
        !stderr.contains("warning: native CPU search failed, falling back to ripgrep"),
        "Detected unexpected native CPU fallback in stderr: {}",
        stderr
    );

    // Verify output matches
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains(":1\n") || stdout.contains(":1\r\n"),
        "stdout={}",
        stdout
    );
}

#[test]
fn test_routing_external_editor_plane_commands_are_forwarded() {
    let scenarios = vec!["map", "session", "doctor"];
    let dir = tempdir().unwrap();
    let python_wrapper = write_python_wrapper(dir.path());

    for command in scenarios {
        let output = tg()
            .current_dir(repo_root())
            .arg(command)
            .arg("--help")
            .env("TG_SIDECAR_PYTHON", &python_wrapper)
            .output()
            .unwrap();

        let stdout = combined_output(&output);
        assert!(
            stdout.contains(&format!("-m tensor_grep {}", command)),
            "External command '{}' was not forwarded properly. stdout={}",
            command,
            stdout
        );
    }
}

#[test]
fn test_routing_native_editor_plane_commands() {
    let dir = tempdir().unwrap();
    let python_wrapper = write_python_wrapper(dir.path());

    for command in vec!["defs", "refs", "context"] {
        let output = tg()
            .current_dir(repo_root())
            .arg(command)
            .arg("--help")
            .env("TG_SIDECAR_PYTHON", &python_wrapper)
            .output()
            .unwrap();

        let stdout = combined_output(&output);
        assert!(
            stdout.contains(&format!("-m tensor_grep {}", command)),
            "Native editor-plane command '{}' was not forwarded properly. output={}",
            command,
            stdout
        );
        assert!(
            stdout.contains("--help"),
            "Native editor-plane command '{}' did not forward the help flag. output={}",
            command,
            stdout
        );
    }
}

#[test]
fn test_routing_ast_workflow_commands_are_native() {
    for command in vec!["scan", "test", "new"] {
        let output = tg().arg(command).arg("--help").output().unwrap();

        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.to_lowercase().contains("usage:"));
        assert!(stdout.contains(command));
    }
}

#[test]
fn test_routing_explicit_gpu_device_ids_override_warm_index() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_text_corpus(&corpus_dir);
    build_index(&corpus_dir);

    let marker = dir.path().join("gpu-sidecar-priority-marker.txt");
    let matched_file = corpus_dir.join("a.txt");
    let sidecar_script = write_mock_gpu_sidecar_script(dir.path(), &matched_file, &marker);

    let output = tg()
        .current_dir(repo_root())
        .arg("search")
        .arg("--fixed-strings")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("hello")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &sidecar_script)
        .output()
        .unwrap();

    if cfg!(feature = "cuda") {
        let payload = assert_json_routing(
            &output,
            "NativeGpuBackend",
            "gpu-device-ids-explicit-native",
            false,
        );
        assert_eq!(payload["total_matches"], 3);
        assert!(
            !marker.exists(),
            "native GPU routing should bypass the Python sidecar"
        );
    } else {
        let payload = assert_json_routing(&output, "GpuSidecar", "gpu-device-ids-explicit", true);
        assert_eq!(payload["total_matches"], 1);
        assert!(marker.exists(), "expected mock GPU sidecar invocation");
    }
}

#[test]
fn test_rust_control_plane_plain_explicit() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Should use the Rust control plane and dispatch to rg
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_plain_positional() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_rejection() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    // -F should fall through to full stack.
    // Full stack with --verbose will show a reason.
    let output_f = tg()
        .arg("--verbose")
        .arg("-F")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    let stderr = String::from_utf8_lossy(&output_f.stderr);
    // If it fell through to Clap, it will process --verbose and print routing info.
    // The fast path would have silently dispatched without printing [routing]...
    assert!(stderr.contains("[routing]"));
    assert!(stderr.contains("routing_backend=RipgrepBackend"));

    // --help should fall through
    let output_help = tg().arg("--help").output().unwrap();
    assert!(String::from_utf8_lossy(&output_help.stdout).contains("Usage:"));

    // scan should fall through
    let output_scan = tg().arg("scan").arg("--help").output().unwrap();
    let stdout_scan = String::from_utf8_lossy(&output_scan.stdout);
    assert!(stdout_scan.to_lowercase().contains("usage:"));
    assert!(stdout_scan.contains("scan"));
}

#[test]
fn test_rust_control_plane_native_fallback() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("hello")
        .arg(dir.path())
        .env("TG_DISABLE_RG", "1")
        .env("PATH", "") // Ensure rg is not in path
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("hello world"));
    assert!(!stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_no_match_exit_code() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());

    let output = tg()
        .arg("non_existent_pattern")
        .arg(dir.path())
        .output()
        .unwrap();

    assert_eq!(output.status.code(), Some(1));
}

#[test]
fn test_rust_control_plane_combined_flags() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("-iv")
        .arg("non_existent")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_no_ignore() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("--no-ignore")
        .arg("hello")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_case_insensitive_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("-i")
        .arg("warning")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_max_count_search_dispatches_to_ripgrep() {
    let dir = tempdir().unwrap();
    write_text_corpus(dir.path());
    let rg_wrapper = write_rg_wrapper(dir.path());

    let output = tg()
        .arg("search")
        .arg("-m")
        .arg("5")
        .arg("ERROR")
        .arg(dir.path())
        .env("TG_RG_PATH", &rg_wrapper)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains(RG_SENTINEL));
}

#[test]
fn test_rust_control_plane_version() {
    let output_v_long = tg().arg("--version").output().unwrap();
    assert!(String::from_utf8_lossy(&output_v_long.stdout).contains("tg 0.2.0"));

    let output_v_short = tg().arg("-V").output().unwrap();
    assert!(String::from_utf8_lossy(&output_v_short.stdout).contains("tg 0.2.0"));
}
