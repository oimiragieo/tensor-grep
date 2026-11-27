#![allow(unused_imports, dead_code)]
use serde_json::Value;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use tempfile::tempdir;
#[path = "common/public_native_cli_parity_helpers.rs"]
mod common;
use common::*;

#[test]
fn test_public_native_root_help_describes_python_passthrough_commands() {
    let output = tg().arg("--help-all").output().unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let expected: &[(&str, &[&str])] = &[
        (
            "map",
            &[
                "Build a bounded repository map",
                "Return a deterministic repository map",
            ],
        ),
        (
            "session",
            &[
                "Open, query, and manage cached",
                "Open and reuse cached repository-map sessions",
            ],
        ),
        (
            "doctor",
            &["Diagnose launcher, GPU, cache", "Print system, GPU, cache"],
        ),
        ("checkpoint", &["Create, list, and undo"]),
        (
            "edit-plan",
            &[
                "Build a machine-readable edit plan",
                "Return a machine-readable edit-planning",
            ],
        ),
        (
            "agent",
            &[
                "Emit an actionable context capsule",
                "Return an actionable context capsule",
            ],
        ),
        (
            "context-render",
            &[
                "Render bounded prompt-ready context",
                "Return a prompt-ready repository context",
            ],
        ),
        (
            "defs",
            &[
                "Find symbol definitions",
                "Return exact definition locations",
            ],
        ),
        (
            "refs",
            &[
                "Find symbol references",
                "Return Python-first symbol references",
            ],
        ),
        (
            "callers",
            &["Find direct callers", "Return Python-first call sites"],
        ),
        (
            "blast-radius",
            &[
                "Build a transitive blast-radius graph",
                "Return exact callers plus",
            ],
        ),
    ];
    for (command, descriptions) in expected {
        assert!(
            stdout.contains(command)
                && descriptions
                    .iter()
                    .any(|description| stdout.contains(description)),
            "missing help for {command}: {stdout}"
        );
    }
}

#[test]
fn test_top_level_structured_search_accepts_no_ignore() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("ignored.log");
    fs::write(dir.path().join(".gitignore"), "*.log\n").unwrap();
    fs::write(&file, "ERROR hidden by ignore rules\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .arg("--json")
        .arg("--no-ignore")
        .arg("ERROR")
        .arg(&file)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["total_files"], 1);
    assert_eq!(payload["matched_file_paths"].as_array().unwrap().len(), 1);
    assert_eq!(
        payload["match_counts_by_file"].as_object().unwrap().len(),
        1
    );
}

#[test]
fn test_top_level_format_rg_search_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "app.log:ERROR failed\n");
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    for args in [
        vec!["--format", "rg", "ERROR", "."],
        vec!["--format=rg", "ERROR", "."],
    ] {
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("app.log:ERROR failed"),
            "args={args:?} stdout={}",
            String::from_utf8_lossy(&output.stdout)
        );
        assert!(
            !String::from_utf8_lossy(&output.stderr).contains("unexpected argument"),
            "args={args:?} stderr={}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn test_format_rg_no_explicit_path_omits_dot_for_files_with_matches() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(
        dir.path(),
        &["-l", "-g", "AGENTS.md", "-e", "tensor-grep"],
        "AGENTS.md\n",
    );
    fs::write(dir.path().join("AGENTS.md"), "tensor-grep\n").unwrap();

    for args in [
        vec![
            "search",
            "--format",
            "rg",
            "-l",
            "-g",
            "AGENTS.md",
            "tensor-grep",
        ],
        vec!["--format", "rg", "-l", "-g", "AGENTS.md", "tensor-grep"],
    ] {
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
            "AGENTS.md\n"
        );
    }
}

#[test]
fn test_format_rg_explicit_dot_path_is_forwarded_for_files_with_matches() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_exact_args_script(
        dir.path(),
        &["-l", "-g", "AGENTS.md", "-e", "tensor-grep", "--", "."],
        "AGENTS.md\n",
    );
    fs::write(dir.path().join("AGENTS.md"), "tensor-grep\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args([
            "search",
            "--format",
            "rg",
            "-l",
            "-g",
            "AGENTS.md",
            "tensor-grep",
            ".",
        ])
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
        "AGENTS.md\n"
    );
}

