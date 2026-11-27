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
    let files: Vec<&str> = plan
        .edits
        .iter()
        .map(|e| e.file.file_name().unwrap().to_str().unwrap())
        .collect();
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
fn test_rewrite_plan_captures_planned_file_mtime() {
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

    let planned_mtime_ns = plan.edits[0].planned_mtime_ns;
    assert!(planned_mtime_ns > 0);
    assert_eq!(planned_mtime_ns, file_mtime_ns(&file_path));

    let json = serde_json::to_value(&plan).unwrap();
    assert_eq!(
        json["edits"][0]["planned_mtime_ns"].as_u64().unwrap(),
        planned_mtime_ns
    );
}

#[test]
fn test_apply_rewrites_rejects_stale_file_without_writing_other_files() {
    let dir = tempdir().unwrap();
    let stale_file = dir.path().join("a.py");
    let untouched_file = dir.path().join("b.py");
    fs::write(&stale_file, "def add(x): return x\n").unwrap();
    fs::write(&untouched_file, "def mul(y): return y\n").unwrap();
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            dir.path().to_str().unwrap(),
        )
        .unwrap();

    std::thread::sleep(Duration::from_millis(25));
    let modified_content = "def add(x): return x + 1\n";
    fs::write(&stale_file, modified_content).unwrap();
    assert_ne!(plan.edits[0].planned_mtime_ns, file_mtime_ns(&stale_file));

    let error = AstBackend::apply_rewrites(&plan).unwrap_err();
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
        .args([
            "run",
            "--lang",
            "python",
            "--rewrite",
            "lambda $$$ARGS: $EXPR",
            "def $F($$$ARGS): return $EXPR",
        ])
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("no matches"), "stderr={stderr}");
}

#[test]
fn test_tg_run_rewrite_apply_no_matches_reports_nothing() {
    let (_dir, file_path) = write_source_file("py", "x = 1\n");

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
    assert_eq!(fs::read_to_string(&file_path).unwrap(), "x = 1\n");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("no matches"), "stderr={stderr}");
}

#[test]
fn test_tg_run_rewrite_apply_json_no_matches_emits_empty_payload() {
    let (_dir, file_path) = write_source_file("py", "x = 1\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--apply")
        .arg("--json")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: Value =
        serde_json::from_slice(&output.stdout).expect("no-match apply stdout must still be JSON");
    assert_eq!(parsed["version"], 1);
    assert_eq!(parsed["plan"]["total_edits"], 0);
    assert_eq!(parsed["validation"], Value::Null);
    assert_eq!(parsed["verification"], Value::Null);
    assert_eq!(parsed["rollback"], Value::Null);
    assert_eq!(fs::read_to_string(&file_path).unwrap(), "x = 1\n");
    assert!(
        output.stderr.is_empty(),
        "json no-match apply should not emit human stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_rewrite_plan_json_contract_fields() {
    let source = "def add(x, y): return x + y\ndef mul(a, b): return a * b\n";
    let (_dir, file_path) = write_source_file("py", source);

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

    assert!(output.status.success());
    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();

    assert_eq!(plan["version"], 1);
    assert_eq!(plan["routing_backend"], "AstBackend");
    assert_eq!(plan["routing_reason"], "ast-native");
    assert_eq!(plan["sidecar_used"], false);
    assert_eq!(plan["total_files_scanned"], 1);
    assert_eq!(plan["total_edits"], 2);
    assert_eq!(plan["pattern"], "def $F($$$ARGS): return $EXPR");
    assert_eq!(plan["replacement"], "lambda $$$ARGS: $EXPR");
    assert_eq!(plan["lang"], "python");

    let edits = plan["edits"].as_array().unwrap();
    assert_eq!(edits.len(), 2);

    let e0 = &edits[0];
    let id0 = e0["id"].as_str().unwrap();
    assert!(
        id0.starts_with("e0000:"),
        "edit ID should be deterministic: {id0}"
    );
    assert!(
        id0.contains("fixture.py:"),
        "edit ID should contain filename: {id0}"
    );
    let planned_mtime_ns = e0["planned_mtime_ns"].as_u64().unwrap();
    assert!(
        planned_mtime_ns > 0,
        "planned_mtime_ns should be present in rewrite plan JSON"
    );
    assert!(e0["metavar_env"]["F"].is_string());
    assert!(e0["metavar_env"]["EXPR"].is_string());
    assert!(e0["metavar_env"]["ARGS"].is_string());

    let e1 = &edits[1];
    let id1 = e1["id"].as_str().unwrap();
    assert!(
        id1.starts_with("e0001:"),
        "second edit should have sequential ID: {id1}"
    );
    assert_ne!(id0, id1, "edit IDs must be unique");
    assert_eq!(e1["planned_mtime_ns"].as_u64().unwrap(), planned_mtime_ns);
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("--- a/"),
        "should contain --- header: {stdout}"
    );
    assert!(
        stdout.contains("+++ b/"),
        "should contain +++ header: {stdout}"
    );
    assert!(
        stdout.contains("@@"),
        "should contain hunk header: {stdout}"
    );
    assert!(
        stdout.contains("-def add(x, y): return x + y"),
        "should show removed line: {stdout}"
    );
    assert!(
        stdout.contains("+lambda x, y: x + y"),
        "should show added line: {stdout}"
    );

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, source, "diff should not modify file");
}

