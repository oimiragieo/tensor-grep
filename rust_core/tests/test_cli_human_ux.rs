use serde_json::Value;
use std::fs;
use std::process::Command;
use tempfile::tempdir;

#[test]
fn test_tg_run_rewrite_dry_run_emits_json_plan() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("sample.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .args([
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "--json",
            "def $F($$$ARGS): return $EXPR",
        ])
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(plan["edits"][0]["replacement_text"], "lambda x, y: x + y");
    assert_eq!(
        plan["edits"][0]["original_text"],
        "def add(x, y): return x + y"
    );

    let content = fs::read_to_string(&file_path).unwrap();
    assert!(
        content.contains("def add"),
        "dry-run should not modify file"
    );
}

#[test]
fn test_tg_run_rewrite_dry_run_defaults_to_unified_diff() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("sample.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("-def add(x, y): return x + y"),
        "stdout={stdout}"
    );
    assert!(stdout.contains("+lambda x, y: x + y"), "stdout={stdout}");
}
