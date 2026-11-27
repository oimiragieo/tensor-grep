#![allow(unused_imports, dead_code)]
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use serde_json::Value;
use tempfile::tempdir;

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

pub fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

pub fn run_tg(args: &[&str], cwd: &Path) -> Output {
    tg().current_dir(cwd).args(args).output().unwrap()
}

pub fn run_command_with_stdin(mut command: Command, stdin_bytes: &[u8]) -> Output {
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin_bytes).unwrap();
    child.wait_with_output().unwrap()
}

pub fn write_executable_script(dir: &Path, name: &str, body: &str) -> PathBuf {
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

pub fn output_script(dir: &Path, name: &str, stdout_bytes: &[u8]) -> PathBuf {
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

pub fn fake_rg_script(dir: &Path, stdout_body: &str) -> PathBuf {
    output_script(dir, "fake-rg", stdout_body.as_bytes())
}

/// Task #264: a fake `rg` that inspects ONLY the values passed to `--ignore-file` flags (by
/// filename suffix, so it is platform-separator-agnostic) rather than the whole argv -- built
/// for testing `root_ignore_file_args` (`rg_passthrough.rs`) end-to-end through the real
/// compiled `tg` binary without depending on a real system `rg` being on PATH in CI (mirrors
/// this file's existing `fake_rg_asserting_args_script` pattern).
pub fn fake_rg_asserting_ignore_file_values_script(
    dir: &Path,
    must_include_suffixes: &[&str],
    must_exclude_suffixes: &[&str],
    stdout_text: &str,
) -> PathBuf {
    let script = dir.join("fake-rg-ignorefile-assert.py");
    let must_include_json = serde_json::to_string(must_include_suffixes).unwrap();
    let must_exclude_json = serde_json::to_string(must_exclude_suffixes).unwrap();
    let stdout_text_json = serde_json::to_string(stdout_text).unwrap();
    fs::write(
        &script,
        format!(
            r#"import json, sys
argv = sys.argv[1:]
ignore_file_values = [argv[i + 1] for i, a in enumerate(argv) if a == "--ignore-file" and i + 1 < len(argv)]
must_include = json.loads({must_include_json:?})
must_exclude = json.loads({must_exclude_json:?})
missing = [s for s in must_include if not any(v.endswith(s) for v in ignore_file_values)]
forbidden_present = [s for s in must_exclude if any(v.endswith(s) for v in ignore_file_values)]
if missing or forbidden_present:
    sys.stderr.write(
        "ignore-file mismatch: values=" + repr(ignore_file_values)
        + " missing=" + repr(missing) + " forbidden_present=" + repr(forbidden_present) + "\n"
    )
    raise SystemExit(2)
sys.stdout.write(json.loads({stdout_text_json:?}))
"#,
        ),
    )
    .unwrap();

    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-rg-ignorefile-assert.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-rg-ignorefile-assert",
            &format!("#!/bin/sh\npython \"{}\" \"$@\"\n", script.display()),
        )
    }
}

pub fn fake_python_passthrough_script(dir: &Path, stdout_text: &str) -> PathBuf {
    output_script(
        dir,
        "fake-python-passthrough",
        format!("{stdout_text}\n").as_bytes(),
    )
}

pub fn fake_sidecar_script(dir: &Path) -> PathBuf {
    let response = r#"{"stdout":"{\"classifications\":[{\"label\":\"info\"},{\"label\":\"error\"},{\"label\":\"warn\"}]}","stderr":"","exit_code":0,"pid":123}"#;
    output_script(dir, "fake-sidecar", format!("{response}\n").as_bytes())
}

pub fn fake_rg_asserting_args_script(
    dir: &Path,
    required_args: &[&str],
    stdout_text: &str,
) -> PathBuf {
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

pub fn fake_python_passthrough_asserting_args_script(
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

pub fn fake_python_passthrough_asserting_stdin_script(
    dir: &Path,
    required_args: &[&str],
    expected_stdin: &str,
    stdout_text: &str,
) -> PathBuf {
    let script = dir.join("fake-python-passthrough-stdin.py");
    let required_args_json = serde_json::to_string(required_args).unwrap();
    let expected_stdin_json = serde_json::to_string(expected_stdin).unwrap();
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
expected = json.loads({expected_stdin_json:?})
actual = sys.stdin.read()
if actual != expected:
    sys.stderr.write("stdin mismatch: " + repr(actual) + "\n")
    raise SystemExit(3)
sys.stdout.write(json.loads({stdout_text_json:?}))
"#,
        ),
    )
    .unwrap();

    if cfg!(windows) {
        write_executable_script(
            dir,
            "fake-python-passthrough-stdin.cmd",
            &format!("@echo off\r\npython \"{}\" %*\r\n", script.display()),
        )
    } else {
        write_executable_script(
            dir,
            "fake-python-passthrough-stdin",
            &format!("#!/bin/sh\npython \"{}\" \"$@\"\n", script.display()),
        )
    }
}

pub fn fake_rg_exact_args_script(dir: &Path, expected_args: &[&str], stdout_text: &str) -> PathBuf {
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
