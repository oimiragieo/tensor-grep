use std::fs;
use std::path::Path;
use std::process::{Output, Stdio};
use std::time::Duration;

use process_control::{ChildExt, Control};
use serde_json::Value;
use tempfile::{tempdir, TempDir};

#[path = "support/routing.rs"]
mod routing_support;

use routing_support::{combined_output, repo_python, repo_root, tg};

fn scan_routing_fixture() -> TempDir {
    let fixture = tempdir().unwrap();
    let root = fixture.path();
    for directory in ["rules", "alt-rules", "src/sub", "src/other", "-dash"] {
        fs::create_dir_all(root.join(directory)).unwrap();
    }
    fs::write(
        root.join("sgconfig.yml"),
        "ruleDirs:\n  - rules\nlanguage: python\n",
    )
    .unwrap();
    fs::write(
        root.join("alternate.yml"),
        "ruleDirs:\n  - alt-rules\nlanguage: python\n",
    )
    .unwrap();
    fs::write(
        root.join("rules/print.yml"),
        "id: print-call\nlanguage: python\nrule:\n  pattern: print($A)\n",
    )
    .unwrap();
    fs::write(
        root.join("alt-rules/abs.yml"),
        "id: abs-call\nlanguage: python\nrule:\n  pattern: abs($A)\n",
    )
    .unwrap();
    for file in ["src/sub/a.py", "src/other/b.py", "-dash/c.py"] {
        fs::write(root.join(file), "print(42)\n").unwrap();
    }
    let positive = run_scan_routing(root, &["scan", "src/sub", "--json"]);
    assert!(
        positive.status.success(),
        "SETUP: positive AST scan failed: {}",
        combined_output(&positive)
    );
    let payload: Value =
        serde_json::from_slice(&positive.stdout).expect("SETUP: JSON positive scan output");
    assert_eq!(
        payload["total_matches"], 1,
        "SETUP: fixture must match exactly one source call"
    );
    fixture
}

fn run_scan_routing(root: &Path, args: &[&str]) -> Output {
    let child = tg()
        .args(args)
        .current_dir(root)
        .env("TG_SIDECAR_PYTHON", repo_python())
        .env("PYTHONPATH", repo_root().join("src"))
        .env("TG_DISABLE_NATIVE_TG", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("failed to spawn scan routing process");
    // Drain both pipes while enforcing the bound, following the index-lock tests.
    let timeout = Duration::from_secs(90);
    match child
        .controlled_with_output()
        .time_limit(timeout)
        .terminate_for_timeout()
        .wait()
    {
        Ok(Some(output)) => output.into_std_lossy(),
        Ok(None) => panic!("scan routing process exceeded {timeout:?} and was terminated"),
        Err(error) => panic!("failed waiting for scan routing process: {error}"),
    }
}

#[test]
fn scan_config_equals_preserves_selected_configuration() {
    let fixture = scan_routing_fixture();
    for args in [
        vec!["scan", "--config", "alternate.yml"],
        vec!["scan", "--config=alternate.yml"],
    ] {
        let result = run_scan_routing(fixture.path(), &args);
        assert!(result.status.success(), "{}", combined_output(&result));
        assert!(
            String::from_utf8_lossy(&result.stdout).contains("total_matches=0"),
            "selected alternate config ignored: {}",
            combined_output(&result)
        );
    }
}

#[test]
fn scan_end_of_options_preserves_literal_dash_prefixed_path() {
    let fixture = scan_routing_fixture();
    let result = run_scan_routing(fixture.path(), &["scan", "--", "-dash"]);
    assert!(
        result.status.success(),
        "literal path failed after argument boundary: {}",
        combined_output(&result)
    );
    assert!(
        String::from_utf8_lossy(&result.stdout).contains("total_matches=1"),
        "wrong literal-path scope: {}",
        combined_output(&result)
    );
}

#[test]
fn scan_text_honors_subtree_and_refuses_missing_path() {
    let fixture = scan_routing_fixture();
    let result = run_scan_routing(fixture.path(), &["scan", "src/sub"]);
    assert!(result.status.success(), "{}", combined_output(&result));
    assert!(
        String::from_utf8_lossy(&result.stdout).contains("total_matches=1"),
        "subtree scope lost: {}",
        combined_output(&result)
    );
    let missing = run_scan_routing(fixture.path(), &["scan", "missing"]);
    assert_eq!(
        missing.status.code(),
        Some(2),
        "missing path accepted: {}",
        combined_output(&missing)
    );
}

#[test]
fn scan_alternate_config_and_subtree_nonzero_differential() {
    let fixture = scan_routing_fixture();
    let root = fixture.path();
    for (relative, content) in [
        ("src/sub/a.py", "print(1)\nprint(2)\nabs(-101)\n"),
        ("src/other/b.py", "print(3)\nabs(-201)\nabs(-202)\n"),
        ("-dash/c.py", "print(4)\nabs(-301)\nabs(-302)\nabs(-303)\n"),
    ] {
        fs::write(root.join(relative), content).unwrap();
    }
    for (args, expected, rule) in [
        (
            vec!["scan", "--config", "alternate.yml", "--json"],
            6,
            "abs-call",
        ),
        (
            vec!["scan", "--config=alternate.yml", "src/sub", "--json"],
            1,
            "abs-call",
        ),
        (vec!["scan", "src/sub", "--json"], 2, "print-call"),
    ] {
        let output = run_scan_routing(root, &args);
        assert!(output.status.success(), "{}", combined_output(&output));
        let payload: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(payload["total_matches"], expected);
        assert_eq!(payload["findings"][0]["rule_id"], rule);
    }
    for args in [
        vec!["scan", "--config=alternate.yml", "src/sub"],
        vec!["scan", "-c", "alternate.yml", "src/sub"],
    ] {
        let output = run_scan_routing(root, &args);
        assert!(output.status.success(), "{}", combined_output(&output));
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("total_matches=1"),
            "config and selected subtree must both survive routing: {}",
            combined_output(&output)
        );
    }
}

