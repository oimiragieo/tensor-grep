"""Offline tests for scripts/ci_verdict.py. No network, no gh, no subprocess.

Payload shapes mirror the real `actions/runs?head_sha=` / `runs/<id>/jobs` responses (recorded
2026-10-03 against origin/main). The classifier must return a LABELLED state for every input,
including a positive control that it can return FAILURE at all.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "ci_verdict", REPO_ROOT / "scripts" / "ci_verdict.py"
)
assert _spec is not None and _spec.loader is not None
cv = importlib.util.module_from_spec(_spec)
sys.modules["ci_verdict"] = cv
_spec.loader.exec_module(cv)

SHA = "a" * 40
OTHER = "b" * 40


def run(
    rid: int,
    number: int,
    status: str = "completed",
    conclusion: str | None = "success",
    path: str = ".github/workflows/ci.yml",
    sha: str = SHA,
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "id": rid,
        "run_number": number,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion,
        "path": path,
        "head_sha": sha,
        "event": "push",
    }


def payload(*runs: dict[str, Any], total: int | None = None) -> dict[str, Any]:
    return {"total_count": len(runs) if total is None else total, "workflow_runs": list(runs)}


def jobs(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"total_count": len(pairs), "jobs": [{"name": n, "conclusion": c} for n, c in pairs]}


def test_success() -> None:
    v = cv.classify(
        SHA, "ci.yml", payload(run(1, 10)), jobs(("lint", "success"), ("test", "success"))
    )
    assert v.state == cv.SUCCESS and v.exit_code == 0


def test_failure_positive_control_lists_failed_jobs() -> None:
    v = cv.classify(
        SHA,
        "ci.yml",
        payload(run(1, 10, conclusion="failure")),
        jobs(("lint", "success"), ("Test (3.12)", "failure"), ("Test (3.13)", "timed_out")),
    )
    assert v.state == cv.FAILURE and v.exit_code == 1
    text = "\n".join(v.detail)
    assert (
        "Test (3.12)" in text
        and "Test (3.13)" in text
        and "lint" not in text.split("failing jobs:")[1]
    )


def test_cancelled_then_rerun_success_is_success() -> None:
    v = cv.classify(
        SHA,
        "ci.yml",
        payload(run(1, 10, conclusion="cancelled"), run(2, 11, conclusion="success")),
        jobs(("test", "success")),
    )
    assert v.state == cv.SUCCESS
    assert "superseded" in "\n".join(v.detail) and "1=cancelled" in "\n".join(v.detail)


def test_cancelled_alone_is_cancelled() -> None:
    v = cv.classify(
        SHA, "ci.yml", payload(run(1, 10, conclusion="cancelled")), jobs(("t", "cancelled"))
    )
    assert v.state == cv.CANCELLED and v.exit_code == 5
    assert "no newer run" in "\n".join(v.detail)


def test_success_then_newer_failure_is_failure() -> None:
    v = cv.classify(
        SHA,
        "ci.yml",
        payload(run(1, 10), run(2, 11, conclusion="failure")),
        jobs(("t", "failure")),
    )
    assert v.state == cv.FAILURE


def test_in_progress_every_open_status() -> None:
    for status in ("queued", "pending", "waiting", "requested", "in_progress"):
        v = cv.classify(SHA, "ci.yml", payload(run(1, 10, status=status, conclusion=None)))
        assert v.state == cv.IN_PROGRESS and v.exit_code == 3, status


def test_open_older_run_beats_completed_newer() -> None:
    v = cv.classify(
        SHA,
        "ci.yml",
        payload(run(1, 10, status="in_progress", conclusion=None), run(2, 11)),
        jobs(("t", "success")),
    )
    assert v.state == cv.IN_PROGRESS


def test_no_runs_is_no_run() -> None:
    v = cv.classify(SHA, "ci.yml", payload())
    assert v.state == cv.NO_RUN and v.exit_code == 4


def test_skip_ci_commit_is_named() -> None:
    v = cv.classify(SHA, "ci.yml", payload(), commit_message="chore(release): v1 [skip ci]\n")
    assert v.state == cv.NO_RUN
    assert "skip-ci" in "\n".join(v.detail)


def test_other_workflows_and_other_shas_are_ignored() -> None:
    codeql = run(5, 3, path="dynamic/github-code-scanning/codeql")
    elsewhere = run(6, 4, sha=OTHER)
    v = cv.classify(SHA, "ci.yml", payload(codeql, elsewhere))
    assert v.state == cv.NO_RUN
    assert "other workflows" in "\n".join(v.detail)


def test_malformed_payloads_are_cannot_measure() -> None:
    for bad in (
        None,
        [],
        "x",
        {},
        {"workflow_runs": "no"},
        {"workflow_runs": [], "total_count": "3"},
    ):
        v = cv.classify(SHA, "ci.yml", bad)
        assert v.state == cv.CANNOT_MEASURE and v.exit_code == 2, bad
    v = cv.classify(SHA, "ci.yml", {"total_count": 1, "workflow_runs": [None]})
    assert v.state == cv.CANNOT_MEASURE


def test_truncated_listing_is_cannot_measure() -> None:
    v = cv.classify(SHA, "ci.yml", payload(run(1, 10), total=150))
    assert v.state == cv.CANNOT_MEASURE and "truncated" in "\n".join(v.detail)


def test_success_without_jobs_is_not_green() -> None:
    assert cv.classify(SHA, "ci.yml", payload(run(1, 10)), None).state == cv.CANNOT_MEASURE
    assert cv.classify(SHA, "ci.yml", payload(run(1, 10)), {"jobs": []}).state == cv.CANNOT_MEASURE


def test_skipped_or_neutral_conclusion_is_not_green() -> None:
    for c in ("skipped", "neutral", "stale", "action_required", None):
        v = cv.classify(SHA, "ci.yml", payload(run(1, 10, conclusion=c)), jobs(("t", "success")))
        assert v.state == cv.CANNOT_MEASURE, c


def test_exit_codes_are_distinct() -> None:
    assert len(set(cv.EXIT_CODES.values())) == len(cv.EXIT_CODES) == 6


def test_extract_failed_tests_bounded_and_deduped() -> None:
    log = "\n".join(
        ["x\tRun Pytest\t2026 FAILED tests/unit/test_a.py::t1 - boom"]
        + ["y\tz\tFAILED tests/unit/test_a.py::t1 - boom"]
        + [f"y\tz\tFAILED tests/unit/test_b.py::t{i}" for i in range(50)]
        + ["noise FAILED other/thing"]
    )
    got = cv.extract_failed_tests(log, 5)
    assert len(got) == 5 and got[0] == "FAILED tests/unit/test_a.py::t1"
    assert cv.extract_failed_tests("nothing here", 5) == []