#[test]
fn test_native_json_context_counts_only_real_matches() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("sample.txt"), "before\nneedle\nafter\n").unwrap();

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--json", "-C", "1", "needle", "sample.txt"])
        .env("TG_DISABLE_RG", "1")
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
    assert_eq!(payload["total_matches"], 1);
    assert_eq!(payload["total_files"], 1);
    assert_eq!(payload["matched_file_paths"].as_array().unwrap().len(), 1);
    assert_eq!(
        payload["match_counts_by_file"].as_object().unwrap().len(),
        1
    );
}

#[test]
fn test_format_rg_column_no_column_forwards_to_rg_with_no_column_last() {
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("app.log"), "ERROR failed\nINFO ok\n").unwrap();

    for args in [
        vec![
            "search",
            "--format",
            "rg",
            "--column",
            "--no-column",
            "-n",
            "-F",
            "ERROR",
            "app.log",
        ],
        vec![
            "--format",
            "rg",
            "--column",
            "--no-column",
            "-n",
            "-F",
            "ERROR",
            "app.log",
        ],
    ] {
        let fake_rg = fake_rg_exact_args_script(
            dir.path(),
            &["--no-column", "-F", "-n", "-e", "ERROR", "--", "app.log"],
            "app.log:1:ERROR failed\n",
        );
        let output = tg()
            .current_dir(dir.path())
            .args(&args)
            .env("TG_RG_PATH", &fake_rg)
            .output()
            .unwrap();

        assert!(
            output.status.success(),
            "args={args:?} status={:?}\nstdout={}\nstderr={}",
            output.status.code(),
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(
            String::from_utf8_lossy(&output.stdout).replace("\r\n", "\n"),
            "app.log:1:ERROR failed\n"
        );
    }
}

#[test]
fn test_top_level_type_list_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_rg = fake_rg_script(dir.path(), "rust: *.rs\n");

    let output = tg()
        .current_dir(dir.path())
        .arg("--type-list")
        .env("TG_RG_PATH", &fake_rg)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("rust:"));
}

#[test]
fn test_new_rule_respects_base_dir_and_does_not_scaffold_cwd_root() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("ast-project");

    let output = run_tg(
        &[
            "new",
            "rule",
            "demo",
            "--lang",
            "python",
            "--yes",
            "--base-dir",
            project.to_str().unwrap(),
        ],
        dir.path(),
    );

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !dir.path().join("sgconfig.yml").exists(),
        "tg new rule must not ignore --base-dir and scaffold the current directory"
    );
    assert!(project.join("rules").join("demo.yml").exists());
    assert!(
        !project.join("rules").join("sample-rule.yml").exists(),
        "tg new rule should create the requested rule, not the project sample rule"
    );
}

