use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

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

fn fake_rg_asserting_args_script(dir: &Path, required_args: &[&str], stdout_text: &str) -> PathBuf {
    let script = dir.join("fake-rg-assert.py");
    let required_args_json = serde_json::to_string(required_args).unwrap();
    let stdout_text_json = serde_json::to_string(stdout_text).unwrap();
    fs::write(
        &script,
        format!(
            r#"import json, sys
required = json.loads({required_args_json:?})
missing = [arg for arg in required if arg not in sys.argv[1:]]
if missing:
    sys.stderr.write("missing required rg args: " + ", ".join(missing) + "\n")
    raise SystemExit(2)
sys.stdout.write(json.loads({stdout_text_json:?}))
"#,
        ),
    )
    .unwrap();

    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-rg-assert.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-rg-assert",
            &format!("#!/bin/sh\npython \"{}\" \"$@\"\n", script.display()),
        )
    }
}

fn fake_python_passthrough_asserting_args_script(
    dir: &Path,
    required_args: &[&str],
    stdout_text: &str,
) -> PathBuf {
    let script = dir.join("fake-python-passthrough-assert.py");
    let required_args_json = serde_json::to_string(required_args).unwrap();
    let stdout_text_json = serde_json::to_string(stdout_text).unwrap();
    fs::write(
        &script,
        format!(
            r#"import json, sys
required = json.loads({required_args_json:?})
missing = [arg for arg in required if arg not in sys.argv[1:]]
if missing:
    sys.stderr.write("missing required python passthrough args: " + ", ".join(missing) + "\n")
    raise SystemExit(2)
sys.stdout.write(json.loads({stdout_text_json:?}))
"#,
        ),
    )
    .unwrap();

    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-python-passthrough-assert.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-python-passthrough-assert",
            &format!("#!/bin/sh\npython \"{}\" \"$@\"\n", script.display()),
        )
    }
}

#[test]
fn test_search_no_path_piped_stdin_forwards_no_default_path_to_rg() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(dir.path(), &["-e", "needle"], "stdin needle\n");

    let mut child = tg()
        .current_dir(dir.path())
        .args(["search", "needle"])
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(b"stdin needle\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "stdin needle\n"
    );
}

#[test]
fn test_search_explicit_path_keeps_path_when_stdin_is_piped() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(
        dir.path(),
        &["-e", "needle", "fixture.txt"],
        "fixture.txt:needle file\n",
    );
    fs::write(dir.path().join("fixture.txt"), "needle file\n").unwrap();

    let mut child = tg()
        .current_dir(dir.path())
        .args(["search", "needle", "fixture.txt"])
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(b"stdin needle\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "fixture.txt:needle file\n"
    );
}

#[test]
fn test_root_no_path_piped_stdin_forwards_no_default_path_to_rg() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(
        dir.path(),
        &["--no-ignore", "-e", "needle"],
        "stdin needle\n",
    );

    let mut child = tg()
        .current_dir(dir.path())
        .arg("needle")
        .env("TG_RG_PATH", &fake_rg)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child
        .stdin
        .as_mut()
        .unwrap()
        .write_all(b"stdin needle\n")
        .unwrap();
    let output = child.wait_with_output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "stdin needle\n"
    );
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
fn test_search_editor_output_flags_are_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_asserting_args_script(
        dir.path(),
        &["--vimgrep", "--path-separator", "/"],
        "src/app.log:1:1:ERROR failed\n",
    );
    fs::create_dir(dir.path().join("src")).unwrap();
    fs::write(dir.path().join("src").join("app.log"), "ERROR failed\n").unwrap();

    for args in [
        vec!["search", "--vimgrep", "--path-separator", "/", "ERROR", "."],
        vec![
            "--format",
            "rg",
            "--vimgrep",
            "--path-separator",
            "/",
            "ERROR",
            ".",
        ],
    ] {
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("src/app.log:1:1:ERROR failed"),
            "args={args:?} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "args={args:?} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn test_lsp_provider_args_are_forwarded_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_asserting_args_script(
        dir.path(),
        &["-m", "tensor_grep", "lsp", "--provider", "native"],
        "lsp passthrough ok\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["lsp", "--provider", "native"])
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
        String::from_utf8_lossy(&output.stdout).contains("lsp passthrough ok"),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn fake_rg_exact_args_script(dir: &Path, expected_args: &[&str], stdout_text: &str) -> PathBuf {
    let script = dir.join("fake-rg-exact.py");
    let expected_args_json = serde_json::to_string(expected_args).unwrap();
    let stdout_text_json = serde_json::to_string(stdout_text).unwrap();
    fs::write(
        &script,
        format!(
            r#"import json, sys
expected = json.loads({expected_args_json:?})
actual = sys.argv[1:]
if actual != expected:
    sys.stderr.write("expected rg args " + repr(expected) + " but saw " + repr(actual) + "\n")
    raise SystemExit(2)
sys.stdin.buffer.read()
sys.stdout.write(json.loads({stdout_text_json:?}))
"#,
        ),
    )
    .unwrap();

    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-rg-exact.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-rg-exact",
            &format!("#!/bin/sh\npython \"{}\" \"$@\"\n", script.display()),
        )
    }
}

