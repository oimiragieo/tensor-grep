use std::process::Command;

#[test]
fn citations_search_routes_to_python_instead_of_ripgrep() {
    let output = Command::new(env!("CARGO_BIN_EXE_tg"))
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .args(["search", "X", "Cargo.toml", "--citations", "--format", "rg"])
        .output()
        .expect("tg search must run");

    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8(output.stderr)
        .expect("stderr must be UTF-8")
        .contains("--citations cannot be combined with --ndjson or --format rg"));
}