#[test]
fn test_ast_compatibility_flags_route_or_fail_explicitly_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(dir.path(), "python-route-ok");
    let rule_path = dir.path().join("rule.yml");
    fs::write(
        &rule_path,
        "id: no-print\nlanguage: python\nrule:\n  pattern: print($A)\n",
    )
    .unwrap();

    let scan_output = tg()
        .current_dir(dir.path())
        .args(["scan", "--rule", rule_path.to_str().unwrap(), ".", "--json"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        scan_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        scan_output.status.code(),
        String::from_utf8_lossy(&scan_output.stdout),
        String::from_utf8_lossy(&scan_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&scan_output.stdout).contains("python-route-ok"),
        "stdout={}",
        String::from_utf8_lossy(&scan_output.stdout)
    );

    let new_output = tg()
        .current_dir(dir.path())
        .args(["new", "rule", "demo", "--config", "sgconfig.yml", "--yes"])
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();
    assert!(
        new_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        new_output.status.code(),
        String::from_utf8_lossy(&new_output.stdout),
        String::from_utf8_lossy(&new_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&new_output.stdout).contains("python-route-ok"),
        "stdout={}",
        String::from_utf8_lossy(&new_output.stdout)
    );

    let run_python = fake_python_passthrough_asserting_args_script(
        dir.path(),
        &[
            "-m",
            "tensor_grep",
            "run",
            "--pattern",
            "print($A)",
            "--selector",
            "call_expression",
            "--strictness",
            "relaxed",
            "--globs",
            "*.py",
            ".",
        ],
        "python-route-ok",
    );
    let selector_output = tg()
        .current_dir(dir.path())
        .args([
            "run",
            "--pattern",
            "print($A)",
            "--selector",
            "call_expression",
            "--strictness",
            "relaxed",
            "--globs",
            "*.py",
            ".",
        ])
        .env("TG_SIDECAR_PYTHON", &run_python)
        .output()
        .unwrap();
    assert!(selector_output.status.success());
    assert!(
        String::from_utf8_lossy(&selector_output.stdout).contains("python-route-ok"),
        "stdout={}",
        String::from_utf8_lossy(&selector_output.stdout)
    );

    let stdin_python = fake_python_passthrough_asserting_stdin_script(
        dir.path(),
        &[
            "-m",
            "tensor_grep",
            "run",
            "--lang",
            "python",
            "--pattern",
            "print($A)",
            "--stdin",
        ],
        "print('hello')\n",
        "stdin-forward-ok",
    );
    let mut stdin_command = tg();
    stdin_command
        .current_dir(dir.path())
        .args(["run", "--pattern", "print($A)", "--stdin"])
        .env("TG_SIDECAR_PYTHON", &stdin_python);
    let stdin_output = run_command_with_stdin(stdin_command, b"print('hello')\n");
    assert!(
        stdin_output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        stdin_output.status.code(),
        String::from_utf8_lossy(&stdin_output.stdout),
        String::from_utf8_lossy(&stdin_output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&stdin_output.stdout).contains("stdin-forward-ok"),
        "stdout={}",
        String::from_utf8_lossy(&stdin_output.stdout)
    );
}

#[test]
fn test_new_rejects_unsupported_shapes_without_scaffolding_cwd_root() {
    let dir = tempdir().unwrap();

    let output = run_tg(&["new", "rule", "demo", "--unsupported-option"], dir.path());

    assert!(
        !output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("Unsupported tg new option"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !dir.path().join("sgconfig.yml").exists(),
        "unsupported tg new shapes must not scaffold the current directory"
    );
    assert!(!dir.path().join("rules").exists());
    assert!(!dir.path().join("tests").exists());
}

#[test]
fn test_new_project_name_scaffolds_named_directory_without_scaffolding_cwd_root() {
    let dir = tempdir().unwrap();
    let output = run_tg(&["new", "project", "demo"], dir.path());

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!dir.path().join("sgconfig.yml").exists());
    assert!(dir.path().join("demo").join("sgconfig.yml").exists());
    assert!(dir
        .path()
        .join("demo")
        .join("rules")
        .join("sample-rule.yml")
        .exists());
    assert!(dir
        .path()
        .join("demo")
        .join("tests")
        .join("sample-test.yml")
        .exists());
}

#[test]
fn test_run_short_rewrite_alias_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("sample.py");
    fs::write(&file, "print('hello')\n").unwrap();

    let output = run_tg(
        &[
            "run",
            "--lang",
            "python",
            "-r",
            "print('bye')",
            "print('hello')",
            file.to_str().unwrap(),
        ],
        &repo_root(),
    );

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn test_classify_format_json_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("sample.log");
    let fake_sidecar = fake_sidecar_script(dir.path());
    fs::write(&file, "INFO ok\nERROR database failed\nWARN retrying\n").unwrap();

    let output = tg()
        .current_dir(repo_root())
        .arg("classify")
        .arg("--format")
        .arg("json")
        .arg(&file)
        .env("TG_SIDECAR_PYTHON", &fake_sidecar)
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
    assert_eq!(payload["classifications"].as_array().unwrap().len(), 3);
}

