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

import pytest

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
    event: str = "push",
) -> dict[str, Any]:
    return {
        "id": rid,
        "run_number": number,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion,
        "path": path,
        "head_sha": sha,
        "event": event,
    }


def payload(*runs: dict[str, Any], total: int | None = None) -> dict[str, Any]:
    return {"total_count": len(runs) if total is None else total, "workflow_runs": list(runs)}


def job_payload(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"total_count": len(pairs), "jobs": [{"name": n, "conclusion": c} for n, c in pairs]}


def jobs(*pairs: tuple[str, str]) -> dict[int, dict[str, Any]]:
    """Same jobs payload for every run id 1..29 (tests needing per-run jobs build the dict)."""
    return {i: job_payload(*pairs) for i in range(1, 30)}


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


@pytest.mark.parametrize(
    "message",
    [
        "chore: x [skip ci]",
        "chore: x [ci skip]",
        "chore: x [no ci]",
        "chore: x [skip actions]",
        "chore: x [actions skip]",
        "chore: x@@@@body@@@@skip-checks: true@@",
    ],
)
def test_skip_ci_commit_is_named(message: str) -> None:
    v = cv.classify(SHA, "ci.yml", payload(), commit_message=message.replace("@", chr(10)))
    assert v.state == cv.NO_RUN
    assert "skip-ci" in chr(10).join(v.detail)


def test_plain_message_is_not_skip_ci() -> None:
    v = cv.classify(SHA, "ci.yml", payload(), commit_message="fix: x")
    assert v.state == cv.NO_RUN and "skip-ci" not in chr(10).join(v.detail)


def test_push_failure_plus_schedule_neutral_is_failure() -> None:
    runs = payload(
        run(1, 10, conclusion="failure", event="push"),
        run(2, 11, conclusion="neutral", event="schedule"),
    )
    by_run = {1: job_payload(("t", "failure")), 2: job_payload(("t", "success"))}
    assert cv.classify(SHA, "ci.yml", runs, by_run).state == cv.FAILURE


def test_mistyped_event_filter_is_cannot_measure_naming_events() -> None:
    runs = payload(run(1, 10, event="push"), run(2, 11, event="schedule"))
    v = cv.classify(SHA, "ci.yml", runs, event="pushh")
    assert v.state == cv.CANNOT_MEASURE
    text = chr(10).join(v.detail)
    assert "'pushh'" in text and "push, schedule" in text


def test_event_filter_with_no_runs_at_all_is_still_no_run() -> None:
    assert cv.classify(SHA, "ci.yml", payload(), event="push").state == cv.NO_RUN


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
    assert (
        cv.classify(SHA, "ci.yml", payload(run(1, 10)), {1: {"jobs": []}}).state
        == cv.CANNOT_MEASURE
    )


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


def test_push_failure_then_schedule_success_is_not_success() -> None:
    """Different events never supersede each other (live: 03b81539 push #4100 + schedule #4101)."""
    runs = payload(
        run(1, 10, conclusion="failure", event="push"),
        run(2, 11, conclusion="success", event="schedule"),
    )
    by_run = {1: job_payload(("Test", "failure")), 2: job_payload(("Test", "success"))}
    v = cv.classify(SHA, "ci.yml", runs, by_run)
    assert v.state == cv.FAILURE and v.exit_code == 1
    assert v.failed_run_ids == [1]
    text = "\n".join(v.detail)
    assert "[push] FAILURE" in text and "[schedule] SUCCESS" in text and "WORST" in text


def test_push_rerun_attempt2_failure_after_schedule_success_is_failure() -> None:
    runs = payload(
        run(1, 10, conclusion="success", event="schedule"),
        run(2, 11, conclusion="success", event="push", attempt=1),
        run(2, 11, conclusion="failure", event="push", attempt=2),
    )
    by_run = {1: job_payload(("t", "success")), 2: job_payload(("t", "failure"))}
    assert cv.classify(SHA, "ci.yml", runs, by_run).state == cv.FAILURE


def test_event_filter_restricts_deliberately() -> None:
    runs = payload(
        run(1, 10, conclusion="failure", event="push"),
        run(2, 11, conclusion="success", event="schedule"),
    )
    by_run = {1: job_payload(("t", "failure")), 2: job_payload(("t", "success"))}
    assert cv.classify(SHA, "ci.yml", runs, by_run, event="schedule").state == cv.SUCCESS
    assert cv.classify(SHA, "ci.yml", runs, by_run, event="push").state == cv.FAILURE


