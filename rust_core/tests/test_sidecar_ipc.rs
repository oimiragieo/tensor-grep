use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

#[cfg(not(feature = "cuda"))]
use std::time::Instant;

use serde_json::json;
use tempfile::tempdir;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn repo_python() -> PathBuf {
    let candidate = repo_root().join(".venv").join("Scripts").join("python.exe");
    if candidate.exists() {
        return candidate;
    }

    if cfg!(windows) {
        return PathBuf::from("python");
    }

    let unix_candidate = repo_root().join(".venv").join("bin").join("python");
    if unix_candidate.exists() {
        return unix_candidate;
    }

    PathBuf::from("python")
}

fn configure_repo_python_env(command: &mut Command) {
    command.env("PYTHONPATH", repo_root().join("src"));
}

fn repo_python_has_module(module: &str) -> bool {
    let output = Command::new(repo_python())
        .current_dir(repo_root())
        .arg("-c")
        .arg(format!("import {module}"))
        .output()
        .unwrap();
    output.status.success()
}

fn isolated_tg_binary(dir: &Path) -> PathBuf {
    let binary_name = if cfg!(windows) { "tg.exe" } else { "tg" };
    let target = dir.join(binary_name);
    fs::copy(env!("CARGO_BIN_EXE_tg"), &target).unwrap();
    target
}

fn run_with_timeout(mut command: Command, timeout: Duration) -> Output {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let _ = tx.send(command.output());
    });

    match rx.recv_timeout(timeout) {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => panic!("command failed: {err}"),
        Err(_) => panic!("command timed out after {timeout:?}"),
    }
}

fn run_with_stdin_timeout(mut command: Command, stdin_bytes: Vec<u8>, timeout: Duration) -> Output {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let result = (|| -> std::io::Result<Output> {
            command
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            let mut child = command.spawn()?;
            if let Some(mut stdin) = child.stdin.take() {
                stdin.write_all(&stdin_bytes)?;
            }
            child.wait_with_output()
        })();

        let _ = tx.send(result);
    });

    match rx.recv_timeout(timeout) {
        Ok(Ok(output)) => output,
        Ok(Err(err)) => panic!("command failed: {err}"),
        Err(_) => panic!("command timed out after {timeout:?}"),
    }
}

fn write_sample_log(dir: &Path) -> PathBuf {
    let file_path = dir.join("sample.log");
    fs::write(
        &file_path,
        "INFO ok\nERROR database failed\nWARN retrying\nINFO recovered\n",
    )
    .unwrap();
    file_path
}

fn large_classify_payload(target_bytes: usize) -> String {
    let mut content = String::new();
    let line = "ERROR suspicious payload detected on sidecar transport 0123456789abcdef\n";
    while content.len() < target_bytes {
        content.push_str(line);
    }
    content
}

fn configure_classify_env(command: &mut Command) {
    command
        .env("TENSOR_GREP_TRITON_TIMEOUT_SECONDS", "0.01")
        .env("HF_HUB_OFFLINE", "1")
        .env("TRANSFORMERS_OFFLINE", "1");
    configure_repo_python_env(command);
}

fn sidecar_test_timeout() -> Duration {
    if cfg!(windows) {
        Duration::from_secs(15)
    } else {
        Duration::from_secs(5)
    }
}

#[cfg(not(feature = "cuda"))]
fn wait_for_pid_file(path: &Path, timeout: Duration) -> u32 {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(contents) = fs::read_to_string(path) {
            if let Ok(pid) = contents.trim().parse::<u32>() {
                return pid;
            }
        }
        thread::sleep(Duration::from_millis(50));
    }

    panic!("pid file {:?} was not populated within {:?}", path, timeout);
}

#[cfg(not(feature = "cuda"))]
fn is_pid_running(pid: u32) -> bool {
    if cfg!(windows) {
        let output = Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
            .output()
            .unwrap();
        let stdout = String::from_utf8_lossy(&output.stdout);
        stdout.contains(&format!("\"{pid}\","))
    } else {
        Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
}

#[cfg(not(feature = "cuda"))]
fn wait_for_process_exit(pid: u32, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !is_pid_running(pid) {
            return true;
        }
        thread::sleep(Duration::from_millis(50));
    }
    !is_pid_running(pid)
}

#[test]
fn test_tg_classify_stdout_matches_python_module() {
    if !repo_python_has_module("typer") {
        return;
    }

    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root()).arg("classify").arg(&file_path);
    configure_classify_env(&mut tg);
    let tg_output = run_with_timeout(tg, Duration::from_secs(40));

    let mut py = Command::new(repo_python());
    py.current_dir(repo_root())
        .arg("-m")
        .arg("tensor_grep")
        .arg("classify")
        .arg(&file_path);
    configure_classify_env(&mut py);
    let py_output = run_with_timeout(py, Duration::from_secs(40));

    assert_eq!(tg_output.status.code(), py_output.status.code());
    assert_eq!(tg_output.stdout, py_output.stdout);
    assert_eq!(tg_output.stderr, py_output.stderr);
}