#[test]
fn scan_literal_json_named_path_keeps_text_mode() {
    let fixture = scan_routing_fixture();
    let root = fixture.path();
    fs::create_dir(root.join("--json")).unwrap();
    fs::write(root.join("--json/literal.py"), "print(1)\nprint(2)\n").unwrap();
    let positive = run_scan_routing(root, &["scan", "--json", "--", "--json"]);
    assert!(positive.status.success(), "{}", combined_output(&positive));
    let payload: Value = serde_json::from_slice(&positive.stdout).unwrap();
    assert_eq!(payload["total_matches"], 2);
    assert_eq!(payload["findings"][0]["rule_id"], "print-call");
    assert_eq!(payload["findings"][0]["files"].as_array().unwrap().len(), 1);
    assert!(payload["findings"][0]["files"][0]
        .as_str()
        .unwrap()
        .replace('\\', "/")
        .ends_with("/--json/literal.py"));
    let result = run_scan_routing(root, &["scan", "--", "--json"]);
    assert!(result.status.success(), "{}", combined_output(&result));
    assert!(
        String::from_utf8_lossy(&result.stdout).contains("total_matches=2"),
        "literal --json path changed output mode or scope: {}",
        combined_output(&result)
    );
}

#[test]
fn scan_json_named_config_is_consumed_as_value() {
    let fixture = scan_routing_fixture();
    let root = fixture.path();
    fs::write(
        root.join("--json"),
        "ruleDirs:\n  - alt-rules\nlanguage: python\n",
    )
    .unwrap();
    fs::write(root.join("src/sub/a.py"), "print(1)\nprint(2)\nabs(-101)\n").unwrap();
    fs::write(
        root.join("src/other/b.py"),
        "print(3)\nabs(-201)\nabs(-202)\n",
    )
    .unwrap();
    for args in [
        vec!["scan", "--config", "--json", "src/sub"],
        vec!["scan", "--config=--json", "src/sub"],
    ] {
        let mut positive_args = args.clone();
        positive_args.push("--json");
        let positive = run_scan_routing(root, &positive_args);
        assert!(positive.status.success(), "{}", combined_output(&positive));
        let payload: Value = serde_json::from_slice(&positive.stdout).unwrap();
        assert_eq!(payload["total_matches"], 1);
        assert_eq!(payload["findings"][0]["rule_id"], "abs-call");
        assert_eq!(payload["findings"][0]["files"].as_array().unwrap().len(), 1);
        assert!(payload["findings"][0]["files"][0]
            .as_str()
            .unwrap()
            .replace('\\', "/")
            .ends_with("/src/sub/a.py"));
        let result = run_scan_routing(root, &args);
        assert!(result.status.success(), "{}", combined_output(&result));
        let text = String::from_utf8_lossy(&result.stdout);
        assert!(
            text.contains("total_matches=1"),
            "{}",
            combined_output(&result)
        );
        assert!(
            text.contains("rule=abs-call"),
            "{}",
            combined_output(&result)
        );
        let config_only_args = &args[..args.len() - 1];
        let mut all_positive_args = config_only_args.to_vec();
        all_positive_args.push("--json");
        let all_positive = run_scan_routing(root, &all_positive_args);
        assert!(
            all_positive.status.success(),
            "{}",
            combined_output(&all_positive)
        );
        let all_payload: Value = serde_json::from_slice(&all_positive.stdout).unwrap();
        assert_eq!(all_payload["total_matches"], 3);
        assert_eq!(all_payload["findings"][0]["rule_id"], "abs-call");
        let config_only = run_scan_routing(root, config_only_args);
        assert!(
            config_only.status.success(),
            "{}",
            combined_output(&config_only)
        );
        let all_text = String::from_utf8_lossy(&config_only.stdout);
        assert!(
            all_text.contains("total_matches=3"),
            "{}",
            combined_output(&config_only)
        );
        assert!(
            all_text.contains("rule=abs-call"),
            "{}",
            combined_output(&config_only)
        );
    }
}
