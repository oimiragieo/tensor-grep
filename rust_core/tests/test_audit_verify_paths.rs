use serde_json::Value;
use std::fs;
use std::process::Command;
use tempfile::tempdir;

#[test]
fn test_audit_verify_strips_windows_extended_length_prefix() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("sample.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();
    let audit_manifest_path = dir.path().join("rewrite-audit.json");
    let signing_key_path = dir.path().join("audit.key");
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
    assert_eq!(parsed["valid"], true);
    let manifest_path = parsed["manifest_path"].as_str().unwrap();
    assert!(
        !manifest_path.starts_with(r"\\?\"),
        "manifest_path should strip Windows extended prefix: {manifest_path:?}"
    );
    let key_path = parsed["signing_key_path"].as_str().unwrap();
    assert!(
        !key_path.starts_with(r"\\?\"),
        "signing_key_path should strip Windows extended prefix: {key_path:?}"
    );
}