#[test]
fn test_tg_classify_text_flag_stdout_matches_python_module() {
    // #92: --text classifies a literal string through the SAME sidecar payload["content"]
    // path a file classify already used -- verify both front doors agree byte-for-byte.
    if !repo_python_has_module("typer") {
        return;
    }

    let literal = "2026-05-26 ERROR payment retry failed";

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("classify")
        .arg("--text")
        .arg(literal);
    configure_classify_env(&mut tg);
    let tg_output = run_with_timeout(tg, Duration::from_secs(40));

    let mut py = Command::new(repo_python());
    py.current_dir(repo_root())
        .arg("-m")
        .arg("tensor_grep")
        .arg("classify")
        .arg("--text")
        .arg(literal);
    configure_classify_env(&mut py);
    let py_output = run_with_timeout(py, Duration::from_secs(40));

    assert_eq!(tg_output.status.code(), py_output.status.code());
    assert_eq!(tg_output.stdout, py_output.stdout);
    assert_eq!(tg_output.stderr, py_output.stderr);

    assert!(
        tg_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        tg_output.status.code(),
        String::from_utf8_lossy(&tg_output.stdout),
        String::from_utf8_lossy(&tg_output.stderr)
    );
    let payload: serde_json::Value = serde_json::from_slice(&tg_output.stdout).unwrap();
    let classifications = payload["classifications"].as_array().unwrap();
    assert_eq!(classifications.len(), 1);
    assert_eq!(classifications[0]["label"], "error");
    assert!(classifications[0]["file"].is_null());
}

#[test]
fn test_tg_classify_stdin_flag_stdout_matches_python_module() {
    // #92: --stdin reads to EOF and classifies via the same content payload; compare against
    // `python -m tensor_grep classify --stdin` fed identical bytes.
    if !repo_python_has_module("typer") {
        return;
    }

    let content = b"INFO ok\nERROR database failed\nWARN retrying\n".to_vec();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root()).arg("classify").arg("--stdin");
    configure_classify_env(&mut tg);
    let tg_output = run_with_stdin_timeout(tg, content.clone(), Duration::from_secs(40));

    let mut py = Command::new(repo_python());
    py.current_dir(repo_root())
        .arg("-m")
        .arg("tensor_grep")
        .arg("classify")
        .arg("--stdin");
    configure_classify_env(&mut py);
    let py_output = run_with_stdin_timeout(py, content, Duration::from_secs(40));

    assert_eq!(tg_output.status.code(), py_output.status.code());
    assert_eq!(tg_output.stdout, py_output.stdout);
    assert_eq!(tg_output.stderr, py_output.stderr);

    assert!(
        tg_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        tg_output.status.code(),
        String::from_utf8_lossy(&tg_output.stdout),
        String::from_utf8_lossy(&tg_output.stderr)
    );
    let payload: serde_json::Value = serde_json::from_slice(&tg_output.stdout).unwrap();
    let classifications = payload["classifications"].as_array().unwrap();
    assert_eq!(classifications.len(), 3);
    assert_eq!(classifications[1]["label"], "error");
}

#[test]
fn test_tg_classify_stdin_empty_input_degrades_cleanly_without_hanging() {
    // TRAP coverage: a closed/empty stdin pipe must exit cleanly (bounded by
    // run_with_stdin_timeout's own hard timeout), not hang and not crash.
    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root()).arg("classify").arg("--stdin");
    configure_classify_env(&mut tg);
    let output = run_with_stdin_timeout(tg, Vec::new(), sidecar_test_timeout());

    assert!(
        !output.status.success(),
        "empty stdin should exit non-zero rather than fabricate an empty success"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("no content to classify"),
        "expected a clean diagnostic, got: {stderr}"
    );
}

#[test]
fn test_sidecar_protocol_handles_large_payload_and_large_stdout() {
    let payload = json!({
        "content": large_classify_payload(2 * 1024 * 1024),
    });
    let request = json!({
        "command": "classify",
        "args": ["--max-lines", "0"],
        "payload": payload,
    });

    let mut sidecar = Command::new(repo_python());
    sidecar
        .current_dir(repo_root())
        .arg("-m")
        .arg("tensor_grep.sidecar");
    configure_classify_env(&mut sidecar);
    let output = run_with_stdin_timeout(
        sidecar,
        serde_json::to_vec(&request).unwrap(),
        Duration::from_secs(40),
    );

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let response: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(response["exit_code"].as_i64(), Some(0));
    assert!(response["stdout"].as_str().unwrap().len() > 1_000_000);
    assert_ne!(response["pid"].as_u64().unwrap() as u32, std::process::id());
}

