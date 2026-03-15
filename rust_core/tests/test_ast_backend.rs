#![cfg(windows)]

use std::fs;
use std::ops::Range;
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

fn assert_single_match(
    pattern: &str,
    lang: &str,
    extension: &str,
    source: &str,
    expected_text: &str,
    expected_range: Range<usize>,
) {
    let (_dir, file_path) = write_source_file(extension, source);
    let backend = AstBackend::new();

    let matches = backend
        .search(pattern, lang, file_path.to_str().unwrap())
        .unwrap();

    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].file, file_path);
    assert_eq!(matches[0].line, 1);
    assert_eq!(matches[0].matched_text, expected_text);
    assert_eq!(matches[0].candidate.file, file_path);
    assert_eq!(matches[0].candidate.byte_range, expected_range);
    assert!(!matches[0].candidate.metavar_env.is_empty());
}

#[test]
fn test_ast_backend_matches_python_reference_snippet() {
    let source = "def add(a, b):\n    return a + b\n";
    assert_single_match(
        "def $F($$$ARGS): $$$BODY",
        "python",
        "py",
        source,
        "def add(a, b):\n    return a + b",
        0..31,
    );
}

#[test]
fn test_ast_backend_matches_javascript_reference_snippet() {
    let source = "const fn = (x) => x * 2;\n";
    assert_single_match(
        "const $F = ($X) => $BODY",
        "javascript",
        "js",
        source,
        "const fn = (x) => x * 2;",
        0..24,
    );
}

#[test]
fn test_ast_backend_matches_typescript_reference_snippet() {
    let source = "function greet(name: string): string { return name; }\n";
    assert_single_match(
        "function $F($$$): $T { $$$BODY }",
        "typescript",
        "ts",
        source,
        "function greet(name: string): string { return name; }",
        0..53,
    );
}

#[test]
fn test_ast_backend_matches_rust_reference_snippet() {
    let source = "fn main() { println!(\"hi\"); }\n";
    assert_single_match(
        "fn $F() { $$$BODY }",
        "rust",
        "rs",
        source,
        "fn main() { println!(\"hi\"); }",
        0..29,
    );
}

#[test]
fn test_ast_backend_reports_line_numbers_for_multiple_python_matches() {
    let source = "def first(a): return a\n\n\ndef second(b): return b\n";
    let (_dir, file_path) = write_source_file("py", source);
    let backend = AstBackend::new();

    let matches = backend
        .search("def $F($$$ARGS): return $EXPR", "python", file_path.to_str().unwrap())
        .unwrap();

    assert_eq!(matches.len(), 2);
    assert_eq!(matches[0].line, 1);
    assert_eq!(matches[1].line, 4);
}
#[test]
fn test_tg_run_json_metadata_uses_ast_backend_routing() {
    let (_dir, file_path) = write_source_file("py", "def add(a, b):\n    return a + b\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--json")
        .arg("def $F($$$ARGS): $$$BODY")
        .arg(&file_path)
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
    assert_eq!(payload["routing_backend"], "AstBackend");
    assert_eq!(payload["routing_reason"], "ast-native");
    assert_eq!(payload["sidecar_used"], false);
    assert_eq!(payload["total_matches"], 1);
}

#[test]
fn test_tg_run_verbose_emits_ast_routing_metadata_and_match_output() {
    let (_dir, file_path) = write_source_file("py", "def add(a, b):\n    return a + b\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("--verbose")
        .arg("def $F($$$ARGS): $$$BODY")
        .arg(&file_path)
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
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("routing_backend=AstBackend"), "stderr={stderr}");
    assert!(stderr.contains("routing_reason=ast-native"), "stderr={stderr}");
    assert!(stderr.contains("sidecar_used=false"), "stderr={stderr}");
    assert!(
        stdout.starts_with(&format!("{}:1:def add(a, b):", file_path.display())),
        "stdout={stdout}"
    );
    assert!(stdout.contains("return a + b"), "stdout={stdout}");
}

#[test]
fn test_tg_run_succeeds_without_python_sidecar() {
    let (_dir, file_path) = write_source_file("py", "def add(a, b):\n    return a + b\n");
    let bogus_python_home = tempdir().unwrap();
    let bogus_sidecar = bogus_python_home.path().join("missing-python.exe");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("def $F($$$ARGS): $$$BODY")
        .arg(&file_path)
        .env("PYTHONHOME", bogus_python_home.path())
        .env("TG_SIDECAR_PYTHON", &bogus_sidecar)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_tg_run_reports_invalid_pattern_without_panic() {
    let (_dir, file_path) = write_source_file("py", "def add(a, b):\n    return a + b\n");

    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .arg("run")
        .arg("--lang")
        .arg("python")
        .arg("def $F(")
        .arg(&file_path)
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(output.stdout.is_empty(), "stdout={}", String::from_utf8_lossy(&output.stdout));

    let stderr = String::from_utf8_lossy(&output.stderr).to_lowercase();
    assert!(stderr.contains("invalid pattern") || stderr.contains("parse"), "stderr={stderr}");
    assert!(!stderr.contains("panicked"), "stderr={stderr}");
}
