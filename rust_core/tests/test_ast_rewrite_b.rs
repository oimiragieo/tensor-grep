#![allow(unused_imports, dead_code)]
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, UNIX_EPOCH};
use tempfile::{tempdir, TempDir};
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewriteRule};
#[path = "common/ast_rewrite_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_tg_run_apply_verify_json_can_apply_selected_edit_ids_only() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
    let (_dir, file_path) = write_source_file("py", source);

    let plan_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--json")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        plan_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&plan_output.stderr)
    );
    let planned: Value = serde_json::from_slice(&plan_output.stdout).unwrap();
    let selected_id = planned["edits"][0]["id"].as_str().unwrap().to_string();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--apply-edit-ids")
        .arg(&selected_id)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["plan"]["total_edits"], 1);
    assert_eq!(parsed["verification"]["total_edits"], 1);
    assert_eq!(parsed["verification"]["verified"], 1);
    assert_eq!(
        fs::read_to_string(&file_path).unwrap(),
        "lambda x, y: x + y\ndef mul(a, b): return a * b\n"
    );
}

#[test]
fn test_tg_run_apply_edit_ids_rejects_unknown_id() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--apply-edit-ids")
        .arg("missing-edit-id")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("unknown edit id"), "stderr={stderr}");
    assert_eq!(
        fs::read_to_string(&file_path).unwrap(),
        "def add(x, y): return x + y\n"
    );
}

#[test]
fn test_tg_run_apply_verify_json_can_reject_selected_edit_ids() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
    let (_dir, file_path) = write_source_file("py", source);

    let plan_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--json")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        plan_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&plan_output.stderr)
    );
    let planned: Value = serde_json::from_slice(&plan_output.stdout).unwrap();
    let rejected_id = planned["edits"][0]["id"].as_str().unwrap().to_string();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--reject-edit-ids")
        .arg(&rejected_id)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["plan"]["total_edits"], 1);
    assert_eq!(parsed["verification"]["total_edits"], 1);
    assert_eq!(parsed["verification"]["verified"], 1);
    assert_eq!(
        fs::read_to_string(&file_path).unwrap(),
        "def add(x, y): return x + y\nlambda a, b: a * b\n"
    );
}

