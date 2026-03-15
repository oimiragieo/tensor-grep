#![cfg(windows)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;

use serde_json::Value;
use tempfile::{tempdir, TempDir};
use tensor_grep_rs::backend_ast::AstBackend;

fn write_source_file(extension: &str, content: &str) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

#[test]
fn test_plan_rewrites_substitutes_metavars_in_replacement() {
    let source = "def add(x, y): return x + y\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 1);
    let edit = &plan.edits[0];
    assert_eq!(edit.original_text, "def add(x, y): return x + y");
    assert_eq!(edit.replacement_text, "lambda x, y: x + y");
    assert_eq!(edit.metavar_env.get("F").unwrap(), "add");
    assert_eq!(edit.metavar_env.get("EXPR").unwrap(), "x + y");
    assert!(plan.rejected_overlaps.is_empty());
}

#[test]
fn test_plan_rewrites_handles_multiple_matches_in_one_file() {
    let source = "def first(a): return a\ndef second(b): return b\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 2);
    assert_eq!(plan.edits[0].replacement_text, "lambda a: a");
    assert_eq!(plan.edits[1].replacement_text, "lambda b: b");
    assert!(plan.edits[0].byte_range.end <= plan.edits[1].byte_range.start);
}

#[test]
fn test_plan_rewrites_across_multiple_files() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.py"), "def foo(x): return x\n").unwrap();
    fs::write(dir.path().join("b.py"), "def bar(y): return y\n").unwrap();
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            dir.path().to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 2);
    let files: Vec<&str> = plan.edits.iter().map(|e| e.file.file_name().unwrap().to_str().unwrap()).collect();
    assert!(files.contains(&"a.py"));
    assert!(files.contains(&"b.py"));
}

#[test]
fn test_plan_rewrites_no_matches_returns_empty_plan() {
    let source = "x = 1\ny = 2\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert!(plan.edits.is_empty());
}

#[test]
fn test_apply_rewrites_modifies_file() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    let files_written = AstBackend::apply_rewrites(&plan).unwrap();
    assert_eq!(files_written, 1);

    let result = fs::read_to_string(&file_path).unwrap();
    assert_eq!(result, "lambda x, y: x + y\nlambda a, b: a * b\n");
}

#[test]
fn test_plan_rewrites_edits_sorted_deterministically() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("z.py"), "def last(z): return z\n").unwrap();
    fs::write(dir.path().join("a.py"), "def first(a): return a\n").unwrap();
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            dir.path().to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 2);
    assert!(plan.edits[0].file < plan.edits[1].file);
}

#[test]
fn test_plan_rewrites_javascript_arrow_function() {
    let source = "const fn = (x) => x * 2;\n";
    let (_dir, file_path) = write_source_file("js", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "const $F = ($X) => $BODY",
            "function $F($X) { return $BODY; }",
            "javascript",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 1);
    assert_eq!(
        plan.edits[0].replacement_text,
        "function fn(x) { return x * 2; }"
    );
}

#[test]
fn test_rewrite_plan_serializes_to_json() {
    let source = "def add(x, y): return x + y\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    let json = serde_json::to_string_pretty(&plan).unwrap();
    let parsed: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed["edits"][0]["replacement_text"], "lambda x, y: x + y");
    assert!(parsed["edits"][0]["metavar_env"]["F"].is_string());
}

#[test]
fn test_tg_run_rewrite_dry_run_emits_json_plan() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

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

    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(plan["edits"][0]["replacement_text"], "lambda x, y: x + y");
    assert_eq!(plan["edits"][0]["original_text"], "def add(x, y): return x + y");

    let content = fs::read_to_string(&file_path).unwrap();
    assert!(content.contains("def add"), "dry-run should not modify file");
}

#[test]
fn test_tg_run_rewrite_apply_modifies_file() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, "lambda x, y: x + y\n");

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("applied"), "stderr={stderr}");
}

#[test]
fn test_tg_run_rewrite_no_matches_reports_nothing() {
    let (_dir, file_path) = write_source_file("py", "x = 1\n");

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

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("no matches"), "stderr={stderr}");
}
