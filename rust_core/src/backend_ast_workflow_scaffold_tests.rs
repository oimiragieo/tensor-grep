use super::*;
use tempfile::tempdir;

#[test]
fn new_rule_and_test_scaffolds_match_python_front_door_contract() {
    let dir = tempdir().unwrap();
    handle_ast_new(vec![
        "rule".to_string(),
        "no-eval2".to_string(),
        "--lang".to_string(),
        "python".to_string(),
        "--base-dir".to_string(),
        dir.path().to_string_lossy().into_owned(),
        "--yes".to_string(),
    ])
    .unwrap();
    handle_ast_new(vec![
        "test".to_string(),
        "no-eval2".to_string(),
        "--base-dir".to_string(),
        dir.path().to_string_lossy().into_owned(),
        "--yes".to_string(),
    ])
    .unwrap();

    assert_eq!(
        fs::read(dir.path().join("rules/no-eval2.yml")).unwrap(),
        b"id: \"no-eval2\"\nlanguage: python\nrule:\n  pattern: \"TODO_REPLACE_ME($$$ARGS)\"\n"
    );
    assert_eq!(
        fs::read(dir.path().join("tests/no-eval2.yml")).unwrap(),
        b"id: \"no-eval2\"\nruleId: \"no-eval2\"\nvalid:\n  - \"other(1)\"\ninvalid:\n  - \"TODO_REPLACE_ME(1)\"\n"
    );
}

#[test]
fn test_reports_linked_rule_with_empty_pattern() {
    let dir = tempdir().unwrap();
    let config_path = dir.path().join("sgconfig.yml");
    fs::write(
        &config_path,
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n",
    )
    .unwrap();
    fs::create_dir(dir.path().join("rules")).unwrap();
    fs::write(
        dir.path().join("rules/empty.yml"),
        "id: empty-rule\nlanguage: python\nrule:\n  pattern: ''\n",
    )
    .unwrap();
    fs::create_dir(dir.path().join("tests")).unwrap();
    fs::write(
        dir.path().join("tests/empty.yml"),
        "id: empty-test\nruleId: empty-rule\nvalid: [other(1)]\ninvalid: [TODO_REPLACE_ME(1)]\n",
    )
    .unwrap();

    let orchestrator = AstWorkflowOrchestrator::new(config_path.to_str()).unwrap();
    let data = orchestrator.load_project_data().unwrap();
    let mut output = Vec::new();
    let success =
        execute_ast_test_core(&orchestrator, &data, &AstBackend::new(), &mut output).unwrap();

    assert!(!success);
    let output = String::from_utf8(output).unwrap();
    assert!(
        output.contains("empty.yml:empty-test: rule empty-rule has an empty pattern"),
        "unexpected test output: {output}"
    );
    assert!(!output.contains("missing pattern or ruleId"));
}