#[test]
fn test_tg_run_apply_verify_json_includes_validation_results() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(r#"python -c "print('lint-ok')""#)
        .arg("--test-cmd")
        .arg(r#"python -c "print('test-ok')""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["verification"]["verified"], 1);
    assert_eq!(parsed["validation"]["success"], true);
    let commands = parsed["validation"]["commands"].as_array().unwrap();
    assert_eq!(commands.len(), 2);
    assert_eq!(commands[0]["kind"], "lint");
    assert_eq!(commands[1]["kind"], "test");
    assert_eq!(commands[0]["success"], true);
    assert_eq!(commands[1]["success"], true);
    assert!(commands[0]["stdout"].as_str().unwrap().contains("lint-ok"));
    assert!(commands[1]["stdout"].as_str().unwrap().contains("test-ok"));
}

#[test]
fn test_tg_run_apply_validation_accepts_quoted_windows_path_with_spaces() {
    let dir = tempdir().unwrap();
    let spaced_dir = dir.path().join("path with spaces");
    fs::create_dir(&spaced_dir).unwrap();
    let file_path = spaced_dir.join("app.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();
    let lint_cmd = format!("python -m py_compile \"{}\"", file_path.display());

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(&lint_cmd)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["validation"]["success"], true);
    assert_eq!(
        parsed["validation"]["commands"][0]["command"]
            .as_str()
            .unwrap(),
        lint_cmd
    );
}

#[test]
fn test_tg_run_apply_validation_substitutes_file_placeholder() {
    let dir = tempdir().unwrap();
    let spaced_dir = dir.path().join("path with spaces");
    fs::create_dir(&spaced_dir).unwrap();
    let file_path = spaced_dir.join("app.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();
    let expected_cmd = format!("python -m py_compile \"{}\"", file_path.display());

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(r#"python -m py_compile "$file""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["validation"]["success"], true);
    assert_eq!(
        parsed["validation"]["commands"][0]["command"]
            .as_str()
            .unwrap(),
        expected_cmd
    );
}

#[test]
fn test_tg_run_apply_validation_substitutes_file_placeholder_for_each_edited_file_under_directory()
{
    let dir = tempdir().unwrap();
    let src_dir = dir.path().join("src");
    fs::create_dir(&src_dir).unwrap();
    let first_path = src_dir.join("a.py");
    let second_path = src_dir.join("b.py");
    fs::write(&first_path, "def a(x): return x\n").unwrap();
    fs::write(&second_path, "def b(y): return y\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(r#"python -m py_compile "$file""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["validation"]["success"], true);
    let commands = parsed["validation"]["commands"].as_array().unwrap();
    assert_eq!(commands.len(), 2);
    let rendered_commands: Vec<&str> = commands
        .iter()
        .map(|command| command["command"].as_str().unwrap())
        .collect();
    let first_path_text = first_path.to_string_lossy();
    let second_path_text = second_path.to_string_lossy();
    let root_compile_command = format!("python -m py_compile \"{}\"", dir.path().display());
    assert!(rendered_commands
        .iter()
        .any(|command| command.contains(first_path_text.as_ref())));
    assert!(rendered_commands
        .iter()
        .any(|command| command.contains(second_path_text.as_ref())));
    assert!(!rendered_commands
        .iter()
        .any(|command| *command == root_compile_command));
}

#[test]
fn test_tg_run_apply_validation_substitutes_file_placeholder_once_per_file_for_multiple_edits_in_directory(
) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("multi.py");
    fs::write(&file_path, "def a(x): return x\n\ndef b(y): return y\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(r#"python -m py_compile "$file""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(parsed["validation"]["success"], true);
    let commands = parsed["validation"]["commands"].as_array().unwrap();
    assert_eq!(commands.len(), 1);
    assert_eq!(
        commands[0]["command"].as_str().unwrap(),
        format!("python -m py_compile \"{}\"", file_path.display())
    );
}

#[cfg_attr(windows, test)]
fn test_tg_run_apply_verify_json_can_emit_audit_manifest() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let audit_manifest_path = file_path.parent().unwrap().join("rewrite-audit.json");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--checkpoint")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("--lint-cmd")
        .arg(r#"python -c "print('lint-ok')""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let manifest_summary = parsed["audit_manifest"]
        .as_object()
        .expect("audit manifest summary must be present");
    assert_eq!(
        manifest_summary["path"].as_str().unwrap(),
        audit_manifest_path.to_str().unwrap()
    );
    let manifest_json: Value =
        serde_json::from_slice(&fs::read(&audit_manifest_path).unwrap()).unwrap();
    assert_eq!(manifest_json["version"], 1);
    assert_eq!(manifest_json["kind"], "rewrite-audit-manifest");
    assert_eq!(manifest_json["lang"], "python");
    assert_eq!(manifest_json["plan_total_edits"], 1);
    assert_eq!(
        manifest_json["applied_edit_ids"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        manifest_json["checkpoint"]["checkpoint_id"],
        parsed["checkpoint"]["checkpoint_id"]
    );
    assert_eq!(manifest_json["validation"]["success"], true);
    let files = manifest_json["files"].as_array().unwrap();
    assert_eq!(files.len(), 1);
    assert_eq!(
        files[0]["path"].as_str().unwrap(),
        file_path.to_str().unwrap()
    );
    assert!(files[0]["before_sha256"].as_str().unwrap().len() >= 32);
    assert!(files[0]["after_sha256"].as_str().unwrap().len() >= 32);
    assert_eq!(manifest_json["previous_manifest_sha256"], Value::Null);
    let manifest_digest = manifest_json["manifest_sha256"]
        .as_str()
        .unwrap()
        .to_string();
    let mut canonical_manifest = manifest_json.clone();
    canonical_manifest
        .as_object_mut()
        .unwrap()
        .remove("manifest_sha256");
    canonical_manifest
        .as_object_mut()
        .unwrap()
        .remove("signature");
    let mut hasher = Sha256::new();
    hasher.update(serde_json::to_vec_pretty(&canonical_manifest).unwrap());
    assert_eq!(manifest_digest, format!("{:x}", hasher.finalize()));

    let second_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: ($EXPR)")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("lambda $$$ARGS: $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        second_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&second_output.stderr)
    );

    let second_manifest: Value =
        serde_json::from_slice(&fs::read(&audit_manifest_path).unwrap()).unwrap();
    let second_digest = second_manifest["manifest_sha256"]
        .as_str()
        .unwrap()
        .to_string();
    let mut second_canonical = second_manifest.clone();
    second_canonical
        .as_object_mut()
        .unwrap()
        .remove("manifest_sha256");
    second_canonical
        .as_object_mut()
        .unwrap()
        .remove("signature");
    let mut second_hasher = Sha256::new();
    second_hasher.update(serde_json::to_vec_pretty(&second_canonical).unwrap());
    assert_eq!(second_digest, format!("{:x}", second_hasher.finalize()));
    assert_eq!(
        second_manifest["previous_manifest_sha256"],
        Value::String(manifest_digest)
    );
}

#[test]
fn test_apply_audit_manifest_refuses_symlink_at_manifest_path() {
    // audit #110 Gap 1: a symlink swapped into the --audit-manifest target must not be
    // followed by the Rust-side write (a cross-process TOCTOU on the Python-side
    // confinement check). Plant a symlink pointing OUTSIDE the workdir and confirm apply
    // refuses to write through it, leaving the outside target untouched.
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let workdir = file_path.parent().unwrap();

    let outside_dir = tempdir().unwrap();
    let outside_target = outside_dir.path().join("outside-target.json");
    fs::write(&outside_target, b"UNTOUCHED").unwrap();

    let manifest_link = workdir.join("rewrite-audit.json");
    #[cfg(windows)]
    let symlink_result = std::os::windows::fs::symlink_file(&outside_target, &manifest_link);
    #[cfg(unix)]
    let symlink_result = std::os::unix::fs::symlink(&outside_target, &manifest_link);
    if let Err(err) = symlink_result {
        eprintln!(
            "skipping test_apply_audit_manifest_refuses_symlink_at_manifest_path: \
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
fn test_apply_audit_manifest_overwrites_regular_file_on_rerun() {
    // Guard: the legit create-or-overwrite path (a rerun over an EXISTING, larger manifest
    // file) must still succeed and leave no stale trailing bytes -- a regression test for
    // the Windows set_len(0)-after-check ordering and the "create, don't require absence"
    // (no O_EXCL) semantics in write_bytes_refuse_symlink.
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

    // Pad the existing manifest file so it is LONGER than the next write will be -- if the
    // write path ever regresses to "open without truncate", stale trailing bytes here would
    // survive and the reparsed manifest would fail to parse as clean JSON.
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
fn test_batch_apply_audit_manifest_refuses_symlink_at_manifest_path() {
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
    #[cfg(windows)]
    let symlink_result = std::os::windows::fs::symlink_file(&outside_target, &manifest_link);
    #[cfg(unix)]
    let symlink_result = std::os::unix::fs::symlink(&outside_target, &manifest_link);
    if let Err(err) = symlink_result {
        eprintln!(
            "skipping test_batch_apply_audit_manifest_refuses_symlink_at_manifest_path: \
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

#[test]
fn test_tg_run_apply_verify_json_can_sign_audit_manifest() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let audit_manifest_path = file_path.parent().unwrap().join("rewrite-audit.json");
    let signing_key_path = file_path.parent().unwrap().join("audit.key");
    fs::write(&signing_key_path, b"super-secret-key").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("--audit-signing-key")
        .arg(&signing_key_path)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let manifest_summary = parsed["audit_manifest"].as_object().unwrap();
    assert_eq!(manifest_summary["signed"], true);
    assert_eq!(manifest_summary["signature_kind"], "hmac-sha256");

    let manifest_json: Value =
        serde_json::from_slice(&fs::read(&audit_manifest_path).unwrap()).unwrap();
    assert_eq!(manifest_json["signature"]["kind"], "hmac-sha256");
    assert_eq!(
        manifest_json["signature"]["key_path"].as_str().unwrap(),
        signing_key_path.to_str().unwrap()
    );
    assert!(manifest_json["signature"]["value"].as_str().unwrap().len() >= 32);
}

#[test]
fn test_tg_audit_verify_json_accepts_signed_manifest() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let audit_manifest_path = file_path.parent().unwrap().join("rewrite-audit.json");
    let signing_key_path = file_path.parent().unwrap().join("audit.key");
    fs::write(&signing_key_path, b"super-secret-key").unwrap();

    let apply_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("--audit-signing-key")
        .arg(&signing_key_path)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        apply_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&apply_output.stderr)
    );

    let verify_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("audit-verify")
        .arg(&audit_manifest_path)
        .arg("--signing-key")
        .arg(&signing_key_path)
        .arg("--json")
        .output()
        .unwrap();

    assert!(
        verify_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&verify_output.stderr)
    );

    let parsed: Value = serde_json::from_slice(&verify_output.stdout).unwrap();
    assert_eq!(parsed["routing_reason"], "audit-manifest-verify");
    assert_eq!(parsed["checks"]["digest_valid"], true);
    assert_eq!(parsed["checks"]["chain_valid"], true);
    assert_eq!(parsed["checks"]["signature_valid"], true);
    assert_eq!(parsed["valid"], true);
}

#[test]
fn test_tg_audit_verify_json_reports_chain_failure() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");
    let audit_manifest_path = file_path.parent().unwrap().join("rewrite-audit.json");

    let first_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        first_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&first_output.stderr)
    );

    let previous_manifest_path = file_path.parent().unwrap().join("previous-audit.json");
    fs::copy(&audit_manifest_path, &previous_manifest_path).unwrap();
    fs::write(
        &previous_manifest_path,
        br#"{"version":1,"manifest_sha256":"deadbeef"}"#,
    )
    .unwrap();

    let second_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: ($EXPR)")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--audit-manifest")
        .arg(&audit_manifest_path)
        .arg("lambda $$$ARGS: $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        second_output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&second_output.stderr)
    );

    let verify_output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("audit-verify")
        .arg(&audit_manifest_path)
        .arg("--previous-manifest")
        .arg(&previous_manifest_path)
        .arg("--json")
        .output()
        .unwrap();

    assert!(!verify_output.status.success());
    let parsed: Value = serde_json::from_slice(&verify_output.stdout).unwrap();
    assert_eq!(parsed["checks"]["digest_valid"], true);
    assert_eq!(parsed["checks"]["chain_valid"], false);
    assert_eq!(parsed["checks"]["signature_valid"], true);
    assert_eq!(parsed["valid"], false);
    assert!(parsed["errors"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value
            .as_str()
            .unwrap()
            .contains("Previous manifest digest does not match previous_manifest_sha256.")));
}

#[test]
fn test_tg_run_apply_verify_json_reports_failed_validation_and_exits_non_zero() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("--json")
        .arg("--lint-cmd")
        .arg(r#"python -c "import sys; sys.stderr.write('lint-fail'); sys.exit(3)""#)
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );

    let parsed: Value =
        serde_json::from_slice(&output.stdout).expect("stdout must remain valid JSON on failure");
    assert_eq!(parsed["verification"]["verified"], 1);
    assert_eq!(parsed["validation"]["success"], false);
    let commands = parsed["validation"]["commands"].as_array().unwrap();
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0]["kind"], "lint");
    assert_eq!(commands[0]["success"], false);
    assert_eq!(commands[0]["exit_code"], 3);
    assert!(commands[0]["stderr"]
        .as_str()
        .unwrap()
        .contains("lint-fail"));
    assert_eq!(parsed["rollback"]["success"], true);
    assert_eq!(parsed["rollback"]["triggered_by"], "validation");
    assert_eq!(
        parsed["rollback"]["files_restored"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        fs::read_to_string(&file_path).unwrap(),
        "def add(x, y): return x + y\n"
    );
}

