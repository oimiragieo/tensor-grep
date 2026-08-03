"""Governance: Task 2A Round-60 CI wiring must not mask setup/discovery failure.

Inspects ``.github/workflows/ci.yml`` semantically (no network). These tests are
NOT Task 2A owned Windows nodes — they gate CI structure only.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MANIFEST = REPO_ROOT / "tests" / "fixtures" / "task2a_windows_node_manifest.json"

_CARGO_CMD = (
    "cargo test --manifest-path rust_core/Cargo.toml -p tensor_grep_rs "
    "--all-targets --no-run --message-format=json"
)


def _workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _task2a_steps(job_name: str) -> list[dict]:
    job = _workflow()["jobs"][job_name]
    return [
        step
        for step in job["steps"]
        if "Task 2A" in str(step.get("name") or "") and "Upload" not in str(step.get("name") or "")
    ]


def test_task2a_ci_has_no_github_job_env_override() -> None:
    """Runners must read the live Actions-provided GITHUB_JOB."""
    for job_name in ("test-python", "native-build-smoke"):
        for step in _task2a_steps(job_name):
            env = step.get("env") or {}
            assert "GITHUB_JOB" not in env, (
                f"{job_name}/{step.get('name')} overrides GITHUB_JOB={env.get('GITHUB_JOB')!r}"
            )
            run = str(step.get("run") or "")
            assert "GITHUB_JOB:" not in run
            assert "export GITHUB_JOB=" not in run


def test_task2a_ci_enumerates_all_owned_nodes_no_first_node_shortcut() -> None:
    """Each Windows job loops every manifest node it owns (duplicate-preserving)."""
    import json

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    test_python_ids = [
        n["id"]
        for n in payload["nodes"]
        if n["id"].startswith("python::") and n.get("job") == "test-python"
    ]
    native_py_ids = [
        n["id"]
        for n in payload["nodes"]
        if n["id"].startswith("python::") and n.get("job") == "native-build-smoke"
    ]
    rust_ids = [
        n["id"]
        for n in payload["nodes"]
        if n["id"].startswith("rust::") and n.get("job") == "native-build-smoke"
    ]
    assert len(test_python_ids) >= 1
    assert len(native_py_ids) >= 1
    assert len(rust_ids) == 9

    py_steps = _task2a_steps("test-python")
    assert len(py_steps) == 1
    py_run = str(py_steps[0]["run"])
    assert "while IFS= read -r NODE_ID" in py_run
    assert "python-node-ids.txt" in py_run
    assert "break" not in py_run
    assert "job') == 'test-python'" in py_run or 'job") == "test-python"' in py_run

    native_steps = _task2a_steps("native-build-smoke")
    assert len(native_steps) == 1
    native_run = str(native_steps[0]["run"])
    assert "while IFS= read -r NODE_ID" in native_run
    assert "native-python-node-ids.txt" in native_run
    assert "rust-node-ids.txt" in native_run
    assert "break" not in native_run
    assert "len(rust) == 9" in native_run


def test_task2a_ci_generates_real_cargo_json_messages() -> None:
    """native-build-smoke must invoke exact Cargo JSON selection once; no empty file."""
    native_run = str(_task2a_steps("native-build-smoke")[0]["run"])
    assert _CARGO_CMD in native_run
    assert "cargo-messages.jsonl" in native_run
    assert ": >" not in native_run
    assert "truncate" not in native_run
    # Fail closed on cargo setup/compile failure before runners.
    assert "cargo_rc" in native_run
    assert "SETUP FAILURE" in native_run
    assert '[ ! -s "$ART/cargo-messages.jsonl" ]' in native_run or (
        '! -s "$ART/cargo-messages.jsonl"' in native_run
    )


def test_task2a_ci_does_not_mask_expected_red_as_success() -> None:
    """Exit 2 / missing receipt / verifier failure must not green the step."""
    for job_name in ("test-python", "native-build-smoke"):
        run = str(_task2a_steps(job_name)[0]["run"])
        assert "expected RED exit 2" not in run
        assert 'rc" -ne 2' not in run
        assert '[ "$rc" -ne 2 ]' not in run
        assert '[ "$rc" -eq 2 ]' not in run
        # Verifier non-zero must not be treated as success-by-inversion.
        assert 'vrc" -eq 0' not in run
        assert "verifier must not pass without a receipt" not in run
        # After full census, RED remain visible.
        assert "refusing green CI" in run
        assert "NativeCiReceipt emit and verifier not implemented" in run
        # Setup/census failure distinguishable.
        assert "SETUP/CENSUS FAILURE" in run or "SETUP FAILURE" in run


def test_task2a_ci_upload_steps_are_always() -> None:
    workflow = _workflow()
    for job_name, needle in (
        ("test-python", "Upload Task 2A Python raw artifacts"),
        ("native-build-smoke", "Upload Task 2A native raw artifacts"),
    ):
        uploads = [
            s for s in workflow["jobs"][job_name]["steps"] if needle in str(s.get("name") or "")
        ]
        assert len(uploads) == 1, uploads
        assert "always()" in str(uploads[0].get("if") or "")


def test_task2a_ci_workflow_yaml_still_parses() -> None:
    doc = _workflow()
    assert "test-python" in doc["jobs"]
    assert "native-build-smoke" in doc["jobs"]
    assert _task2a_steps("test-python")
    assert _task2a_steps("native-build-smoke")
