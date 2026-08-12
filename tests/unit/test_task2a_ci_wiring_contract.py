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
    assert len(rust_ids) == 12

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
    assert "len(rust) == 12" in native_run


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
        # After full census, RED remain visible; verify path must be wired.
        assert "refusing green" in run
        assert "VERIFY_RC" in run
        assert "verify_task2a_windows_nodes.py" in run
        assert "NativeCiReceipt emit and verifier not implemented" not in run
        # Setup/census failure distinguishable.
        assert "SETUP/CENSUS FAILURE" in run or "SETUP FAILURE" in run
        # Unconditional forever-stub exit 1 (ignoring VERIFY_RC) is forbidden.
        assert 'exit "$VERIFY_RC"' in run or "exit $VERIFY_RC" in run
        # Sol R4 HIGH#1: pass census artifact paths runners actually write.
        assert "--junit" in run
        assert "junit.xml" in run
        if job_name == "native-build-smoke":
            assert "--rust-list" in run
            assert "rust-list.txt" in run


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


def test_runners_do_not_classify_crash_or_setup_as_behavioral_red(tmp_path: Path) -> None:
    """A61 / HIGH#2: crash/setup/import/panic are not behavioral RED phases."""
    import importlib.util
    import sys

    def _load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    py_runner = _load(
        "run_task2a_pytest_nodes", REPO_ROOT / "scripts" / "run_task2a_pytest_nodes.py"
    )
    rust_runner = _load("run_task2a_rust_node", REPO_ROOT / "scripts" / "run_task2a_rust_node.py")

    junit = tmp_path / "crash.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="1" errors="1" failures="0" skipped="0">
  <testcase classname="tests.unit.test_x" name="test_leaf" file="tests/unit/test_x.py">
    <error message="ImportError">ImportError: boom</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    crash_phase = py_runner.classify_pytest_node_phase(
        junit_path=junit,
        pytest_nodeid="tests/unit/test_x.py::test_leaf",
        exit_code=1,
    )
    assert crash_phase != "executed_refused_receipt"
    assert crash_phase in {"crash_or_setup", "non_behavioral"}

    fail_junit = tmp_path / "fail.xml"
    fail_junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="1" errors="0" failures="1" skipped="0">
  <testcase classname="tests.unit.test_x" name="test_leaf" file="tests/unit/test_x.py">
    <failure message="AssertionError">AssertionError: expected reason</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    fail_phase = py_runner.classify_pytest_node_phase(
        junit_path=fail_junit,
        pytest_nodeid="tests/unit/test_x.py::test_leaf",
        exit_code=1,
    )
    assert fail_phase == "executed_refused_receipt"

    # F1 mutation control (Sol round 1): a non-AssertionError failure type is a
    # crash/setup error, NOT behavioral RED — an uncaught NotImplementedError from a
    # behaviorless stub must not earn a receipt.
    nie_junit = tmp_path / "nie.xml"
    nie_junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="1" errors="0" failures="1" skipped="0">
  <testcase classname="tests.unit.test_x" name="test_leaf" file="tests/unit/test_x.py">
    <failure type="NotImplementedError" message="stub">NotImplementedError</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )
    nie_phase = py_runner.classify_pytest_node_phase(
        junit_path=nie_junit,
        pytest_nodeid="tests/unit/test_x.py::test_leaf",
        exit_code=1,
    )
    assert nie_phase != "executed_refused_receipt", (
        "NotImplementedError failure must classify as crash_or_setup, not behavioral RED"
    )
    assert nie_phase in {"crash_or_setup", "non_behavioral"}

    panic_phase = rust_runner.classify_rust_node_phase(
        exit_code=101,
        stdout="",
        stderr="thread 'tests::leaf' panicked at src/x.rs:1:1:\nbom",
    )
    assert panic_phase != "executed_refused_receipt"
    assert panic_phase in {"crash_or_setup", "non_behavioral"}

    assert (
        rust_runner.classify_rust_node_phase(
            exit_code=101,
            stdout="test leaf ... FAILED\n",
            stderr=(
                "thread 'tests::leaf' panicked at src/x.rs:1:1:\nassertion `left == right` failed"
            ),
        )
        == "executed_refused_receipt"
    )


def test_pcre2_construction_oracles_are_inside_closed_world_census() -> None:
    """HIGH#3: PCRE2 matcher-construction oracles must be owned census nodes (not ignored companions)."""
    import json
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from helpers import task2a_ownership as ownership

    required = {
        "rust::native_search::tests::pcre2_direct_native_route_zero_matcher_constructions_before_refusal",
        "rust::native_search::tests::below_cap_direct_native_route_one_matcher_construction",
    }
    registry = set(ownership.TASK2A_OWNED_RUST_NODE_IDS)
    manifest_ids = set(ownership.load_manifest_node_ids())
    missing_reg = sorted(required - registry)
    missing_man = sorted(required - manifest_ids)
    assert not missing_reg, f"construction oracles missing from rust registry: {missing_reg}"
    assert not missing_man, f"construction oracles missing from manifest: {missing_man}"

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rust = [n for n in payload["nodes"] if n["id"].startswith("rust::")]
    assert len(rust) == 12, (
        f"expected 12 rust census nodes after file-bytes oracle, got {len(rust)}"
    )
    for node_id in required:
        node = next(n for n in rust if n["id"] == node_id)
        vector = node["command_vector"]
        assert "--include-ignored" in vector, f"{node_id} must run via --include-ignored"
        assert "--lib" in vector
        assert node.get("rust_test_target") in node_id