#[test]
fn test_verify_detects_tampered_file() {
    let source = "def add(x, y): return x + y\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_and_apply(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    fs::write(&file_path, "TAMPERED CONTENT\n").unwrap();

    let verification = plan.verify(&backend).unwrap();
    assert!(
        !verification.mismatches.is_empty(),
        "should detect tampered file"
    );
    assert_eq!(verification.verified, 0);
}

#[test]
fn test_verify_multi_edit_with_length_changes() {
    let source = "def f(x): return x\ndef g(a, b, c): return a + b + c\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_and_apply(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 2);
    let v = plan.verify(&backend).unwrap();
    assert_eq!(v.verified, 2);
    assert!(v.mismatches.is_empty());
}

#[test]
fn test_plan_and_apply_does_not_write_rejected_overlaps() {
    let source = "def add(x, y): return x + y\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_and_apply(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert!(plan.rejected_overlaps.is_empty());
    assert_eq!(plan.edits.len(), 1);

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, "lambda x, y: x + y\n");
}

#[test]
fn test_tg_run_batch_rewrite_apply_executes_multiple_rules_in_one_pass() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(
        &file_path,
        "def add(x): return x\ndef mul(y): return y\nvalue = add(1)\nprint(value)\n",
    )
    .unwrap();

    let config_path = write_batch_config(
        dir.path(),
        &serde_json::json!({
            "rewrites": [
                {
                    "pattern": "def $F($$$ARGS): return $EXPR",
                    "replacement": "lambda $$$ARGS: $EXPR",
                    "lang": "python"
                },
                {
                    "pattern": "value = $EXPR",
                    "replacement": "result = $EXPR",
                    "lang": "python"
                },
                {
                    "pattern": "print($MSG)",
                    "replacement": "emit($MSG)",
                    "lang": "python"
                }
            ],
            "verify": true
        }),
    );

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--batch-rewrite")
        .arg(&config_path)
        .arg("--apply")
        .arg("--json")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["version"], 1);
    assert_eq!(payload["routing_backend"], "AstBackend");
    assert_eq!(payload["routing_reason"], "ast-native");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["plan"]["total_edits"], 4);
    assert_eq!(payload["plan"]["rewrites"].as_array().unwrap().len(), 3);
    assert_eq!(payload["verification"]["verified"], 4);

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(
        content,
        "lambda x: x\nlambda y: y\nresult = add(1)\nemit(value)\n"
    );
}

