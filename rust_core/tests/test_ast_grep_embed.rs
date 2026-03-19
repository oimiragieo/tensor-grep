use std::fs;

use ast_grep_core::AstGrep;
use ast_grep_language::SupportLang;
use tempfile::tempdir;

#[test]
fn test_ast_grep_python_root_node_parses_simple_file() {
    let dir = tempdir().unwrap();
    let file_path = dir.path().join("sample.py");
    fs::write(&file_path, "def add(a, b):\n    return a + b\n").unwrap();

    let source = fs::read_to_string(&file_path).unwrap();
    let ast = AstGrep::new(source, SupportLang::Python);
    let root = ast.root();

    assert!(root.is_named());
    assert_eq!(root.kind(), "module");
}