def _manifest_python_files() -> set[str]:
    import json

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {n["id"].split("::")[1] for n in payload["nodes"] if n["id"].startswith("python::")}


def _test_python_steps() -> list[dict]:
    return _workflow()["jobs"]["test-python"]["steps"]


def test_task2a_blanket_pytest_excludes_exactly_the_census_owned_files() -> None:
    """codex H3 / R1: the WINDOWS blanket `Run Pytest` step must --ignore exactly the
    manifest-owned suite files — their intended-RED nodes otherwise abort the step
    (pyproject -x) before the Task 2A collector runs, making the census unreachable
    (receipt: CI run 31631927863, every test-python leg red at `Run Pytest`, collector
    never executed). Both directions: every owned file ignored; no extra --ignore
    smuggles an unowned file out of the blanket run.
    F3 (Sol round 1): the exclusion is WINDOWS-ONLY — a non-Windows blanket step must
    exist WITHOUT the ignores so the Linux-only Win32 node still executes there."""
    steps = _test_python_steps()
    blanket = [s for s in steps if str(s.get("name") or "") == "Run Pytest"]
    assert len(blanket) == 1, "exactly one Windows blanket Run Pytest step expected"
    win_step = blanket[0]
    assert " ".join(str(win_step.get("if") or "").split()) == "runner.os == 'Windows'", (
        f"Windows blanket must be gated to Windows, got {win_step.get('if')!r}"
    )
    run = str(win_step["run"])
    ignored = {
        part.split("=", 1)[1].strip() for part in run.split() if part.startswith("--ignore=")
    }
    owned = _manifest_python_files()
    assert owned, "manifest must own at least one python suite file"
    missing = sorted(owned - ignored)
    extra = sorted(ignored - owned)
    assert not missing, f"census-owned files NOT ignored by the blanket run: {missing}"
    assert not extra, f"blanket run ignores files the manifest does not own: {extra}"
    assert win_step.get("continue-on-error") is None, (
        "the blanket run must gate the job; continue-on-error would mask real regressions"
    )
    # F3: a non-Windows blanket step must exist and must NOT carry the census ignores.
    non_win = [
        s
        for s in steps
        if str(s.get("name") or "").startswith("Run Pytest")
        and str(s.get("name") or "") != "Run Pytest"
    ]
    assert len(non_win) == 1, "exactly one non-Windows blanket step expected"
    assert " ".join(str(non_win[0].get("if") or "").split()) == "runner.os != 'Windows'", (
        f"non-Windows blanket must be gated to non-Windows, got {non_win[0].get('if')!r}"
    )
    assert "--ignore=" not in str(non_win[0]["run"]), (
        "non-Windows blanket must not ignore the census suites (Linux-only nodes run here)"
    )


def test_task2a_collector_step_is_reachable_after_green_blanket() -> None:
    """codex H3 / R1 reachability ratchet: the Windows collector must sit AFTER the
    blanket run in step order (it depends on the installed env) and carry no condition
    beyond the documented runner.os gate — a `success()`-style inversion or an
    accidental extra gate would silently drop the census again."""
    steps = _test_python_steps()
    names = [str(s.get("name") or "") for s in steps]
    blanket_idx = names.index("Run Pytest")
    collector_idx = names.index("Task 2A Round-60 Python runner discovery (Windows)")
    assert blanket_idx < collector_idx, (
        "collector must follow the blanket run (env-install ordering)"
    )
    collector_if = str(steps[collector_idx].get("if") or "")
    # F6 (Sol round 1): require EXACT equality, not substring membership — a substring
    # check lets `runner.os == 'Windows' && false` pass while silently disabling the
    # collector. Normalize whitespace and demand the documented gate verbatim.
    assert " ".join(collector_if.split()) == "runner.os == 'Windows'", (
        f"collector condition must be exactly the OS gate, got {collector_if!r}"
    )


def test_task2a_census_owned_files_have_no_node_outside_manifest() -> None:
    """Closed-world containment: excluding the owned files from the blanket run is only
    safe if EVERY collectable node in them is census-owned — otherwise a node escapes
    both surfaces and silently loses coverage. Compare live pytest collection against
    the manifest (both directions)."""
    import json
    import subprocess
    import sys

    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_ids = {
        n["id"].removeprefix("python::") for n in payload["nodes"] if n["id"].startswith("python::")
    }
    files = sorted(_manifest_python_files())
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    collected = {
        line.strip().replace("\\", "/")
        for line in proc.stdout.splitlines()
        if "::" in line and not line.startswith(("=", " ", "<"))
    }
    assert collected, (
        f"collection returned nothing (exit {proc.returncode}); instrument failure, "
        f"stderr tail: {proc.stderr[-500:]}"
    )
    unowned = sorted(collected - manifest_ids)
    ghost = sorted(manifest_ids - collected)
    assert not unowned, f"nodes collectable but NOT census-owned (coverage escape): {unowned}"
    assert not ghost, f"census nodes no longer collectable (stale manifest): {ghost}"