#[test]
fn test_sidecar_crash_reports_error_without_hanging() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());
    let crash_script = dir.path().join("mock_sidecar_crash.py");
    fs::write(
        &crash_script,
        "import sys\n".to_string()
            + "sys.stdin.buffer.read()\n"
            + "sys.stdout.write('{\\\"stdout\\\":')\n"
            + "sys.stdout.flush()\n"
            + "raise SystemExit(1)\n",
    )
    .unwrap();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("classify")
        .arg(&file_path)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &crash_script);
    configure_classify_env(&mut tg);
    let output = run_with_timeout(tg, sidecar_test_timeout());

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("sidecar exited") || stderr.contains("invalid JSON"));
}

#[test]
fn test_gpu_search_json_output_is_augmented_with_unified_envelope() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    let file_path = write_sample_log(&corpus_dir);
    let mock_script = dir.path().join("mock_gpu_sidecar.py");
    fs::write(
        &mock_script,
        format!(
            "import json\nimport os\nimport sys\nrequest = json.loads(sys.stdin.buffer.read())\nassert request[\"payload\"].get(\"gpu_device_ids\") == [0]\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"requested_gpu_device_ids\": [9], \"routing_gpu_device_ids\": [], \"matches\": [{{\"file\": {:?}, \"line_number\": 2, \"text\": \"ERROR database failed\"}}]}}) + \'\\n\', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
            file_path.display().to_string()
        ),
    )
    .unwrap();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ERROR")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &mock_script);

    let output = run_with_timeout(tg, sidecar_test_timeout());

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["version"], 1);
    if cfg!(feature = "cuda") {
        assert_eq!(payload["routing_backend"], "NativeGpuBackend");
        assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit-native");
        assert_eq!(payload["sidecar_used"], false);
        assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
        assert_eq!(payload["routing_gpu_device_ids"], serde_json::json!([0]));
    } else {
        assert_eq!(payload["routing_backend"], "GpuSidecar");
        assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit");
        assert_eq!(payload["sidecar_used"], true);
        assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
    }
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_gpu_search_smart_case_lowercase_uses_sidecar_and_preserves_flag() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    let file_path = write_sample_log(&corpus_dir);
    let mock_script = dir.path().join("mock_gpu_sidecar_smart_case.py");
    fs::write(
        &mock_script,
        format!(
            "import json\nimport os\nimport sys\nrequest = json.loads(sys.stdin.buffer.read())\npayload = request[\"payload\"]\nassert payload.get(\"gpu_device_ids\") == [0]\nassert payload.get(\"smart_case\") is True\nassert payload.get(\"hidden\") is True\nassert payload.get(\"max_depth\") == 1\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"requested_gpu_device_ids\": [0], \"routing_gpu_device_ids\": [], \"matches\": [{{\"file\": {:?}, \"line_number\": 2, \"text\": \"ERROR database failed\"}}]}}) + \'\\n\', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
            file_path.display().to_string()
        ),
    )
    .unwrap();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("--smart-case")
        .arg("--hidden")
        .arg("--max-depth")
        .arg("1")
        .arg("warning")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &mock_script);

    let output = run_with_timeout(tg, sidecar_test_timeout());

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "GpuSidecar");
    assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit");
    assert_eq!(payload["sidecar_used"], true);
    assert_eq!(payload["requested_gpu_device_ids"], serde_json::json!([0]));
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_gpu_search_invalid_device_id_reports_clear_error_without_traceback() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_sample_log(&corpus_dir);

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("99")
        .arg("ERROR")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python());
    configure_repo_python_env(&mut tg);
    if !cfg!(feature = "cuda") {
        tg.arg("--json");
    }

    let output = run_with_timeout(tg, sidecar_test_timeout());

    if cfg!(feature = "cuda") {
        assert!(!output.status.success());
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains("99"), "stderr={stderr}");
        assert!(stderr.contains("invalid CUDA device id"), "stderr={stderr}");
    } else {
        assert!(
            output.status.success(),
            "status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains("native GPU unavailable"), "stderr={stderr}");
        let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(payload["routing_backend"], "NativeCpuBackend");
        assert_eq!(payload["gpu_evidence_status"], "unsupported");
        assert_eq!(payload["gpu_proof"], false);
        assert_eq!(payload["native_gpu_unavailable"], true);
        assert_eq!(payload["requested_gpu_device_ids"], json!([99]));
    }
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains("Traceback"), "stderr={stderr}");
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_gpu_search_cuda_visible_devices_empty_reports_clear_error_without_traceback() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_sample_log(&corpus_dir);

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("ERROR")
        .arg(&corpus_dir)
        .arg("--json")
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("CUDA_VISIBLE_DEVICES", "");
    configure_repo_python_env(&mut tg);

    let output = run_with_timeout(tg, sidecar_test_timeout());

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("native GPU unavailable"), "stderr={stderr}");
    assert!(!stderr.contains("Traceback"), "stderr={stderr}");
    let payload: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "NativeCpuBackend");
    assert_eq!(payload["gpu_evidence_status"], "unsupported");
    assert_eq!(payload["gpu_proof"], false);
    assert_eq!(payload["native_gpu_unavailable"], true);
    assert_eq!(payload["requested_gpu_device_ids"], json!([0]));
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_sidecar_timeout_kills_child_and_reports_error() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_sample_log(&corpus_dir);
    let pid_file = dir.path().join("sleeping_sidecar.pid");
    let sleep_script = dir.path().join("mock_sidecar_sleep.py");
    fs::write(
        &sleep_script,
        format!(
            "import pathlib\nimport sys\nimport time\nimport os\npathlib.Path({:?}).write_text(str(os.getpid()), encoding='utf-8')\nsys.stdin.buffer.read()\ntime.sleep(10)\n",
            pid_file.display().to_string()
        ),
    )
    .unwrap();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("ERROR")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &sleep_script)
        .env("TG_SIDECAR_TIMEOUT_MS", "300");

    // #167: widened 5s -> 30s. This bound gates the TEST's own wait-for-result window (see
    // run_with_timeout), not the product's kill deadline (TG_SIDECAR_TIMEOUT_MS=300 above, which
    // is unchanged) -- a loaded Windows GitHub runner can push tg-spawn + sidecar-spawn + kill +
    // reap well past 5s even though the actual timeout fires at 300ms. The wedged sidecar sleeps
    // only 10s if the kill genuinely never fires, so 30s comfortably preserves the regression
    // signal (a broken kill still shows up via the stderr "timed out"/"terminated" assertions
    // below, well inside this ceiling) while tolerating CI contention.
    let output = run_with_timeout(tg, Duration::from_secs(30));

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("timed out"), "stderr={stderr}");
    assert!(stderr.contains("terminated"), "stderr={stderr}");

    if !cfg!(windows) {
        let pid = wait_for_pid_file(&pid_file, sidecar_test_timeout());
        assert!(
            wait_for_process_exit(pid, Duration::from_secs(2)),
            "expected sidecar pid {pid} to be terminated"
        );
    }
}