#[test]
fn test_classify_help_describes_local_default_provider_contract() {
    let output = tg()
        .current_dir(repo_root())
        .args(["classify", "--help"])
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
    assert!(
        stdout.contains("local heuristics") && stdout.contains("CyBERT/Triton is opt-in"),
        "classify help should describe local default provider contract: {stdout}"
    );
    assert!(
        !stdout.contains("via cyBERT"),
        "classify help should not imply CyBERT is the default provider: {stdout}"
    );
}

#[test]
fn test_classify_reports_clear_error_for_literal_input_on_public_native_frontdoor() {
    // #92: a bare positional that isn't a real file path must still fail -- literal
    // classification is only supported via the explicit --text flag now, not by accident.
    let output = tg()
        .current_dir(repo_root())
        .args([
            "classify",
            "--format",
            "json",
            "2026-05-26 ERROR payment retry failed",
        ])
        .output()
        .unwrap();

    assert!(
        !output.status.success(),
        "a non-existent positional path should still fail without --text/--stdin"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("classify expects a file path")
            && stderr.contains("use --text for a literal string or --stdin to read from stdin"),
        "classify literal error should point at the new --text/--stdin flags: {stderr}"
    );
}

#[test]
fn test_classify_help_lists_stdin_and_text_flags_on_public_native_frontdoor() {
    let output = tg()
        .current_dir(repo_root())
        .args(["classify", "--help"])
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
    assert!(
        stdout.contains("--stdin"),
        "classify --help missing --stdin: {stdout}"
    );
    assert!(
        stdout.contains("--text"),
        "classify --help missing --text: {stdout}"
    );
}

#[test]
fn test_classify_stdin_and_text_together_is_a_clean_error_on_public_native_frontdoor() {
    // Validation must happen BEFORE any stdin read, so this can never hang even though we
    // pass an empty (immediately-closed) stdin pipe as a hang-safety belt-and-suspenders.
    let mut command = tg();
    command.current_dir(repo_root()).args([
        "classify",
        "--stdin",
        "--text",
        "2026-05-26 ERROR payment retry failed",
    ]);
    let output = run_command_with_stdin(command, b"");

    assert!(
        !output.status.success(),
        "tg classify --stdin --text should be a clean, immediate error"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("--stdin") && stderr.contains("--text"),
        "mutual-exclusion error should name both flags: {stderr}"
    );
}

#[test]
fn test_classify_text_and_file_path_together_is_a_clean_error_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let file = dir.path().join("sample.log");
    fs::write(&file, "INFO ok\n").unwrap();

    let mut command = tg();
    command
        .current_dir(repo_root())
        .arg("classify")
        .arg("--text")
        .arg("literal input")
        .arg(&file);
    let output = run_command_with_stdin(command, b"");

    assert!(
        !output.status.success(),
        "tg classify --text PATH should be a clean, immediate error"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("--text") && stderr.contains("file path"),
        "mutual-exclusion error should name the conflicting flag and file path: {stderr}"
    );
}

#[test]
fn test_agent_json_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("project");
    fs::create_dir(&project).unwrap();
    fs::write(
        project.join("payments.py"),
        "def create_invoice(total, tax):\n    return total + tax\n",
    )
    .unwrap();
    let fake_python = fake_python_passthrough_script(
        dir.path(),
        r#"{"routing_reason":"agent-context-capsule","capsule_kind":"actionable_context"}"#,
    );

    let output = tg()
        .current_dir(repo_root())
        .arg("agent")
        .arg("--query")
        .arg("change invoice tax calculation")
        .arg("--json")
        .arg(&project)
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unrecognized subcommand"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["routing_reason"], "agent-context-capsule");
}

