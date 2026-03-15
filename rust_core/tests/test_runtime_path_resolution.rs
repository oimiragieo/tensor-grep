use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

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

    repo_root().join(".venv").join("bin").join("python")
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

fn write_sample_log(dir: &Path) -> PathBuf {
    let file_path = dir.join("sample.log");
    fs::write(&file_path, "INFO ok\nERROR database failed\n").unwrap();
    file_path
}

fn configure_classify_env(command: &mut Command) {
    command
        .env("TENSOR_GREP_TRITON_TIMEOUT_SECONDS", "0.01")
        .env("HF_HUB_OFFLINE", "1")
        .env("TRANSFORMERS_OFFLINE", "1");
}

fn write_sidecar_probe_script(dir: &Path) -> PathBuf {
    let script = if cfg!(windows) {
        dir.join("sidecar_probe.py")
    } else {
        dir.join("sidecar_probe")
    };

    fs::write(
        &script,
        "import json\n"
            .to_string()
            + "import os\n"
            + "import sys\n"
            + "sys.stdin.buffer.read()\n"
            + "sys.stdout.write(json.dumps({\"stdout\": \"\", \"stderr\": \"\", \"exit_code\": 0, \"pid\": os.getpid()}))\n",
    )
    .unwrap();

    script
}

fn write_wrapper_script(dir: &Path, file_name: &str, body: &str) -> PathBuf {
    let script = dir.join(file_name);
    fs::write(&script, body).unwrap();

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let mut permissions = fs::metadata(&script).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&script, permissions).unwrap();
    }

    script
}

fn python_wrapper_script(dir: &Path, marker: &Path) -> PathBuf {
    let python = repo_python();

    if cfg!(windows) {
        write_wrapper_script(
            dir,
            "python-wrapper.cmd",
            &format!(
                "@echo off\r\necho python-override>>\"{}\"\r\n\"{}\" %*\r\n",
                marker.display(),
                python.display()
            ),
        )
    } else {
        write_wrapper_script(
            dir,
            "python-wrapper.sh",
            &format!(
                "#!/bin/sh\nprintf 'python-override\\n' >> '{}'\nexec '{}' \"$@\"\n",
                marker.display(),
                python.display()
            ),
        )
    }
}

fn rg_wrapper_script(dir: &Path, marker: &Path) -> PathBuf {
    if cfg!(windows) {
        write_wrapper_script(
            dir,
            "rg-wrapper.cmd",
            &format!(
                "@echo off\r\necho rg-override>>\"{}\"\r\necho TG_RG_OVERRIDE_SENTINEL\r\n",
                marker.display()
            ),
        )
    } else {
        write_wrapper_script(
            dir,
            "rg-wrapper.sh",
            &format!(
                "#!/bin/sh\nprintf 'rg-override\\n' >> '{}'\nprintf 'TG_RG_OVERRIDE_SENTINEL\\n'\n",
                marker.display()
            ),
        )
    }
}

#[test]
fn test_help_documents_runtime_override_env_vars() {
    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root()).arg("--help");
    let output = run_with_timeout(tg, Duration::from_secs(5));

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("TG_SIDECAR_PYTHON"), "stdout={stdout}");
    assert!(stdout.contains("TG_RG_PATH"), "stdout={stdout}");
}

#[test]
fn test_tg_classify_uses_tg_sidecar_python_override() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());
    let marker = dir.path().join("python-marker.txt");
    let python_wrapper = python_wrapper_script(dir.path(), &marker);
    let sidecar_script = write_sidecar_probe_script(dir.path());

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("classify")
        .arg(&file_path)
        .env("TG_SIDECAR_PYTHON", &python_wrapper)
        .env("TG_SIDECAR_SCRIPT", &sidecar_script);

    let output = run_with_timeout(tg, Duration::from_secs(20));

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        marker.exists(),
        "expected override wrapper marker at {}",
        marker.display()
    );
}

#[test]
fn test_tg_search_uses_tg_rg_path_override() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());
    let marker = dir.path().join("rg-marker.txt");
    let rg_wrapper = rg_wrapper_script(dir.path(), &marker);

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("ERROR")
        .arg(&file_path)
        .env("TG_RG_PATH", &rg_wrapper);

    let output = run_with_timeout(tg, Duration::from_secs(10));

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert_eq!(stdout.trim(), "TG_RG_OVERRIDE_SENTINEL", "stdout={stdout}");
    assert!(
        marker.exists(),
        "expected override wrapper marker at {}",
        marker.display()
    );
}

#[test]
fn test_tg_search_warns_when_tg_rg_path_override_is_missing() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("search")
        .arg("ERROR")
        .arg(&file_path)
        .env("TG_RG_PATH", dir.path().join("missing-rg.exe"));

    let output = run_with_timeout(tg, Duration::from_secs(10));

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("TG_RG_PATH"), "stderr={stderr}");
    assert!(stderr.contains("is not a file"), "stderr={stderr}");
}

#[test]
fn test_tg_classify_warns_when_tg_sidecar_python_override_is_missing() {
    let dir = tempdir().unwrap();
    let file_path = write_sample_log(dir.path());
    let sidecar_script = write_sidecar_probe_script(dir.path());

    let mut tg = Command::new(env!("CARGO_BIN_EXE_tg"));
    tg.current_dir(repo_root())
        .arg("classify")
        .arg(&file_path)
        .env("TG_SIDECAR_PYTHON", dir.path().join("missing-python.exe"))
        .env("TG_SIDECAR_SCRIPT", &sidecar_script);
    configure_classify_env(&mut tg);

    let output = run_with_timeout(tg, Duration::from_secs(20));

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("TG_SIDECAR_PYTHON"), "stderr={stderr}");
    assert!(stderr.contains("is not a file"), "stderr={stderr}");
}
