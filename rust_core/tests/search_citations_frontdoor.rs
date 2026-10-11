use std::ffi::OsString;

#[test]
fn citations_search_routes_to_python_instead_of_ripgrep() {
    let raw_args = ["tg", "search", "--citations", "X", "."]
        .iter()
        .map(OsString::from)
        .collect::<Vec<_>>();

    assert_eq!(
        search_format_python_passthrough_args(&raw_args),
        Some(vec![
            "--citations".to_string(),
            "X".to_string(),
            ".".to_string()
        ])
    );
}
