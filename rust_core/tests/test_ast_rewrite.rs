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

#[test]
fn test_rewrite_plan_json_contract_fields() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
    let (_dir, file_path) = write_source_file("py", source);

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
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();

    assert_eq!(plan["version"], 1);
    assert_eq!(plan["total_files_scanned"], 1);
    assert_eq!(plan["total_edits"], 2);
    assert_eq!(plan["pattern"], "def $F($$$ARGS): return $EXPR");
    assert_eq!(plan["replacement"], "lambda $$$ARGS: $EXPR");
    assert_eq!(plan["lang"], "python");

    let edits = plan["edits"].as_array().unwrap();
    assert_eq!(edits.len(), 2);

    let e0 = &edits[0];
    let id0 = e0["id"].as_str().unwrap();
    assert!(id0.starts_with("e0000:"), "edit ID should be deterministic: {id0}");
    assert!(id0.contains("fixture.py:"), "edit ID should contain filename: {id0}");
    assert!(e0["metavar_env"]["F"].is_string());
    assert!(e0["metavar_env"]["EXPR"].is_string());
    assert!(e0["metavar_env"]["ARGS"].is_string());

    let e1 = &edits[1];
    let id1 = e1["id"].as_str().unwrap();
    assert!(id1.starts_with("e0001:"), "second edit should have sequential ID: {id1}");
    assert_ne!(id0, id1, "edit IDs must be unique");
}

#[test]
fn test_plan_and_apply_single_pass_produces_correct_plan() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
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

    assert_eq!(plan.version, 1);
    assert_eq!(plan.total_files_scanned, 1);
    assert_eq!(plan.total_edits, 2);
    assert_eq!(plan.edits.len(), 2);
    assert!(!plan.edits[0].id.is_empty());

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, "lambda x, y: x + y\nlambda a, b: a * b\n");
}

#[test]
fn test_tg_run_rewrite_diff_shows_unified_diff() {
    let source = "x = 1\ndef add(x, y): return x + y\nz = 3\n";
    let (_dir, file_path) = write_source_file("py", source);

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--diff")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("--- a/"), "should contain --- header: {stdout}");
    assert!(stdout.contains("+++ b/"), "should contain +++ header: {stdout}");
    assert!(stdout.contains("@@"), "should contain hunk header: {stdout}");
    assert!(stdout.contains("-def add(x, y): return x + y"), "should show removed line: {stdout}");
    assert!(stdout.contains("+lambda x, y: x + y"), "should show added line: {stdout}");

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, source, "diff should not modify file");
}

#[test]
fn test_apply_rewrite_is_idempotent_when_pattern_no_longer_matches() {
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
    AstBackend::apply_rewrites(&plan).unwrap();

    let after_first = fs::read_to_string(&file_path).unwrap();
    assert_eq!(after_first, "lambda x, y: x + y\n");

    let plan2 = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();
    assert!(plan2.edits.is_empty(), "pattern should not match after rewrite");

    let after_second = fs::read_to_string(&file_path).unwrap();
    assert_eq!(after_second, after_first, "file should be unchanged after idempotent re-run");
}

#[test]
fn test_apply_rewrite_preserves_surrounding_code() {
    let source = "import os\n\ndef add(x, y): return x + y\n\nresult = add(1, 2)\n";
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

    AstBackend::apply_rewrites(&plan).unwrap();
    let result = fs::read_to_string(&file_path).unwrap();
    assert!(result.starts_with("import os\n"), "should preserve import: {result}");
    assert!(result.contains("lambda x, y: x + y"), "should have rewrite: {result}");
    assert!(result.ends_with("result = add(1, 2)\n"), "should preserve trailing code: {result}");
}

#[test]
fn test_apply_rewrite_handles_replacement_length_change() {
    let source = "def f(x): return x\ndef g(a, b, c): return a + b + c\n";
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
    AstBackend::apply_rewrites(&plan).unwrap();

    let result = fs::read_to_string(&file_path).unwrap();
    assert_eq!(result, "lambda x: x\nlambda a, b, c: a + b + c\n");
}

#[test]
fn test_apply_rewrite_rust_language() {
    let source = "fn add(a: i32, b: i32) -> i32 { a + b }\n";
    let (_dir, file_path) = write_source_file("rs", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "fn $F($$$PARAMS) -> $RET { $BODY }",
            "fn $F($$$PARAMS) -> $RET { return $BODY; }",
            "rust",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 1);
    AstBackend::apply_rewrites(&plan).unwrap();

    let result = fs::read_to_string(&file_path).unwrap();
    assert!(result.contains("return a + b;"), "should insert return: {result}");
}

#[test]
fn test_rewrite_newline_preservation() {
    let source = "def add(x, y): return x + y\r\ndef mul(a, b): return a * b\r\n";
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

    AstBackend::apply_rewrites(&plan).unwrap();
    let result = fs::read_to_string(&file_path).unwrap();
    assert!(result.contains("\r\n"), "should preserve CRLF line endings: {:?}", result.as_bytes());
}

#[test]
fn test_verify_after_apply_succeeds() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
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

    let verification = plan.verify(&backend).unwrap();
    assert_eq!(verification.total_edits, 2);
    assert_eq!(verification.verified, 2);
    assert!(verification.mismatches.is_empty());
}

#[test]
fn test_tg_run_rewrite_apply_verify_cli() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--verify")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(output.status.success(), "stderr={}", String::from_utf8_lossy(&output.stderr));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("applied"), "stderr={stderr}");
    assert!(stderr.contains("verified"), "stderr={stderr}");

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, "lambda x, y: x + y\n");
}

#[test]
fn test_end_to_end_search_plan_diff_apply_verify() {
    let source = "def greet(name): return name\ndef farewell(who): return who\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();
    let path_str = file_path.to_str().unwrap();

    // Search
    let matches = backend.search("def $F($$$ARGS): return $EXPR", "python", path_str).unwrap();
    assert_eq!(matches.len(), 2);

    // Plan
    let plan = backend.plan_rewrites(
        "def $F($$$ARGS): return $EXPR",
        "lambda $$$ARGS: $EXPR",
        "python",
        path_str,
    ).unwrap();
    assert_eq!(plan.edits.len(), 2);
    assert_eq!(plan.version, 1);
    assert!(!plan.edits[0].id.is_empty());

    // Diff
    let diff = plan.generate_diff().unwrap();
    assert!(diff.contains("-def greet(name): return name"));
    assert!(diff.contains("+lambda name: name"));

    // Apply
    AstBackend::apply_rewrites(&plan).unwrap();
    let after = fs::read_to_string(&file_path).unwrap();
    assert_eq!(after, "lambda name: name\nlambda who: who\n");

    // Verify (re-search with replacement pattern)
    let verification = plan.verify(&backend).unwrap();
    assert_eq!(verification.verified, 2);
    assert!(verification.mismatches.is_empty());
}