#[test]
fn test_repair_launcher_is_accepted_on_public_native_frontdoor() {
    let dir = tempdir().unwrap();
    let fake_python = fake_python_passthrough_script(
        dir.path(),
        r#"{"status":"blocked_requires_allow_foreign_rename","message":"blocked"}"#,
    );

    let output = tg()
        .current_dir(repo_root())
        .arg("repair-launcher")
        .arg("--json")
        .env("TG_SIDECAR_PYTHON", &fake_python)
        .output()
        .unwrap();

    assert!(
        output.status.success(),
        "status={:?}\nstdout={}\nstderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("unrecognized subcommand"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(payload["status"], "blocked_requires_allow_foreign_rename");
}

// --- Task #264: rg-passthrough root-ignore-file self-inconsistency ---------------------------
//
// Bug: `tg search NEEDLE .` in a NON-git directory containing a root `.gitignore` disagreed
// with `tg search --json NEEDLE .` on which files were searched -- real `rg`'s own `.gitignore`
// auto-discovery requires `require_git=true` by default, so it is silently a no-op outside a
// git repo, while the native engine (which `--json` routes to) already closed that exact gap
// for itself via an explicit `add_ignore` trio (#127). `--json`'s narrower, CORRECT file set is
// the oracle here (per the recorded decision in `docs/BACKLOG.md:154` / `index.rs:968-975`): an
// output-format flag must never change which files are searched.
//
// Four-cell test matrix (2026-07 task #264), each cell asserting on the real compiled `tg`
// binary's ACTUAL constructed rg argv via `fake_rg_asserting_ignore_file_values_script`:
//   1. non-git dir, plain text  -- PRE-FIX: FAILS -- `root_ignore_file_args` did not exist, so
//      no `--ignore-file` was ever emitted here (the exact #264 bug repro). POST-FIX (this PR):
//      `test_search_plain_text_adds_root_ignore_file_outside_git_repo` below passes --
//      `--ignore-file <root>/.gitignore` is present. The sibling
//      `test_search_plain_text_root_ignore_file_omitted_with_no_ignore_flag` pins the one
//      legitimate escape hatch (`--no-ignore`) that must still omit it.
//   2. non-git dir, --json      -- was already correct pre-fix (native engine's own add_ignore
//      trio, #127); untouched by this PR (json/ndjson never reach rg_passthrough for a bare
//      search, so there is no `--ignore-file` argv to assert on this cell in this file). The
//      tests here (and the unit tests in `rg_passthrough.rs`) assert constructed argv only,
//      never an actual search outcome; `tests/e2e/test_search_root_ignore_file_outcome.py`
//      (Python, real compiled `tg` + real `rg`) is the one place that asserts the real matched
//      file set for both cells 1 and 2 in one test. It skips when `rg` is not on PATH, AND when
//      no compiled native `tg` binary is discoverable -- `python -m tensor_grep search` without
//      one falls back to a separate, still-unfixed Python rg-passthrough
//      (`bootstrap.py:1088`) that does not exercise this fix; see that test file's docstring.
//   3. git repo, plain text     -- already agreed pre-fix (real rg auto-discovers root
//      .gitignore inside a git repo); this PR adds a REDUNDANT `--ignore-file` there too
//      (verified live against rg 15.1.0 to produce byte-identical results, see rg_passthrough.rs
//      doc comment on `root_ignore_file_args`) -- asserted not to regress by
//      `test_search_root_ignore_file_added_inside_git_repo_is_still_redundant_safe` below.
//   4. git repo, --json         -- untouched (native engine, unaffected by this PR).

#[test]
fn test_search_plain_text_adds_root_ignore_file_outside_git_repo() {
    // THE FIX: a non-git directory with a root `.gitignore` must surface it to real `rg` via
    // `--ignore-file`, since rg's own auto-discovery of `.gitignore` requires a detected git
    // repo. Pre-fix, `root_ignore_file_args` did not exist and this flag was never emitted --
    // this is the exact repro from task #264's bug report.
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.txt"), "needle\n").unwrap();
    fs::write(dir.path().join("skipme.txt"), "needle\n").unwrap();
    fs::write(dir.path().join(".gitignore"), "skipme.txt\n").unwrap();
    let fake_rg = fake_rg_asserting_ignore_file_values_script(
        dir.path(),
        &[".gitignore"],
        &[],
        "a.txt:needle\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "needle", "."])
        .env("TG_RG_PATH", &fake_rg)
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
fn test_search_plain_text_root_ignore_file_omitted_with_no_ignore_flag() {
    // Regression control: `--no-ignore` must fully suppress the new `--ignore-file` emission,
    // not just leave it to real rg to cancel (rg's OWN `--no-ignore` does NOT cancel an explicit
    // `--ignore-file` -- verified live against rg 15.1.0; if this test were removed/inverted, a
    // user's explicit `--no-ignore` would be silently overridden by our own injected flag).
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.txt"), "needle\n").unwrap();
    fs::write(dir.path().join("skipme.txt"), "needle\n").unwrap();
    fs::write(dir.path().join(".gitignore"), "skipme.txt\n").unwrap();
    let fake_rg = fake_rg_asserting_ignore_file_values_script(
        dir.path(),
        &[],
        &[".gitignore"],
        "a.txt:needle\nskipme.txt:needle\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--no-ignore", "needle", "."])
        .env("TG_RG_PATH", &fake_rg)
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
fn test_search_plain_text_no_ignore_vcs_skips_gitignore_but_keeps_dot_ignore() {
    // Real rg's own docs restrict `--no-ignore-vcs` to source-control ignore files ("only
    // respect rules in .ignore or .rgignore") -- an explicit `--ignore-file` for `.gitignore`
    // must be withheld, but `.ignore` (non-VCS) must still be surfaced.
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".gitignore"), "skipme.txt\n").unwrap();
    fs::write(dir.path().join(".ignore"), "skipme.txt\n").unwrap();
    let fake_rg = fake_rg_asserting_ignore_file_values_script(
        dir.path(),
        &[".ignore"],
        &[".gitignore"],
        "a.txt:needle\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--no-ignore-vcs", "needle", "."])
        .env("TG_RG_PATH", &fake_rg)
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
fn test_search_plain_text_no_ignore_dot_skips_dot_ignore_but_keeps_gitignore() {
    // Mirror of the above: real rg's own docs say `--no-ignore-dot` "does not impact whether
    // filter rules from .gitignore files are respected" -- `.gitignore` must still be surfaced.
    let dir = tempdir().unwrap();
    fs::write(dir.path().join(".gitignore"), "skipme.txt\n").unwrap();
    fs::write(dir.path().join(".ignore"), "skipme.txt\n").unwrap();
    let fake_rg = fake_rg_asserting_ignore_file_values_script(
        dir.path(),
        &[".gitignore"],
        &[".ignore"],
        "a.txt:needle\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "--no-ignore-dot", "needle", "."])
        .env("TG_RG_PATH", &fake_rg)
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
fn test_search_root_ignore_file_added_inside_git_repo_is_still_redundant_safe() {
    // Cell 3 of the matrix: inside a real git repo, real rg ALREADY auto-discovers the root
    // `.gitignore` on its own (git repo detected -> `require_git` satisfied), so this cell
    // agreed with `--json` even pre-fix. Post-fix, `root_ignore_file_args` does not special-case
    // git-repo-ness (verified live: an explicit `--ignore-file` pointed at the SAME already-
    // auto-discovered `.gitignore` produces byte-identical rg output), so it still emits
    // `--ignore-file` here too -- this test pins that the emission happens (no git-repo branch
    // was silently added) and that the command still succeeds end-to-end.
    let dir = tempdir().unwrap();
    fs::write(dir.path().join("a.txt"), "needle\n").unwrap();
    fs::write(dir.path().join("skipme.txt"), "needle\n").unwrap();
    fs::write(dir.path().join(".gitignore"), "skipme.txt\n").unwrap();
    let init = Command::new("git")
        .arg("init")
        .arg("-q")
        .current_dir(dir.path())
        .output();
    if init.is_err() {
        eprintln!("skipping: git not available in this environment");
        return;
    }
    let fake_rg = fake_rg_asserting_ignore_file_values_script(
        dir.path(),
        &[".gitignore"],
        &[],
        "a.txt:needle\n",
    );

    let output = tg()
        .current_dir(dir.path())
        .args(["search", "needle", "."])
        .env("TG_RG_PATH", &fake_rg)
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
