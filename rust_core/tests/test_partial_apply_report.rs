//! J-04 (bug hunt 2026-10-03): a mid-batch `--apply` failure must name the files an EARLIER
//! language group already wrote, instead of surfacing only the failing file's error.

use std::fs;

use tempfile::tempdir;
use tensor_grep_rs::backend_ast::{AstBackend, BatchRewriteRule};

#[test]
fn test_batch_apply_failure_in_later_language_group_names_earlier_group_writes() {
    let dir = tempdir().unwrap();
    let py = dir.path().join("first.py");
    let rs = dir.path().join("second.rs");
    fs::write(&py, "x = 1\n").unwrap();
    fs::write(&rs, "fn main() { let a = 1; }\n").unwrap();

    let mut perms = fs::metadata(&rs).unwrap().permissions();
    perms.set_readonly(true);
    fs::set_permissions(&rs, perms).unwrap();
    // Root (or any principal that bypasses read-only) cannot provoke the failure: skip then.
    let probe = fs::OpenOptions::new().write(true).open(&rs);
    if probe.is_ok() {
        let mut perms = fs::metadata(&rs).unwrap().permissions();
        perms.set_readonly(false);
        fs::set_permissions(&rs, perms).unwrap();
        eprintln!("skipping: read-only file is writable for this principal");
        return;
    }

    let rewrites = vec![
        BatchRewriteRule {
            pattern: "x = $E".to_string(),
            replacement: "y = $E".to_string(),
            lang: "python".to_string(),
        },
        BatchRewriteRule {
            pattern: "let a = $E;".to_string(),
            replacement: "let a = 0;".to_string(),
            lang: "rust".to_string(),
        },
    ];
    let result = AstBackend::new().plan_and_apply_batch(&rewrites, dir.path().to_str().unwrap());

    let mut perms = fs::metadata(&rs).unwrap().permissions();
    perms.set_readonly(false);
    fs::set_permissions(&rs, perms).unwrap();

    let err = result.expect_err("the read-only Rust file must fail the apply");
    let msg = format!("{err:#}");
    assert_eq!(
        fs::read_to_string(&py).unwrap(),
        "y = 1\n",
        "python group wrote first"
    );
    assert!(
        msg.contains("first.py") && msg.contains("second.rs") && msg.contains("1 file(s) written"),
        "the failure report must name the already-written Python file and the failed Rust file: {msg}"
    );
}