#[test]
fn test_tg_run_rewrite_diff_json_wraps_unified_diff() {
    let source = "x = 1\ndef add(x, y): return x + y\nz = 3\n";
    let (_dir, file_path) = write_source_file("py", source);

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--diff")
        .arg("--json")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: Value = serde_json::from_slice(&output.stdout).expect("diff stdout must be JSON");
    assert_eq!(parsed["version"], 1);
    assert_eq!(parsed["routing_backend"], "AstBackend");
    assert_eq!(parsed["routing_reason"], "ast-native");
    assert_eq!(parsed["sidecar_used"], false);
    assert_eq!(parsed["plan"]["total_edits"], 1);
    let diff = parsed["diff"].as_str().expect("diff must be a string");
    assert!(diff.contains("--- a/"), "should contain --- header: {diff}");
    assert!(diff.contains("+++ b/"), "should contain +++ header: {diff}");
    assert!(diff.contains("@@"), "should contain hunk header: {diff}");
    assert!(
        diff.contains("-def add(x, y): return x + y"),
        "should show removed line: {diff}"
    );
    assert!(
        diff.contains("+lambda x, y: x + y"),
        "should show added line: {diff}"
    );

    let content = fs::read_to_string(&file_path).unwrap();
    assert_eq!(content, source, "diff should not modify file");
}

#[test]
fn test_tg_run_rewrite_diff_json_no_matches_emits_empty_payload() {
    let source = "x = 1\n";
    let (_dir, file_path) = write_source_file("py", source);

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("--diff")
        .arg("--json")
        .arg("def $F($$$ARGS): return $EXPR")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: Value =
        serde_json::from_slice(&output.stdout).expect("no-match diff stdout must still be JSON");
    assert_eq!(parsed["version"], 1);
    assert_eq!(parsed["plan"]["total_edits"], 0);
    assert_eq!(parsed["diff"], "");
    assert!(
        output.stderr.is_empty(),
        "json no-match diff should not emit human stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
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
    assert!(
        plan2.edits.is_empty(),
        "pattern should not match after rewrite"
    );

    let after_second = fs::read_to_string(&file_path).unwrap();
    assert_eq!(
        after_second, after_first,
        "file should be unchanged after idempotent re-run"
    );
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
    assert!(
        result.starts_with("import os\n"),
        "should preserve import: {result}"
    );
    assert!(
        result.contains("lambda x, y: x + y"),
        "should have rewrite: {result}"
    );
    assert!(
        result.ends_with("result = add(1, 2)\n"),
        "should preserve trailing code: {result}"
    );
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
    assert!(
        result.contains("return a + b;"),
        "should insert return: {result}"
    );
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
    assert!(
        result.contains("\r\n"),
        "should preserve CRLF line endings: {:?}",
        result.as_bytes()
    );
}

#[test]
fn test_rewrite_preserves_utf8_bom_and_adjusts_byte_offsets() {
    const UTF8_BOM: &[u8; 3] = b"\xEF\xBB\xBF";

    let mut source = UTF8_BOM.to_vec();
    source.extend_from_slice(b"def add(x): return x\n");
    let (_dir, file_path) = write_source_bytes_file("py", &source);
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
    assert_eq!(plan.edits[0].byte_range.start, UTF8_BOM.len());

    AstBackend::apply_rewrites(&plan).unwrap();

    let rewritten = fs::read(&file_path).unwrap();
    assert!(rewritten.starts_with(UTF8_BOM));
    assert_eq!(
        rewritten
            .windows(UTF8_BOM.len())
            .filter(|window| *window == UTF8_BOM)
            .count(),
        1
    );
    assert_eq!(&rewritten[UTF8_BOM.len()..], b"lambda x: x\n");
}

#[test]
fn test_rewrite_preserves_crlf_outside_edited_ranges() {
    let source = "import os\r\ndef add(x): return x\r\nvalue = add(1)\r\n";
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

    let result = fs::read_to_string(&file_path).unwrap();
    assert_eq!(result, "import os\r\nlambda x: x\r\nvalue = add(1)\r\n");
}

#[test]
fn test_rewrite_handles_non_ascii_without_corruption() {
    let source = "print(\"こんにちは😀\")\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "print($MSG)",
            "log($MSG)",
            "python",
            file_path.to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.edits.len(), 1);
    let original = fs::read_to_string(&file_path).unwrap();
    assert!(original.is_char_boundary(plan.edits[0].byte_range.start));
    assert!(original.is_char_boundary(plan.edits[0].byte_range.end));

    AstBackend::apply_rewrites(&plan).unwrap();

    let result = fs::read_to_string(&file_path).unwrap();
    assert_eq!(result, "log(\"こんにちは😀\")\n");
}

