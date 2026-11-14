use super::*;

#[test]
fn clean_prefix_preserves_text_when_binary_matcher_compilation_failed() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("sample.txt");
    let mut bytes = vec![b'x'; 65536];
    bytes.extend_from_slice(b"\nneedle\0payload\n");
    std::fs::write(&path, bytes).unwrap();
    let matcher = SearchMatcher::new("needle", false, true).unwrap();
    let binary_matcher: Result<RegexMatcher> = Err(anyhow::anyhow!("forced compile error"));
    let (matches, binary) = collect_matches(&path, None, &matcher, &binary_matcher).unwrap();
    assert_eq!(matches.len(), 1);
    assert_eq!(matches[0].line, 2);
    assert_eq!(matches[0].text, "needle\0payload");
    assert!(binary.is_none());
}

#[test]
fn prefix_nul_propagates_binary_matcher_compilation_failure() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("sample.txt");
    std::fs::write(&path, b"needle\0payload\n").unwrap();
    let matcher = SearchMatcher::new("needle", false, true).unwrap();
    let binary_matcher: Result<RegexMatcher> = Err(anyhow::anyhow!("forced compile error"));
    let error = collect_matches(&path, None, &matcher, &binary_matcher).unwrap_err();
    assert!(error
        .to_string()
        .contains("failed to compile binary search pattern"));
    assert!(error.to_string().contains("forced compile error"));
}