def test_worst_ordering_across_events() -> None:
    runs = payload(
        run(1, 10, conclusion="cancelled", event="push"),
        run(2, 11, conclusion="success", event="schedule"),
    )
    by_run = {1: job_payload(("t", "cancelled")), 2: job_payload(("t", "success"))}
    assert cv.classify(SHA, "ci.yml", runs, by_run).state == cv.CANCELLED
    runs = payload(
        run(1, 10, conclusion="cancelled", event="push"),
        run(2, 11, conclusion="skipped", event="schedule"),
    )
    assert cv.classify(SHA, "ci.yml", runs, by_run).state == cv.CANNOT_MEASURE


def test_open_run_in_any_event_is_in_progress() -> None:
    runs = payload(
        run(1, 10, conclusion="failure", event="push"),
        run(2, 11, status="queued", conclusion=None, event="schedule"),
    )
    assert cv.classify(SHA, "ci.yml", runs).state == cv.IN_PROGRESS


def test_jobs_truncation_is_noted() -> None:
    big = {"total_count": 150, "jobs": [{"name": "t", "conclusion": "success"}]}
    v = cv.classify(SHA, "ci.yml", payload(run(1, 10)), {1: big})
    assert v.state == cv.SUCCESS and "jobs truncated" in "\n".join(v.detail)


def test_malformed_entries_never_raise_in_classify() -> None:
    bad = payload(run(1, 10))
    bad["workflow_runs"][0]["run_number"] = "abc"
    assert cv.classify(SHA, "ci.yml", bad).state == cv.CANNOT_MEASURE
    assert cv.classify(SHA, "ci.yml", {"total_count": 1, "workflow_runs": [None]}).state == (
        cv.CANNOT_MEASURE
    )


def _patch_main(monkeypatch: Any, runs: Any, jobs_for: Any = None) -> None:
    def fake_gh(args: list[str]) -> Any:
        if args[:2] == ["repo", "view"]:
            return {"nameWithOwner": "o/r"}
        if "/jobs" in args[1]:
            return jobs_for if jobs_for is not None else job_payload(("t", "success"))
        return runs

    monkeypatch.setattr(cv, "_gh_json", fake_gh)
    monkeypatch.setattr(cv, "resolve_sha", lambda sha: SHA)
    monkeypatch.setattr(cv, "_commit_message", lambda sha: "")


def test_main_malformed_run_number_is_cannot_measure(monkeypatch: Any, capsys: Any) -> None:
    bad = payload(run(1, 10))
    bad["workflow_runs"][0]["run_number"] = "abc"
    _patch_main(monkeypatch, bad)
    assert cv.main(["--sha", SHA]) == cv.EXIT_CODES[cv.CANNOT_MEASURE]
    assert "CI_VERDICT: CANNOT_MEASURE" in capsys.readouterr().out


def test_main_null_run_entry_is_cannot_measure(monkeypatch: Any, capsys: Any) -> None:
    _patch_main(monkeypatch, {"total_count": 1, "workflow_runs": [None]})
    assert cv.main(["--sha", SHA]) == cv.EXIT_CODES[cv.CANNOT_MEASURE]
    assert "CI_VERDICT: CANNOT_MEASURE" in capsys.readouterr().out


def test_main_mixed_events_reports_failure_with_log_lines(monkeypatch: Any, capsys: Any) -> None:
    runs = payload(
        run(1, 10, conclusion="failure", event="push"),
        run(2, 11, conclusion="success", event="schedule"),
    )

    def fake_gh(args: list[str]) -> Any:
        if args[:2] == ["repo", "view"]:
            return {"nameWithOwner": "o/r"}
        if "/runs/1/jobs" in args[1]:
            return job_payload(("Test", "failure"))
        if "/jobs" in args[1]:
            return job_payload(("Test", "success"))
        return runs

    monkeypatch.setattr(cv, "_gh_json", fake_gh)
    monkeypatch.setattr(cv, "resolve_sha", lambda sha: SHA)
    monkeypatch.setattr(cv, "_commit_message", lambda sha: "")
    monkeypatch.setattr(cv, "_run", lambda argv, timeout: "x FAILED tests/unit/test_a.py::t1\n")
    assert cv.main(["--sha", SHA]) == 1
    out = capsys.readouterr().out
    assert "CI_VERDICT: FAILURE" in out and "run 1: FAILED tests/unit/test_a.py::t1" in out


def test_parser_has_no_docstring_dependency() -> None:
    assert "commit SHA" in cv.build_parser().description