#[test]
fn test_rewrite_planning_skips_binary_files_without_error() {
    let dir = tempdir().unwrap();
    let binary_path = dir.path().join("binary.py");
    let text_path = dir.path().join("text.py");
    fs::write(&binary_path, b"def add(x): return x\0garbage\n").unwrap();
    fs::write(&text_path, "def mul(y): return y\n").unwrap();
    let backend = AstBackend::new();

    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            dir.path().to_str().unwrap(),
        )
        .unwrap();

    assert_eq!(plan.total_files_scanned, 2);
    assert_eq!(plan.edits.len(), 1);
    assert_eq!(plan.edits[0].file, text_path);
    assert_eq!(
        fs::read(&binary_path).unwrap(),
        b"def add(x): return x\0garbage\n"
    );
}

#[test]
fn test_tg_run_rewrite_skips_large_files_with_warning_and_processes_other_files() {
    let dir = tempdir().unwrap();
    let small_path = dir.path().join("small.py");
    let large_path = dir.path().join("large.py");
    fs::write(&small_path, "def add(x): return x\n").unwrap();
    create_sparse_file(&large_path, 100_u64 * 1024 * 1024 + 1);

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
        .arg(dir.path())
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );

    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("warning"), "stderr={stderr}");
    assert!(stderr.contains("large.py"), "stderr={stderr}");
    assert!(
        stderr.contains("100 MB") || stderr.contains("100MB"),
        "stderr={stderr}"
    );

    let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(plan["total_edits"], 1);
    assert_eq!(plan["edits"].as_array().unwrap().len(), 1);
    assert!(plan["edits"][0]["file"]
        .as_str()
        .unwrap()
        .ends_with("small.py"));
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
fn test_batch_plan_and_apply_verify_succeeds() {
    let dir = tempdir().unwrap();
    let first_file = dir.path().join("defs.py");
    let second_file = dir.path().join("values.py");
    fs::write(&first_file, "def add(x): return x\n").unwrap();
    fs::write(&second_file, "value = add(1)\n").unwrap();

    let backend = AstBackend::new();
    let rewrites = vec![
        BatchRewriteRule {
            pattern: "def $F($$$ARGS): return $EXPR".to_string(),
            replacement: "lambda $$$ARGS: $EXPR".to_string(),
            lang: "python".to_string(),
        },
        BatchRewriteRule {
            pattern: "value = $EXPR".to_string(),
            replacement: "result = $EXPR".to_string(),
            lang: "python".to_string(),
        },
    ];

    let plan = backend
        .plan_and_apply_batch(&rewrites, dir.path().to_str().unwrap())
        .unwrap();

    let verification = plan.verify(&backend).unwrap();
    assert_eq!(verification.total_edits, 2);
    assert_eq!(verification.verified, 2);
    assert!(verification.mismatches.is_empty());

    assert_eq!(fs::read_to_string(&first_file).unwrap(), "lambda x: x\n");
    assert_eq!(
        fs::read_to_string(&second_file).unwrap(),
        "result = add(1)\n"
    );
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

    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
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
    let matches = backend
        .search("def $F($$$ARGS): return $EXPR", "python", path_str)
        .unwrap();
    assert_eq!(matches.len(), 2);

    // Plan
    let plan = backend
        .plan_rewrites(
            "def $F($$$ARGS): return $EXPR",
            "lambda $$$ARGS: $EXPR",
            "python",
            path_str,
        )
        .unwrap();
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

#[test]
fn test_tg_run_apply_verify_json_is_single_document() {
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
    let parsed: Value =
        serde_json::from_str(&stdout).expect("stdout must be single valid JSON document");
    assert_eq!(parsed["version"], 1);
    assert_eq!(parsed["routing_backend"], "AstBackend");
    assert_eq!(parsed["routing_reason"], "ast-native");
    assert_eq!(parsed["sidecar_used"], false);
    assert!(parsed["plan"].is_object(), "must have plan field");
    assert!(
        parsed["verification"].is_object(),
        "must have verification field"
    );
    assert_eq!(parsed["plan"]["version"], 1);
    assert_eq!(parsed["verification"]["total_edits"], 1);
    assert_eq!(parsed["verification"]["verified"], 1);
    assert!(parsed["verification"]["mismatches"]
        .as_array()
        .unwrap()
        .is_empty());
}

#[cfg_attr(windows, test)]
fn test_tg_run_apply_verify_json_can_create_checkpoint() {
    let (_dir, file_path) = write_source_file("py", "def add(x, y): return x + y\n");

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
    let checkpoint = parsed["checkpoint"]
        .as_object()
        .expect("checkpoint metadata must be present");
    let checkpoint_id = checkpoint["checkpoint_id"]
        .as_str()
        .expect("checkpoint id must be a string");
    assert!(!checkpoint_id.is_empty());
    assert_eq!(checkpoint["file_count"], 1);
    assert_eq!(checkpoint["mode"], "filesystem-snapshot");

    let checkpoint_root = file_path.parent().unwrap();
    let metadata_path = checkpoint_root
        .join(".tensor-grep")
        .join("checkpoints")
        .join(checkpoint_id)
        .join("metadata.json");
    let index_path = checkpoint_root
        .join(".tensor-grep")
        .join("checkpoints")
        .join("index.json");
    assert!(
        metadata_path.exists(),
        "missing {}",
        metadata_path.display()
    );
    assert!(index_path.exists(), "missing {}", index_path.display());
}

#[cfg_attr(windows, test)]
fn test_tg_run_apply_checkpoint_for_file_inside_git_repo_stays_file_scoped() {
    if Command::new("git").arg("--version").output().is_err() {
        return;
    }

    let dir = tempdir().unwrap();
    let project = dir.path();
    let source_dir = project.join("src");
    fs::create_dir_all(&source_dir).unwrap();
    let file_path = source_dir.join("fixture.py");
    fs::write(&file_path, "def add(x, y): return x + y\n").unwrap();
    fs::write(source_dir.join("notes.txt"), "leave me alone\n").unwrap();
    let ignored_dir = project
        .join("artifacts")
        .join("external_repos")
        .join("chalk");
    fs::create_dir_all(&ignored_dir).unwrap();
    fs::write(ignored_dir.join("README.md"), "ignored\n").unwrap();
    fs::write(project.join(".gitignore"), "artifacts/\n").unwrap();

    Command::new("git")
        .arg("init")
        .current_dir(project)
        .output()
        .unwrap();
    Command::new("git")
        .args(["config", "user.email", "tg@example.com"])
        .current_dir(project)
        .output()
        .unwrap();
    Command::new("git")
        .args(["config", "user.name", "tensor-grep"])
        .current_dir(project)
        .output()
        .unwrap();
    Command::new("git")
        .args(["add", "."])
        .current_dir(project)
        .output()
        .unwrap();
    Command::new("git")
        .args(["commit", "-m", "init"])
        .current_dir(project)
        .output()
        .unwrap();

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
    let checkpoint = parsed["checkpoint"]
        .as_object()
        .expect("checkpoint metadata must be present");
    let checkpoint_id = checkpoint["checkpoint_id"]
        .as_str()
        .expect("checkpoint id must be a string");
    assert_eq!(checkpoint["file_count"], 1);
    assert_eq!(checkpoint["mode"], "filesystem-snapshot");
    assert_eq!(checkpoint["root"], canonical_display_path(&source_dir));

    let metadata_path = source_dir
        .join(".tensor-grep")
        .join("checkpoints")
        .join(checkpoint_id)
        .join("metadata.json");
    let metadata: Value = serde_json::from_slice(&fs::read(&metadata_path).unwrap()).unwrap();
    assert_eq!(metadata["scope"], "file");
    assert_eq!(metadata["entries"].as_object().unwrap().len(), 1);
    assert!(metadata["entries"].get("fixture.py").is_some());
}