/// Writes a fake "python" that ignores all argv and sleeps well past any timeout under test,
/// simulating a wedged/hung Python interpreter for the `--help` probe timeout tests below. Sleeps
/// ~20s (deliberately far beyond any timeout asserted here, including under parallel-test
/// subprocess-spawn contention) so a broken kill/timeout mechanism reads as an unambiguous, far-off
/// natural completion rather than something that could be confused with contention-inflated timing.
fn write_wedged_python_script(dir: &Path) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join("wedged_python.cmd");
        fs::write(&script, "@echo off\r\nping -n 21 127.0.0.1 >nul\r\n").unwrap();
        script
    } else {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let script = dir.join("wedged_python.sh");
            fs::write(&script, "#!/bin/sh\nsleep 20\n").unwrap();
            let mut perms = fs::metadata(&script).unwrap().permissions();
            perms.set_mode(perms.mode() | 0o111);
            fs::set_permissions(&script, perms).unwrap();
            script
        }
        #[cfg(not(unix))]
        unreachable!("non-windows, non-unix target")
    }
}

#[cfg(not(feature = "cuda"))]
// #145: `test_help_probe_timeout_env_override_falls_back_fast_with_wedged_python` (below) and
// `test_help_probe_default_timeout_recovers_with_enriched_fallback_when_python_is_wedged` (further
// below) both drive `tg --help` against the SAME wedged-Python mechanism and measure wall-clock --
// the single most directly identifiable source of mutual contention for either test's timing
// assertion if `cargo test`'s default thread-parallelism happens to schedule them at the same time.
// This lock makes that specific pair mutually exclusive. It cannot (from a single test file) also
// exclude the ~14 OTHER subprocess-spawning tests in this binary or unrelated CI-runner load; see
// `HELP_PROBE_TRIALS` and the widened gap in the override-honored test below for how the timing
// assertion itself stays robust to that residual noise.
static HELP_PROBE_TIMING_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(not(feature = "cuda"))]
fn lock_help_probe_timing() -> std::sync::MutexGuard<'static, ()> {
    // Recover from poisoning instead of propagating it: if an EARLIER test panicked on its own
    // assertion while holding this lock, unwrapping a PoisonError here would replace that test's
    // real failure message with an unrelated "lock poisoned" panic in this one. The lock only
    // provides mutual exclusion for timing purposes, not shared data integrity, so recovering is safe.
    HELP_PROBE_TIMING_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(not(feature = "cuda"))]
const HELP_PROBE_SHORT_OVERRIDE_MS: &str = "250";
#[cfg(not(feature = "cuda"))]
const HELP_PROBE_LONG_OVERRIDE_MS: &str = "6000";
#[cfg(not(feature = "cuda"))]
const HELP_PROBE_MIN_GAP: Duration = Duration::from_millis(2500);
#[cfg(not(feature = "cuda"))]
// Each variant is measured this many times and the FASTEST run is kept -- see the doc comment on
// `min_wedged_help_probe` for why min-of-N is the right reducer for a noise source that only ever
// adds latency.
const HELP_PROBE_TRIALS: u32 = 2;

