#![cfg(unix)]

// audit #110 Gap 1 (Unix half): `test_ast_rewrite.rs` is `#![cfg(windows)]`-gated at the
// top of the file, so a `#[cfg(unix)]` test placed inside it would never compile on any
// platform -- not on Windows (doubly excluded: the inner cfg(unix) fails there too) and
// not on Linux/macOS (the whole file is skipped before the inner cfg is even considered).
// This sibling file carries the real-O_NOFOLLOW half of the write_bytes_refuse_symlink
// regression coverage so it actually runs on the ubuntu-latest/macos-latest legs of the
// `test-rust-core` CI matrix (.github/workflows/ci.yml, `cargo test --verbose
// --no-default-features`). It mirrors the Windows tests in test_ast_rewrite.rs 1:1.

use std::fs;
use std::os::unix::fs::symlink;
use std::path::PathBuf;
use std::process::Command;

use serde_json::Value;
use tempfile::{tempdir, TempDir};

fn write_source_file(extension: &str, content: &str) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

fn write_batch_config(dir: &std::path::Path, payload: &Value) -> PathBuf {
    let config_path = dir.join("batch-rewrite.json");
    fs::write(&config_path, serde_json::to_vec_pretty(payload).unwrap()).unwrap();
    config_path
}

#[test]
fn test_apply_audit_manifest_refuses_symlink_at_manifest_path_unix() {
    // audit #110 Gap 1: a symlink swapped into the --audit-manifest target must not be
    // followed by the Rust-side write (a cross-process TOCTOU on the Python-side
    // confinement check). Plant a symlink pointing OUTSIDE the workdir and confirm apply
    // refuses to write through it (O_NOFOLLOW), leaving the outside target untouched.
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let workdir = file_path.parent().unwrap();

    let outside_dir = tempdir().unwrap();
    let outside_target = outside_dir.path().join("outside-target.json");
    fs::write(&outside_target, b"UNTOUCHED").unwrap();

    let manifest_link = workdir.join("rewrite-audit.json");
    if let Err(err) = symlink(&outside_target, &manifest_link) {
        eprintln!(
            "skipping test_apply_audit_manifest_refuses_symlink_at_manifest_path_unix: \
             cannot create a symlink in this environment: {err}"
        );
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&manifest_link)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "apply must refuse to write the audit manifest through a symlink; stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        fs::read(&outside_target).unwrap(),
        b"UNTOUCHED",
        "the symlink's target outside the workdir must be left untouched"
    );
}

#[test]
fn test_apply_audit_manifest_overwrites_regular_file_on_rerun_unix() {
    // Guard: the legit create-or-overwrite path (a rerun over an EXISTING, larger manifest
    // file) must still succeed -- a regression test for the O_TRUNC + O_NOFOLLOW open in
    // write_bytes_refuse_symlink's unix branch.
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let audit_manifest_path = file_path.parent().unwrap().join("rewrite-audit.json");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let mut stale = fs::read(&audit_manifest_path).unwrap();
    stale.extend(std::iter::repeat_n(b'X', 4096));
    fs::write(&audit_manifest_path, &stale).unwrap();

    let second_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: ($EXPR)")
        .arg("--apply")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("lambda $$$ARGS: $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();
    assert!(
        second_output.status.success(),
        "the legit create-or-overwrite path must still succeed on a rerun over a stale file; stderr={}",
        String::from_utf8_lossy(&second_output.stderr)
    );

    let rewritten_bytes = fs::read(&audit_manifest_path).unwrap();
    assert!(
        rewritten_bytes.len() < stale.len(),
        "rewritten manifest ({} bytes) should be shorter than the padded stale file ({} bytes) it replaced",
        rewritten_bytes.len(),
        stale.len()
    );
    let parsed: Value = serde_json::from_slice(&rewritten_bytes).expect(
        "manifest must parse as clean JSON with no stale trailing bytes left over from the padded write",
    );
    assert_eq!(parsed["kind"], "rewrite-audit-manifest");
}

#[test]
fn test_batch_apply_audit_manifest_refuses_symlink_at_manifest_path_unix() {
    // audit #110 Gap 1, second call site: handle_ast_batch_rewrite_apply also routes
    // through write_audit_manifest_for_plan -- confirm the batch-apply path is covered
    // too, not just the single-rewrite apply path exercised above.
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

    let config_path = write_batch_config(
        dir.path(),
        &serde_json::json!({
            "rewrites": [
                {
                    "pattern": "def $F($$$ARGS): return $EXPR",
                    "replacement": "lambda $$$ARGS: $EXPR",
                    "lang": "python"
                }
            ]
        }),
    );

    let outside_dir = tempdir().unwrap();
    let outside_target = outside_dir.path().join("outside-target.json");
    fs::write(&outside_target, b"UNTOUCHED").unwrap();

    let manifest_link = dir.path().join("rewrite-audit.json");
    if let Err(err) = symlink(&outside_target, &manifest_link) {
        eprintln!(
            "skipping test_batch_apply_audit_manifest_refuses_symlink_at_manifest_path_unix: \
             cannot create a symlink in this environment: {err}"
        );
        return;
    }

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--batch-rewrite")
        .arg(&config_path)
        .arg("--apply")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&manifest_link)
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "batch-apply must refuse to write the audit manifest through a symlink; stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        fs::read(&outside_target).unwrap(),
        b"UNTOUCHED",
        "the symlink's target outside the workdir must be left untouched"
    );
}