#[test]
fn test_tg_run_batch_rewrite_reports_cross_pattern_overlap_and_leaves_file_unchanged() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    let original = "def add(x): return x\n";
    fs::write(&file_path, original).unwrap();

    let config_path = write_batch_config(
        dir.path(),
        &serde_json::json!({
            "rewrites": [
                {
                    "pattern": "def $F($$$ARGS): return $EXPR",
                    "replacement": "lambda $$$ARGS: $EXPR",
                    "lang": "python"
                },
                {
                    "pattern": "return $EXPR",
                    "replacement": "yield $EXPR",
                    "lang": "python"
                }
            ],
            "verify": false
        }),
    );

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--batch-rewrite")
        .arg(&config_path)
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["total_edits"], 0);
    let rejected = payload["rejected_overlaps"].as_array().unwrap();
    assert!(!rejected.is_empty(), "payload={payload}");
    assert!(
        rejected[0]["reason"].as_str().unwrap().contains("overlap"),
        "payload={payload}"
    );
    assert_eq!(fs::read_to_string(&file_path).unwrap(), original);
}

#[test]
fn test_batch_rewrite_preserves_bom_crlf_and_skips_binary_files() {
    const UTF8_BOM: &[u8; 3] = b"\xEF\xBB\xBF";

    let dir = tempdir().unwrap();
    let text_path = dir.path().join("fixture.py");
    let binary_path = dir.path().join("binary.py");

    let mut source = UTF8_BOM.to_vec();
    source.extend_from_slice(b"import os\r\ndef add(x): return x\r\nvalue = add(1)\r\n");
    fs::write(&text_path, source).unwrap();
    fs::write(&binary_path, b"def add(x): return x\0garbage\n").unwrap();

    let config_path = write_batch_config(
        dir.path(),
        &serde_json::json!({
            "rewrites": [
                {
                    "pattern": "def $F($$$ARGS): return $EXPR",
                    "replacement": "lambda $$$ARGS: $EXPR",
                    "lang": "python"
                },
                {
                    "pattern": "value = $EXPR",
                    "replacement": "result = $EXPR",
                    "lang": "python"
                }
            ],
            "verify": true
        }),
    );

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--batch-rewrite")
        .arg(&config_path)
        .arg("--apply")
        .arg("--json")
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let rewritten = fs::read(&text_path).unwrap();
    assert!(rewritten.starts_with(UTF8_BOM));
    assert_eq!(&rewritten[..UTF8_BOM.len()], UTF8_BOM);
    assert_eq!(
        &rewritten[UTF8_BOM.len()..],
        b"import os\r\nlambda x: x\r\nresult = add(1)\r\n"
    );
    assert_eq!(
        fs::read(&binary_path).unwrap(),
        b"def add(x): return x\0garbage\n"
    );
}

