#![cfg(windows)]

use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, UNIX_EPOCH};

use serde_json::Value;
use sha2::{Digest, Sha256};
use tempfile::{tempdir, TempDir};
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewriteRule};

fn write_source_file(extension: &str, content: &str) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

fn write_source_bytes_file(extension: &str, content: &[u8]) -> (TempDir, PathBuf) {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join(format!("fixture.{extension}"));
    fs::write(&file_path, content).unwrap();
    (dir, file_path)
}

fn create_sparse_file(path: &std::path::Path, len: u64) {
    let file = std::fs::File::create(path).unwrap();
    file.set_len(len).unwrap();
}

fn write_batch_config(dir: &std::path::Path, payload: &Value) -> PathBuf {
    let config_path = dir.join("batch-rewrite.json");
    fs::write(&config_path, serde_json::to_vec_pretty(payload).unwrap()).unwrap();
    config_path
}

fn file_mtime_ns(path: &std::path::Path) -> u64 {
    path.metadata()
        .unwrap()
        .modified()
        .unwrap()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64
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
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--rewrite")
        .arg("lambda $$$ARGS: $EXPR")
        .arg("def $F($$$ARGS): return $EXPR")
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

#[test]
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
        .arg("echo lint-ok")
        .arg("--test-cmd")
        .arg("echo test-ok")
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
        .arg("echo lint-ok")
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
    let mut second_hasher = Sha256::new();
    second_hasher.update(serde_json::to_vec_pretty(&second_canonical).unwrap());
    assert_eq!(second_digest, format!("{:x}", second_hasher.finalize()));
    assert_eq!(
        second_manifest["previous_manifest_sha256"],
        Value::String(manifest_digest)
    );
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
        .arg("echo lint-fail 1>&2 && exit /b 3")
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
    assert_eq!(
        fs::read_to_string(&file_path).unwrap(),
        "lambda x, y: x + y\n"
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
