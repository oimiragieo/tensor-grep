use std::fs;
use std::process::Command;

use serde_json::Value;
use tempfile::tempdir;

fn tg() -> Command {
    Command::new(env!("CARGO_BIN_EXE_tg"))
}

#[test]
fn native_tg_run_emits_python_return_type_advisory_across_json_and_text() {
    let dir = tempdir().unwrap();
    let typed_only = dir.path().join("typed_only.py");
    fs::write(&typed_only, "def typed(x: int) -> int:\n    return x\n").unwrap();

    // 1. Zero-match JSON mode: emits advisory_hints while preserving AstBackend native routing.
    let json_zero = tg()
        .current_dir(dir.path())
        .args([
            "run",
            "--lang",
            "python",
            "--json",
            "def $NAME($$$ARGS): $$$BODY",
            "typed_only.py",
        ])
        .output()
        .unwrap();
    assert_eq!(json_zero.status.code(), Some(1));
    let payload_zero: Value = serde_json::from_slice(&json_zero.stdout).unwrap();
    assert_eq!(payload_zero["routing_backend"], "AstBackend");
    assert_eq!(payload_zero["routing_reason"], "ast-native");
    assert_eq!(payload_zero["sidecar_used"], false);
    let hints_zero = payload_zero["advisory_hints"]
        .as_array()
        .expect("advisory_hints should be present")
        .iter()
        .filter_map(|v| v.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    assert!(hints_zero.contains("--selector function_definition"));
    assert!(hints_zero.contains("def $NAME($$$ARGS) -> $RET: $$$BODY"));

    // 2. Zero-match text mode: emits advisory_hints on stderr.
    let text_zero = tg()
        .current_dir(dir.path())
        .args([
            "run",
            "--lang",
            "python",
            "def $NAME($$$ARGS): $$$BODY",
            "typed_only.py",
        ])
        .output()
        .unwrap();
    assert_eq!(text_zero.status.code(), Some(1));
    let stderr_zero = String::from_utf8_lossy(&text_zero.stderr);
    assert!(
        stderr_zero.contains("advisory_hints:"),
        "stderr={stderr_zero}"
    );
    assert!(stderr_zero.contains("--selector function_definition"));

    // 3. Literal function name negative control: `def foo(` must NOT trigger on `def typed(...) -> int`.
    let literal_mismatch = tg()
        .current_dir(dir.path())
        .args([
            "run",
            "--lang",
            "python",
            "--json",
            "def foo($$$ARGS): $$$BODY",
            "typed_only.py",
        ])
        .output()
        .unwrap();
    assert_eq!(literal_mismatch.status.code(), Some(1));
    let payload_mismatch: Value = serde_json::from_slice(&literal_mismatch.stdout).unwrap();
    assert!(
        payload_mismatch.get("advisory_hints").is_none(),
        "unexpected advisory_hints: {payload_mismatch}"
    );

    // 4. Nonzero-match mixed file: matches untyped function and still emits advisory_hints for typed.
    let mixed = dir.path().join("mixed.py");
    fs::write(
        &mixed,
        "def untyped(x):\n    return x\n\ndef typed(x: int) -> int:\n    return x\n",
    )
    .unwrap();
    let json_mixed = tg()
        .current_dir(dir.path())
        .args([
            "run",
            "--lang",
            "python",
            "--json",
            "def $NAME($$$ARGS): $$$BODY",
            "mixed.py",
        ])
        .output()
        .unwrap();
    assert!(json_mixed.status.success());
    let payload_mixed: Value = serde_json::from_slice(&json_mixed.stdout).unwrap();
    assert_eq!(payload_mixed["total_matches"], 1);
    assert!(payload_mixed.get("advisory_hints").is_some());
}