#[test]
fn test_batch_rewrite_apply_rejects_stale_file_without_writing_other_files() {
    let dir = tempdir().unwrap();
    let stale_file = dir.path().join("a.py");
    let untouched_file = dir.path().join("b.py");
    fs::write(&stale_file, "def add(x): return x\n").unwrap();
    fs::write(&untouched_file, "def mul(y): return y\n").unwrap();
    let backend = AstBackend::new();
    let rewrites = vec![
        BatchRewriteRule {
            pattern: "def $F($$$ARGS): return $EXPR".to_string(),
            replacement: "lambda $$$ARGS: $EXPR".to_string(),
            lang: "python".to_string(),
        },
        BatchRewriteRule {
            pattern: "lambda $ARGS: $EXPR".to_string(),
            replacement: "lambda $ARGS: $EXPR".to_string(),
            lang: "python".to_string(),
        },
    ];

    let plan = backend
        .plan_batch_rewrites(&rewrites, dir.path().to_str().unwrap())
        .unwrap();

    std::thread::sleep(Duration::from_millis(25));
    let modified_content = "def add(x): return x + 1\n";
    fs::write(&stale_file, modified_content).unwrap();

    let error = AstBackend::apply_batch_rewrites(&plan).unwrap_err();
    let message = format!("{error:#}");
    assert!(
        message.contains("stale") || message.contains("modified"),
        "unexpected error: {message}"
    );
    assert!(
        message.contains(stale_file.to_str().unwrap()),
        "error should include file path: {message}"
    );

    assert_eq!(fs::read_to_string(&stale_file).unwrap(), modified_content);
    assert_eq!(
        fs::read_to_string(&untouched_file).unwrap(),
        "def mul(y): return y\n",
        "other files should not be rewritten when any planned file is stale"
    );
}

#[test]
fn test_tg_run_batch_rewrite_invalid_config_reports_field_level_error() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("fixture.py");
    fs::write(&file_path, "def add(x): return x\n").unwrap();
    let config_path = write_batch_config(
        dir.path(),
        &serde_json::json!({
            "rewrites": [
                {
                    "pattern": "def $F($$$ARGS): return $EXPR",
                    "lang": "python"
                }
            ],
            "verify": false
        }),
    );

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--batch-rewrite")
        .arg(&config_path)
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "stdout={}",
        String::from_utf8_lossy(&output.stdout)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("rewrites[0].replacement"),
        "stderr={stderr}"
    );
}
