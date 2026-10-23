"""Contract checks for timing and compiler identity in existing CI jobs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

CI_WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
SIBLING_TEST = Path(__file__).with_name("test_ci_cost_os_gate_permissions.py")
_SPEC = importlib.util.spec_from_file_location("_ci_measurement_cost_loader", SIBLING_TEST)
assert _SPEC is not None and _SPEC.loader is not None
_LOADER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LOADER
_SPEC.loader.exec_module(_LOADER)


def _workflow() -> dict[Any, Any]:
    return _LOADER._load_workflow(CI_WORKFLOW.read_text(encoding="utf-8"))


def _job(workflow: dict[Any, Any], name: str) -> dict[str, Any]:
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[name]
    assert isinstance(job, dict)
    return job


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    steps = job["steps"]
    assert isinstance(steps, list)
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def test_PythonTestCommand_DurationsEnabled_PreservesCoverageArguments() -> None:
    workflow = _workflow()

    step = _step(_job(workflow, "test-python"), "Run Pytest")

    assert step["run"] == (
        'uv run pytest tests -v --tb=short -m "not eval" --maxfail=0 '
        "--durations=30 --durations-min=1"
    )


def _cargo_step_index(steps: list[dict[str, Any]]) -> int:
    matches = [index for index, step in enumerate(steps) if step.get("name") == "Cargo Test"]
    assert len(matches) == 1
    return matches[0]


def test_RustTestStep_ProvenanceEnabled_PreservesCargoInvocation() -> None:
    steps = _job(_workflow(), "test-rust-core")["steps"]

    cargo_index = _cargo_step_index(steps)

    assert steps[cargo_index - 1] == {
        "name": "Compiler provenance",
        "shell": "bash",
        "working-directory": "rust_core",
        "run": ("set -euo pipefail\nrustc -Vv\ncargo --version\nrustup show active-toolchain\n"),
    }
    assert steps[cargo_index] == {
        "name": "Cargo Test",
        "working-directory": "rust_core",
        "run": "cargo test --verbose --no-default-features",
    }