#[cfg(not(feature = "cuda"))]
fn run_wedged_help_probe(wedge_script: &Path, probe_timeout_ms: &str) -> Duration {
    // Run `tg --help` against a wedged (never-responding) Python with the given help-probe timeout
    // override; assert the native fallback still succeeds with enriched help; return wall-clock
    // elapsed. The override-honored test COMPARES a short vs long override via this helper: both runs
    // pay identical spawn + fallback overhead, so their DIFFERENCE is robust to CI subprocess-spawn
    // contention (#136), unlike an absolute wall-clock bound.
    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("--help")
        .env("TG_SIDECAR_PYTHON", wedge_script)
        .env("TG_HELP_PROBE_TIMEOUT_MS", probe_timeout_ms);

    let started = Instant::now();
    // Generous outer hang-guard: guards only against a truly-hung command, NOT the timing assertion
    // (the short-vs-long comparison lives in the caller). #145: raised 30s->60s to keep a comparable
    // safety margin above the widened long-side probe override (3000ms -> 6000ms); the wedged Python
    // itself only ever sleeps ~20s (write_wedged_python_script), so in practice this ceiling is not
    // expected to fire even under heavy contention -- it exists purely so a genuinely broken
    // kill-on-timeout regression fails as a bounded panic instead of hanging the runner.
    let output = run_with_timeout(tg, Duration::from_secs(60));
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "expected the native fallback to still exit 0 (probe={probe_timeout_ms}ms); stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("AI agent moat commands"),
        "expected the enriched native fallback help (probe={probe_timeout_ms}ms); stdout={stdout}"
    );
    elapsed
}