#[test]
fn test_lsp_help_is_forwarded_to_python_help_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_asserting_args_script(
        dir.path(),
        &["-m", "tensor_grep", "lsp", "--help"],
        "lsp help with --provider hybrid experimental\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["lsp", "--help"])
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
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--provider hybrid"), "stdout={stdout}");
    assert!(stdout.contains("experimental"), "stdout={stdout}");
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
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
        ("--passthrough", &["search", "--passthrough", "ERROR", "."]),
        ("--unicode", &["search", "--unicode", "ERROR", "."]),
        (
            "--auto-hybrid-regex",
            &["search", "--auto-hybrid-regex", "ERROR", "."],
        ),
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
fn test_search_version_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--version"])
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
        String::from_utf8_lossy(&output.stdout).contains("tg "),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn test_top_level_structured_search_accepts_no_ignore() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("ignored.log");
    fs::write(dir.path().join(".gitignore"), "*.log\n").unwrap();
    fs::write(&file, "ERROR hidden by ignore rules\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .arg("--json")
        .arg("--no-ignore")
        .arg("ERROR")
        .arg(&file)
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
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_top_level_format_rg_search_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "app.log:ERROR failed\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    for args in [
        vec!["--format", "rg", "ERROR", "."],
        vec!["--format=rg", "ERROR", "."],
    ] {
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("app.log:ERROR failed"),
            "args={args:?} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "args={args:?} stderr={}",
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
fn test_new_rule_respects_base_dir_and_does_not_scaffold_cwd_root() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("ast-project");

    let output = run_tg(
        &[
            "new",
            "rule",
            "demo",
            "--lang",
            "python",
            "--yes",
            "--base-dir",
            project.to_str().unwrap(),
        ],
        dir.path(),
    );

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !dir.path().join("sgconfig.yml").exists(),
        "tg new rule must not ignore --base-dir and scaffold the current directory"
    );
    assert!(project.join("rules").join("demo.yml").exists());
    assert!(
        !project.join("rules").join("sample-rule.yml").exists(),
        "tg new rule should create the requested rule, not the project sample rule"
    );
}

#[test]
fn test_ast_compatibility_flags_route_or_fail_explicitly_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(dir.path(), "python-route-ok");
    let rule_path = dir.path().join("rule.yml");
    fs::write(
        &rule_path,
        "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)\n",
    )
    .unwrap();

    let scan_output = tg()
        .current_dir(dir.path())
        .args(["scan", "--rule", rule_path.to_str().unwrap(), ".", "--json"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        scan_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        scan_output.status.code(),
        String::from_utf8_lossy(&scan_output.stdout),
        String::from_utf8_lossy(&scan_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&scan_output.stdout).contains("python-route-ok"),
        "stdout={}",
        String::from_utf8_lossy(&scan_output.stdout)
    );

    let new_output = tg()
        .current_dir(dir.path())
        .args(["new", "rule", "demo", "--config", "sgconfig.yml", "--yes"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        new_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        new_output.status.code(),
        String::from_utf8_lossy(&new_output.stdout),
        String::from_utf8_lossy(&new_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&new_output.stdout).contains("python-route-ok"),
        "stdout={}",
        String::from_utf8_lossy(&new_output.stdout)
    );

    let selector_output = run_tg(
        &[
            "run",
            "--pattern",
            "print($A)",
            "--selector",
            "call_expression",
            ".",
        ],
        dir.path(),
    );
    assert!(!selector_output.status.success());
    assert!(
        String::from_utf8_lossy(&selector_output.stderr).contains("not supported by tg run yet"),
        "stderr={}",
        String::from_utf8_lossy(&selector_output.stderr)
    );
}

#[test]
fn test_new_rejects_unsupported_shapes_without_scaffolding_cwd_root() {
    let dir = tempdir().unwrap();

    let output = run_tg(&["new", "rule", "demo", "--unsupported-option"], dir.path());

    assert!(
        !output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Unsupported tg new option"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !dir.path().join("sgconfig.yml").exists(),
        "unsupported tg new shapes must not scaffold the current directory"
    );
    assert!(!dir.path().join("rules").exists());
    assert!(!dir.path().join("tests").exists());
}

#[test]
fn test_new_project_rejects_ignored_name_without_scaffolding_cwd_root() {
    let dir = tempdir().unwrap();
    let output = run_tg(&["new", "project", "demo"], dir.path());

    assert!(
        !output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("does not accept a name"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!dir.path().join("sgconfig.yml").exists());
    assert!(!dir.path().join("rules").exists());
    assert!(!dir.path().join("tests").exists());
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
