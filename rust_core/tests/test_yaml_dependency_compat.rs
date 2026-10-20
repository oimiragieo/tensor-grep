use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use tempfile::TempDir;

fn write_fixture(root: &Path, relative: &str, contents: &str) -> PathBuf {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, contents).unwrap();
    path
}

fn new_project() -> TempDir {
    tempfile::tempdir().unwrap()
}

fn run_native_scan(root: &Path, config: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_tg"))
        .current_dir(root)
        .arg("scan")
        .arg("--config")
        .arg(config)
        .output()
        .unwrap()
}

#[test]
fn native_scan_preserves_yaml_sequence_merge_precedence_and_explicit_override() {
    let project = new_project();
    let config = write_fixture(
        project.path(),
        "sgconfig.yml",
        r#"defaults: &defaults
  ruleDirs: [missing-from-first]
  testDirs: [tests]
  language: python
secondary: &secondary
  ruleDirs: [missing-from-second]
  language: javascript
<<: [*defaults, *secondary]
ruleDirs: [rules]
"#,
    );
    write_fixture(
        project.path(),
        "rules/config-merge.yml",
        "id: config-merge\npattern: 'needle($A)'\n",
    );
    write_fixture(project.path(), "src/sample.py", "result = needle(\"ok\")\n");

    let output = run_native_scan(project.path(), &config);
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(
        output.status.success(),
        "stdout={stdout}\nstderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(stdout.contains("[scan] rule=config-merge lang=python matches=1 files=1"));
}

#[test]
fn native_scan_preserves_single_merge_composite_rules_and_unicode_metadata() {
    let project = new_project();
    let config = write_fixture(
        project.path(),
        "sgconfig.yml",
        "ruleDirs: [rules]\ntestDirs: [tests]\nlanguage: python\n",
    );
    write_fixture(
        project.path(),
        "rules/composite.yml",
        r#"rule_defaults: &rule_defaults
  id: merged-rule
  language: python
  severity: true
  message: "naïve 🚀"
  rule:
    any:
      - pattern: alpha($A)
      - pattern: beta($A)
<<: *rule_defaults
"#,
    );
    write_fixture(project.path(), "src/sample.py", "alpha(1)\nbeta(2)\n");

    let output = run_native_scan(project.path(), &config);
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(
        output.status.success(),
        "stdout={stdout}\nstderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(stdout.contains("[scan] rule=merged-rule lang=python matches=2 files=1"));
    let cache = fs::read(project.path().join(".tg_cache/ast/project_data_v6.json")).unwrap();
    let cache: serde_json::Value = serde_json::from_slice(&cache).unwrap();
    assert_eq!(cache["rule_specs"][0]["severity"], "True");
    assert_eq!(cache["rule_specs"][0]["message"], "naïve 🚀");
}

#[test]
fn native_scan_refuses_duplicate_config_keys_without_writing_source() {
    let project = new_project();
    let config = write_fixture(
        project.path(),
        "sgconfig.yml",
        "ruleDirs: [missing]\nruleDirs: [rules]\nlanguage: python\n",
    );
    write_fixture(
        project.path(),
        "rules/single.yml",
        "id: single\npattern: 'needle($A)'\n",
    );
    let source = write_fixture(project.path(), "src/sample.py", "sentinel = needle(1)\n");

    let output = run_native_scan(project.path(), &config);

    assert!(
        !output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stderr).contains("Failed to parse sgconfig.yml"));
    assert_eq!(
        fs::read_to_string(source).unwrap(),
        "sentinel = needle(1)\n"
    );
}

#[test]
fn native_scan_refuses_malformed_config_yaml_without_writing_source_or_cache() {
    let project = new_project();
    let config = write_fixture(project.path(), "sgconfig.yml", "ruleDirs: [rules\n");
    write_fixture(
        project.path(),
        "rules/single.yml",
        "id: single\npattern: 'alpha($A)'\n",
    );
    let source = write_fixture(project.path(), "src/sample.py", "sentinel = alpha(1)\n");

    let output = run_native_scan(project.path(), &config);

    assert!(
        !output.status.success(),
        "stdout={}\nstderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stderr).contains("Failed to parse sgconfig.yml"));
    assert_eq!(fs::read_to_string(source).unwrap(), "sentinel = alpha(1)\n");
    assert!(!project.path().join(".tg_cache").exists());
}