#[cfg(not(feature = "cuda"))]
// #145: race `probe_timeout_ms` `trials` times against the wedged Python and keep the FASTEST
// (minimum) elapsed. CI subprocess-spawn/scheduler contention only ever ADDS latency -- it never
// makes a run finish faster than its true uncontended cost -- so the minimum across independent
// trials is a statistically principled lower-bound estimate, and a single unlucky trial (one
// thread-scheduling hiccup, one antivirus scan of a freshly-written EXE) no longer decides the
// result the way a single-shot measurement does. This does NOT weaken the regression check: if
// TG_HELP_PROBE_TIMEOUT_MS is genuinely ignored, EVERY trial of EVERY variant converges on the same
// ~DEFAULT_HELP_PROBE_TIMEOUT_MS wait regardless of which is minimized, so the short-vs-long gap
// still collapses to ~0 and the caller's assertion still fails.
fn min_wedged_help_probe(wedge_script: &Path, probe_timeout_ms: &str, trials: u32) -> Duration {
    (0..trials)
        .map(|_| run_wedged_help_probe(wedge_script, probe_timeout_ms))
        .min()
        .expect("trials must be >= 1")
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_help_probe_timeout_env_override_falls_back_fast_with_wedged_python() {
    // audit #97 item 1: TG_HELP_PROBE_TIMEOUT_MS must override the (3000ms) default help-probe
    // timeout, mirroring how TG_SIDECAR_TIMEOUT_MS overrides resolve_sidecar_timeout(). A short
    // override must make the native --help fallback trigger fast against a Python that never responds.
    //
    // #136: this asserted an ABSOLUTE `elapsed < 3s`, which false-failed under extreme GitHub-runner
    // starvation (spawn overhead alone exceeded the bound, blocking the v1.63.4 release). Fix: run the
    // wedged probe TWICE -- a 250ms override vs a longer one -- on the same host and assert the short
    // run finishes markedly sooner, since shared contention cancels in the DIFFERENCE rather than an
    // absolute wall-clock. That held for a while but STILL false-failed a 3rd time on the
    // windows-latest/nightly leg and blocked v1.65.2 (#145) -- a single-shot pair remains vulnerable
    // to a contention spike landing on just the short run (which shrinks the gap), or to racing the
    // sibling test below, which drives the exact same `tg --help`-against-wedged-Python code path.
    //
    // #145 hardening -- three independent, complementary changes, not another tolerance bump alone:
    //   1. `HELP_PROBE_TIMING_LOCK` mutually excludes this test and its sibling below so they never
    //      compete with EACH OTHER for process-creation resources (the single most directly
    //      identifiable shared-mechanism contention source).
    //   2. Each variant is measured `HELP_PROBE_TRIALS` times and the MINIMUM is kept (see
    //      `min_wedged_help_probe`) so a single unlucky trial can no longer decide the result.
    //   3. The long-side override is raised 3000ms -> 6000ms, doubling the nominal gap from ~2750ms
    //      to ~5750ms, while the required minimum gap rises only 1500ms -> 2500ms -- i.e. the
    //      required gap now needs to retain ~43% of the theoretical maximum instead of ~55%,
    //      materially MORE tolerant of contention eroding the observed gap, while staying far above
    //      the ~0ms gap a genuinely-ignored override would produce (both variants would then wait the
    //      same real ~3000ms default, regardless of trials or minimum-taking).
    let dir = tempdir().unwrap();
    let wedge_script = write_wedged_python_script(dir.path());
    let _guard = lock_help_probe_timing();

    let elapsed_short = min_wedged_help_probe(
        &wedge_script,
        HELP_PROBE_SHORT_OVERRIDE_MS,
        HELP_PROBE_TRIALS,
    );
    let elapsed_long = min_wedged_help_probe(
        &wedge_script,
        HELP_PROBE_LONG_OVERRIDE_MS,
        HELP_PROBE_TRIALS,
    );

    assert!(
        elapsed_short + HELP_PROBE_MIN_GAP < elapsed_long,
        "TG_HELP_PROBE_TIMEOUT_MS override was not honored -- the best of {HELP_PROBE_TRIALS} \
         run(s) at {HELP_PROBE_SHORT_OVERRIDE_MS}ms took {elapsed_short:?}, but the best of \
         {HELP_PROBE_TRIALS} run(s) at {HELP_PROBE_LONG_OVERRIDE_MS}ms took only {elapsed_long:?}; \
         honoring the short override should save several seconds of probe-wait (required >= \
         {HELP_PROBE_MIN_GAP:?} of that gap to survive timing jitter)"
    );
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_help_probe_default_timeout_recovers_with_enriched_fallback_when_python_is_wedged() {
    // audit #97 item 1: the hardcoded default was 750ms (too tight -- a cold Python start can
    // exceed it, which is the root cause of bare `tg --help` sometimes rendering the sparse
    // fallback instead of the rich Typer help). This proves the *default* (no override at all)
    // still recovers cleanly -- exits 0 with the enriched fallback content -- and does not block
    // for an unreasonable time when Python is wedged. The exact millisecond value of the default
    // (raised to 3000ms; see DEFAULT_HELP_PROBE_TIMEOUT_MS in python_sidecar.rs and the
    // measured-latency comment beside it) is deliberately NOT asserted here via a tight wall-clock
    // lower bound: a sibling attempt at that showed parallel-test subprocess-spawn contention can
    // inflate elapsed time by ~1.5-2x, which would make a tight bound flaky. That the override is
    // honored is covered precisely, without timing ambiguity, by the sibling test above.
    //
    // #145: shares `HELP_PROBE_TIMING_LOCK` with the override-honored test above so the two
    // `tg --help`-against-wedged-Python timing tests never race each other for process-creation
    // resources; see the lock's doc comment for why.
    let dir = tempdir().unwrap();
    let wedge_script = write_wedged_python_script(dir.path());
    let _guard = lock_help_probe_timing();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("--help")
        .env("TG_SIDECAR_PYTHON", &wedge_script)
        .env_remove("TG_HELP_PROBE_TIMEOUT_MS");

    let started = Instant::now();
    let output = run_with_timeout(tg, Duration::from_secs(15));
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "expected the native fallback to still exit 0; stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        elapsed < Duration::from_secs(10),
        "the default help-probe timeout should not block --help this long (wedged Python sleeps \
         ~20s, so this would indicate the kill/timeout mechanism did not engage); elapsed={elapsed:?}"
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("AI agent moat commands"),
        "expected the enriched native fallback help; stdout={stdout}"
    );
}

/// A stand-in for `python` that never responds to the general one-shot Python passthrough
/// dispatch (`execute_python_passthrough_command_inner`, audit H5). Unlike the JSON sidecar
/// path, passthrough has no `TG_SIDECAR_SCRIPT` override -- the only way to control what
/// "python" does for this code path is to swap out the interpreter itself via
/// `TG_SIDECAR_PYTHON`, mirroring `write_wedged_python_script` above. On non-Windows it also
/// records its own pid (via the `WEDGE_PID_FILE` env var the caller sets) so a test can assert
/// the wedged child was actually reaped, not merely that tg's wait loop gave up on it.
/// `sleep_seconds` is a caller-chosen bound: long (~20s) for the kill-at-deadline test where
/// the process must never be allowed to finish naturally, short (~3s) for the daemon-exemption
/// test where the test itself owns cleanup and a long-lived orphan process would be wasteful.
fn write_wedged_passthrough_python_script(dir: &Path, name: &str, sleep_seconds: u32) -> PathBuf {
    if cfg!(windows) {
        let script = dir.join(format!("{name}.cmd"));
        // `ping -n N` performs N probes with a ~1s gap, so it sleeps ~(N-1) seconds.
        fs::write(
            &script,
            format!(
                "@echo off\r\nping -n {} 127.0.0.1 >nul\r\n",
                sleep_seconds + 1
            ),
        )
        .unwrap();
        script
    } else {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let script = dir.join(format!("{name}.sh"));
            fs::write(
                &script,
                format!("#!/bin/sh\necho $$ > \"$WEDGE_PID_FILE\"\nsleep {sleep_seconds}\n"),
            )
            .unwrap();
            let mut perms = fs::metadata(&script).unwrap().permissions();
            perms.set_mode(perms.mode() | 0o111);
            fs::set_permissions(&script, perms).unwrap();
            script
        }
        #[cfg(not(unix))]
        unreachable!("non-windows, non-unix target")
    }
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_passthrough_timeout_kills_wedged_child_and_reports_error() {
    // audit H5 RED->GREEN (a): a one-shot passthrough command (`doctor` -- not in the daemon
    // exemption list) whose child never exits must be KILLED at the configured deadline and
    // report a clear timeout error, instead of hanging the whole `tg` invocation forever (the
    // pre-fix bug: execute_python_passthrough_command_inner called a raw, unbounded
    // `child.wait()`). Pre-fix, this test fails by exhausting run_with_timeout's own budget
    // (#167: widened 8s -> 30s for CI-load headroom; see the timing comments below) -- a bounded
    // RED failure, per the anti-hang-test-protocol -- it never hangs the suite.
    let dir = tempdir().unwrap();
    let pid_file = dir.path().join("wedged_passthrough.pid");
    let wedge_script =
        write_wedged_passthrough_python_script(dir.path(), "wedged_passthrough_python", 20);

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("doctor")
        .env("TG_SIDECAR_PYTHON", &wedge_script)
        .env("WEDGE_PID_FILE", &pid_file)
        .env("TG_PASSTHROUGH_TIMEOUT_MS", "300");

    let started = Instant::now();
    // #167: outer wait-for-result cap widened 8s -> 30s so a loaded runner doesn't trip
    // run_with_timeout's own hang-guard before the process even gets a chance to finish; this
    // stays well above the elapsed bound asserted below and the wedge's ~20s natural sleep, so a
    // genuinely wedged child (kill never fires) still completes inside it and fails on content,
    // not on this cap.
    let output = run_with_timeout(tg, Duration::from_secs(30));
    let elapsed = started.elapsed();

    assert!(
        !output.status.success(),
        "expected the wedged passthrough child to fail closed, not hang forever; stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("timed out"), "stderr={stderr}");
    assert!(stderr.contains("terminated"), "stderr={stderr}");
    assert!(
        // #167: widened 5s -> 15s. This is the TEST's own tolerance window around the unrelated,
        // unchanged 300ms product deadline (TG_PASSTHROUGH_TIMEOUT_MS above) -- CI observed a
        // legitimate-kill run take elapsed=6.44s under runner contention alone. 15s keeps a
        // comfortable margin below the wedge's ~20s natural completion (so a REAL "deadline never
        // fired" regression still lands well outside this bound and fails here, in addition to
        // the stderr content assertions above) while tolerating much heavier contention than
        // observed.
        elapsed < Duration::from_secs(15),
        "TG_PASSTHROUGH_TIMEOUT_MS=300 was not honored -- the wedged child sleeps ~20s, so this \
         elapsed={elapsed:?} is consistent with the deadline never firing (the pre-fix bare \
         child.wait() bug)"
    );

    if !cfg!(windows) {
        let pid = wait_for_pid_file(&pid_file, sidecar_test_timeout());
        assert!(
            wait_for_process_exit(pid, Duration::from_secs(2)),
            "expected the wedged passthrough child pid {pid} to be terminated, not left running"
        );
    }
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_passthrough_fast_command_completes_unaffected_by_new_timeout() {
    // audit H5 (b): a legitimate, fast, one-shot passthrough command must complete exactly as
    // before -- the new bounded wait-or-kill machinery must not change behavior, output, or
    // exit code for the common (non-wedged) case. `audit` with no args is a handful of
    // typer.echo calls (main.py:11215-11223) with no repo scanning, so it is fast regardless of
    // machine load.
    if !repo_python_has_module("typer") {
        return;
    }

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("audit")
        .env("TG_SIDECAR_PYTHON", repo_python());
    configure_repo_python_env(&mut tg);

    let started = Instant::now();
    let output = run_with_timeout(tg, Duration::from_secs(40));
    let elapsed = started.elapsed();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("Audit commands:"), "stdout={stdout}");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains("timed out"), "stderr={stderr}");
    assert!(
        elapsed < Duration::from_secs(20),
        "a trivial, side-effect-free passthrough command should complete quickly; elapsed={elapsed:?}"
    );
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_passthrough_exempts_mcp_server_launch_from_timeout() {
    // audit H5 RED->GREEN (c) -- the load-bearing daemon-exemption case: `tg mcp` starts the
    // MCP server and must NEVER be killed on a timer, even with an aggressively short
    // TG_PASSTHROUGH_TIMEOUT_MS configured. Uses a SHORT wedge (~3s, not the ~20s used by the
    // kill test above) purely so this test's own cleanup is fast and deterministic; the
    // assertion below only needs the process to still be alive shortly after the 300ms deadline
    // would have fired if the exemption were broken.
    let dir = tempdir().unwrap();
    let wedge_script = write_wedged_passthrough_python_script(dir.path(), "wedged_mcp_python", 3);

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("mcp")
        .env("TG_SIDECAR_PYTHON", &wedge_script)
        .env("TG_PASSTHROUGH_TIMEOUT_MS", "300")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let mut child = tg.spawn().expect("failed to spawn `tg mcp`");

    // Sleep comfortably past the configured 300ms deadline. If the daemon exemption were NOT
    // honored, execute_python_passthrough_command_inner would already have killed the wedged
    // child at ~300ms via wait_for_passthrough_or_kill, and `tg mcp` itself would already have
    // exited with a timeout error (handle_python_passthrough calls exit_with_sidecar_error on
    // any Err) well before this point.
    thread::sleep(Duration::from_millis(1500));
    assert!(
        matches!(child.try_wait(), Ok(None)),
        "expected `tg mcp` to still be running 1.5s past its 300ms TG_PASSTHROUGH_TIMEOUT_MS \
         deadline -- the mcp server-launch exemption did not hold and it was killed by the timer"
    );

    // Bounded, deterministic cleanup: kill + reap rather than waiting out the wedge's sleep.
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_gpu_search_schema_invalid_json_reports_clear_error() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    write_sample_log(&corpus_dir);
    let mock_script = dir.path().join("mock_gpu_sidecar_schema_invalid.py");
    fs::write(
        &mock_script,
        "import json\nimport os\nimport sys\nsys.stdin.buffer.read()\nresponse = {\"stdout\": json.dumps({\"total_matches\": 1, \"total_files\": 1, \"matches\": \"not-a-list\"}) + '\\n', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}\nsys.stdout.write(json.dumps(response))\n",
    )
    .unwrap();

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("--gpu-device-ids")
        .arg("0")
        .arg("--json")
        .arg("ERROR")
        .arg(&corpus_dir)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("TG_SIDECAR_SCRIPT", &mock_script);

    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("malformed"), "stderr={stderr}");
    assert!(stderr.contains("matches"), "stderr={stderr}");
    assert!(!stderr.contains("panic"), "stderr={stderr}");
}

#[cfg(not(feature = "cuda"))]
#[test]
fn test_gpu_search_recovers_after_previous_malformed_sidecar_payload() {
    let dir = tempdir().unwrap();
    let corpus_dir = dir.path().join("corpus");
    fs::create_dir(&corpus_dir).unwrap();
    let file_path = write_sample_log(&corpus_dir);

    let bad_script = dir.path().join("mock_gpu_sidecar_bad.py");
    fs::write(
        &bad_script,
        "import json\nimport os\nimport sys\nsys.stdin.buffer.read()\nresponse = {\"stdout\": json.dumps({\"total_matches\": 1, \"total_files\": 1, \"matches\": \"not-a-list\"}) + '\\n', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}\nsys.stdout.write(json.dumps(response))\n",
    )
    .unwrap();

    let good_script = dir.path().join("mock_gpu_sidecar_good.py");
    fs::write(
        &good_script,
        format!(
            "import json\nimport os\nimport sys\nsys.stdin.buffer.read()\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"matches\": [{{\"file\": {:?}, \"line_number\": 2, \"text\": \"ERROR database failed\"}}]}}) + '\\n', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
            file_path.display().to_string()
        ),
    )
    .unwrap();

    let first_output = run_with_timeout(
        {
            let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
            tg.current_dir(repo_root())
                .arg("search")
                .arg("--gpu-device-ids")
                .arg("0")
                .arg("--json")
                .arg("ERROR")
                .arg(&corpus_dir)
                .env("TG_SIDECAR_PYTHON", repo_python())
                .env("TG_SIDECAR_SCRIPT", &bad_script);
            tg
        },
        sidecar_test_timeout(),
    );

    assert!(!first_output.status.success());
    let first_stderr = String::from_utf8_lossy(&first_output.stderr);
    assert!(first_stderr.contains("malformed"), "stderr={first_stderr}");

    let second_output = run_with_timeout(
        {
            let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
            tg.current_dir(repo_root())
                .arg("search")
                .arg("--gpu-device-ids")
                .arg("0")
                .arg("--json")
                .arg("ERROR")
                .arg(&corpus_dir)
                .env("TG_SIDECAR_PYTHON", repo_python())
                .env("TG_SIDECAR_SCRIPT", &good_script);
            tg
        },
        sidecar_test_timeout(),
    );

    assert!(
        second_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        second_output.status.code(),
        String::from_utf8_lossy(&second_output.stdout),
        String::from_utf8_lossy(&second_output.stderr)
    );
    let payload: serde_json::Value = serde_json::from_slice(&second_output.stdout).unwrap();
    assert_eq!(payload["routing_backend"], "GpuSidecar");
    assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit");
    assert_eq!(payload["sidecar_used"], true);
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_missing_python_reports_actionable_error() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());
    let isolated_tg = isolated_tg_binary(dir.path());

    let mut tg = Command::new(&isolated_tg);
    tg.current_dir(dir.path())
        .arg("classify")
        .arg(&file_path)
        .env("PATH", "");
    configure_classify_env(&mut tg);
    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert_eq!(output.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("Python sidecar not found"));
}
