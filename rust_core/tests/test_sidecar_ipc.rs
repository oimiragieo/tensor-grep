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

    let unix_candidate = repo_root().join(".venv").join("bin").join("python");
    if unix_candidate.exists() {
        return unix_candidate;
    }

    PathBuf::from("python")
}

fn configure_repo_python_env(command: &mut Command) {
    command.env("PYTHONPATH", repo_root().join("src"));
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
fn test_sidecar_protocol_handles_large_payload_and_large_stdout() {
    let payload = json!({
        "content": large_classify_payload(2 * 1024 * 1024),
    });
    let request = json!({
        "command": "classify",
        "args": [],
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
    let output = run_with_timeout(tg, Duration::from_secs(5));

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
            "import json\nimport os\nimport sys\nrequest = json.loads(sys.stdin.buffer.read())\nresponse = {{\"stdout\": json.dumps({{\"total_matches\": 1, \"total_files\": 1, \"matches\": [{{\"file\": {:?}, \"line_number\": 2, \"text\": \"ERROR database failed\"}}]}}) + \'\\n\', \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}}\nsys.stdout.write(json.dumps(response))\n",
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

    let output = run_with_timeout(tg, Duration::from_secs(5));

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
        assert_eq!(payload["routing_gpu_device_ids"], serde_json::json!([0]));
    } else {
        assert_eq!(payload["routing_backend"], "GpuSidecar");
        assert_eq!(payload["routing_reason"], "gpu-device-ids-explicit");
        assert_eq!(payload["sidecar_used"], true);
    }
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

    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("99"), "stderr={stderr}");
    if cfg!(feature = "cuda") {
        assert!(stderr.contains("invalid CUDA device id"), "stderr={stderr}");
    } else {
        assert!(
            stderr.contains("Requested GPU device ID"),
            "stderr={stderr}"
        );
        assert!(stderr.contains("Available device IDs"), "stderr={stderr}");
    }
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
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("CUDA_VISIBLE_DEVICES", "");

    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("CUDA_VISIBLE_DEVICES"), "stderr={stderr}");
    assert!(stderr.contains("GPU device IDs [0]"), "stderr={stderr}");
    assert!(!stderr.contains("Traceback"), "stderr={stderr}");
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

    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("timed out"), "stderr={stderr}");
    assert!(stderr.contains("terminated"), "stderr={stderr}");

    let pid: u32 = fs::read_to_string(&pid_file)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(
        wait_for_process_exit(pid, Duration::from_secs(2)),
        "expected sidecar pid {pid} to be terminated"
    );
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
        Duration::from_secs(5),
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
        Duration::from_secs(5),
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
