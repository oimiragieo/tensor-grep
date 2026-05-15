use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use serde_json::Value;
use tempfile::tempdir;

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

fn run_tg(args: &[&str], cwd: &Path) -> Output {
    tg().current_dir(cwd).args(args).output().unwrap()
}

fn write_executable_script(dir: &Path, name: &str, body: &str) -> PathBuf {
    let script = dir.join(name);
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

fn output_script(dir: &Path, name: &str, stdout_bytes: &[u8]) -> PathBuf {
    let payload_path = dir.join(format!("{name}.out"));
    fs::write(&payload_path, stdout_bytes).unwrap();
    let payload = payload_path
        .to_string_lossy()
        .replace('\\', "\\\\")
        .replace('"', "\\\"");

    if cfg!(windows) {
        write_executable_script(
            dir,
            &format!("{name}.cmd"),
            &format!(
                "@echo off\r\npython -c \"import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(\\\"{payload}\\\").read_bytes())\"\r\n"
            ),
        )
    } else {
        write_executable_script(
            dir,
            name,
            &format!(
                "#!/bin/sh\npython -c \"import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(\\\"{payload}\\\").read_bytes())\"\n"
            ),
        )
    }
}

fn fake_rg_script(dir: &Path, stdout_body: &str) -> PathBuf {
    output_script(dir, "fake-rg", stdout_body.as_bytes())
}

fn fake_python_passthrough_script(dir: &Path, stdout_text: &str) -> PathBuf {
    output_script(
        dir,
        "fake-python-passthrough",
        format!("{stdout_text}\n").as_bytes(),
    )
}

fn fake_sidecar_script(dir: &Path) -> PathBuf {
    let response = r#"{"stdout":"{\"classifications\":[{\"label\":\"info\"},{\"label\":\"error\"},{\"label\":\"warn\"}]}","stderr":"","exit_code":0,"pid":123}"#;
    output_script(dir, "fake-sidecar", format!("{response}\n").as_bytes())
}

#[test]
fn test_search_accepts_multiline_flags_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "invoice.py:def create_invoice\n");
    fs::write(
        dir.path().join("invoice.py"),
        "def create_invoice(subtotal):\n    tax = subtotal * 0.1\n    return subtotal + tax\n",
    )
    .unwrap();

    for flag in ["--multiline", "-U"] {
        let output = tg()
            .current_dir(dir.path())
            .args(["search", flag, r"create_invoice[\s\S]*return", "."])
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "flag={flag} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("create_invoice"),
            "flag={flag} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
    }
}

#[test]
fn test_search_files_and_null_path_output_work_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(dir.path(), "visible.rs");
    let fake_rg = fake_rg_script(dir.path(), "app.log\0");
    fs::write(dir.path().join("visible.rs"), "fn main() {}\n").unwrap();
    fs::write(dir.path().join("app.log"), "INFO ok\nERROR failed\n").unwrap();

    let files_output = tg()
        .current_dir(dir.path())
        .args(["search", "--files", ".", "--hidden", "--glob", "*.rs"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        files_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        files_output.status.code(),
        String::from_utf8_lossy(&files_output.stdout),
        String::from_utf8_lossy(&files_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&files_output.stdout).contains("visible.rs"),
        "stdout={}",
        String::from_utf8_lossy(&files_output.stdout)
    );

    let null_output = tg()
        .current_dir(dir.path())
        .args([
            "search",
            "--fixed-strings",
            "ERROR",
            ".",
            "--files-with-matches",
            "--null",
        ])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();
    assert!(
        null_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        null_output.status.code(),
        String::from_utf8_lossy(&null_output.stdout),
        String::from_utf8_lossy(&null_output.stderr)
    );
    assert!(
        null_output.stdout.contains(&0),
        "expected NUL-separated path output, stdout bytes={:?}",
        null_output.stdout
    );
}

#[test]
fn test_search_help_advertised_rg_flags_are_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "accepted\n");
    let fake_python = fake_python_passthrough_script(dir.path(), "accepted");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    let flag_cases: &[(&str, &[&str])] = &[
        ("--passthru", &["search", "--passthru", "ERROR", "."]),
        (
            "--no-ignore-dot",
            &["search", "--no-ignore-dot", "ERROR", "."],
        ),
        (
            "--no-ignore-exclude",
            &["search", "--no-ignore-exclude", "ERROR", "."],
        ),
        (
            "--no-ignore-files",
            &["search", "--no-ignore-files", "ERROR", "."],
        ),
        (
            "--no-ignore-global",
            &["search", "--no-ignore-global", "ERROR", "."],
        ),
        (
            "--no-ignore-parent",
            &["search", "--no-ignore-parent", "ERROR", "."],
        ),
        ("--no-config", &["search", "--no-config", "ERROR", "."]),
        ("--type-list", &["search", "--type-list"]),
        ("--pcre2-version", &["search", "--pcre2-version"]),
    ];

    for (flag, args) in flag_cases {
        let output = tg()
            .current_dir(dir.path())
            .args(*args)
            .env("TG_RG_PATH", &fake_rg)
            .env("TG_SIDECAR_PYTHON", &fake_python)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "flag={flag} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "flag={flag} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn test_top_level_type_list_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "rust: *.rs\n");

    let output = tg()
        .current_dir(dir.path())
        .arg("--type-list")
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("rust:"));
}

#[test]
fn test_run_short_rewrite_alias_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("sample.py");
    fs::write(&file, "print('hello')\n").unwrap();

    let output = run_tg(
        &[
            "run",
            "--lang",
            "python",
            "-r",
            "print('bye')",
            "print('hello')",
            file.to_str().unwrap(),
        ],
        &repo_root(),
    );

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_classify_format_json_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("sample.log");
    let fake_sidecar = fake_sidecar_script(dir.path());
    fs::write(&file, "INFO ok\nERROR database failed\nWARN retrying\n").unwrap();

    let output = tg()
        .current_dir(repo_root())
        .arg("classify")
        .arg("--format")
        .arg("json")
        .arg(&file)
        .env("TG_SIDECAR_PYTHON", &fake_sidecar)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["classifications"].as_array().unwrap().len(), 3);
}

#[test]
fn test_agent_json_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("project");
    fs::create_dir(&project).unwrap();
    fs::write(
        project.join("payments.py"),
        "def create_invoice(total, tax):\n    return total + tax\n",
    )
    .unwrap();
    let fake_python = fake_python_passthrough_script(
        dir.path(),
        r#"{"routing_reason":"agent-context-capsule","capsule_kind":"actionable_context"}"#,
    );

    let output = tg()
        .current_dir(repo_root())
        .arg("agent")
        .arg("--query")
        .arg("change invoice tax calculation")
        .arg("--json")
        .arg(&project)
        .env("TG_SIDECAR_PYTHON", &fake_python)
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
        !String::from_utf8_lossy(&output.stderr).contains("unrecognized subcommand"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_reason"], "agent-context-capsule");
}

#[test]
fn test_repair_launcher_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(
        dir.path(),
        r#"{"status":"blocked_requires_allow_foreign_rename","message":"blocked"}"#,
    );

    let output = tg()
        .current_dir(repo_root())
        .arg("repair-launcher")
        .arg("--json")
        .env("TG_SIDECAR_PYTHON", &fake_python)
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
        !String::from_utf8_lossy(&output.stderr).contains("unrecognized subcommand"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["status"], "blocked_requires_allow_foreign_rename");
}
