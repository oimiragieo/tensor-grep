"""Print the labelled CI verdict for ONE commit SHA, so "done" cannot be written over a red run.

WHY THIS EXISTS
---------------
AGENTS.md A139/A133/A142: `gh run list --limit 1` (and any windowed `--commit` list) returns the
newest run and hides the one that matters, and a bare "no output / zero failures" is
indistinguishable from "the probe could not see". This helper asks GitHub for the runs of the
EXACT head SHA (`actions/runs?head_sha=<full sha>`), keeps only the requested workflow, and
prints a LABELLED state -- never a bare zero.

STATES AND EXIT CODES (distinct per state; documented in `--help`)
------------------------------------------------------------------
    0  SUCCESS         newest run of EVERY event completed with conclusion=success (the run
                       passed; for a release also confirm the chore(release) commit and PyPI)
    1  FAILURE         newest run failed / timed_out / startup_failure; failing jobs and the
                       pytest `FAILED tests/...` lines (bounded) are listed
    2  CANNOT_MEASURE  gh error, auth, network, timeout, truncated or malformed payload, or a
                       conclusion this tool will not call green (skipped/neutral/stale/...)
    3  IN_PROGRESS     a run for the SHA is queued / pending / waiting / requested / in_progress
                       (A140: `pending` with 0 jobs is a concurrency hold, not a hang)
    4  NO_RUN          no run of the workflow exists for the SHA (a `[skip ci]` commit is named
                       as such; runs of OTHER workflows on the SHA are ignored on purpose)
    5  CANCELLED       newest run was cancelled and nothing newer exists for the SHA
                       (a cancelled run followed by a newer run is judged by the newer run)

Runs of different events (push, schedule, ...) never supersede each other: the newest run is
chosen per event and the verdict is the WORST across events (FAILURE > CANNOT_MEASURE >
CANCELLED > SUCCESS); `--event push` restricts deliberately.

The classifier (`classify`) is a pure function over decoded JSON; it is tested offline against
recorded fixtures. Only `main()` shells out, via list-argv subprocess with bounded timeouts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

SUCCESS = "SUCCESS"
FAILURE = "FAILURE"
CANNOT_MEASURE = "CANNOT_MEASURE"
IN_PROGRESS = "IN_PROGRESS"
NO_RUN = "NO_RUN"
CANCELLED = "CANCELLED"

EXIT_CODES = {
    SUCCESS: 0,
    FAILURE: 1,
    CANNOT_MEASURE: 2,
    IN_PROGRESS: 3,
    NO_RUN: 4,
    CANCELLED: 5,
}

_OPEN_STATUSES = frozenset({"queued", "pending", "waiting", "requested", "in_progress"})
_FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_SKIP_CI_RE = re.compile(r"\[(skip ci|ci skip|no ci|skip actions|actions skip)\]", re.IGNORECASE)
_SKIP_TRAILER_RE = re.compile(r"^skip-checks:\s*true\s*$", re.IGNORECASE | re.MULTILINE)
_FAILED_LINE_RE = re.compile(r"\bFAILED tests/\S+")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

GH_TIMEOUT_SECONDS = 60
LOG_TIMEOUT_SECONDS = 180
GIT_TIMEOUT_SECONDS = 20


def _skip_ci(message: str) -> bool:
    return bool(_SKIP_CI_RE.search(message or "") or _SKIP_TRAILER_RE.search(message or ""))


@dataclass
class Verdict:
    state: str
    sha: str
    workflow: str
    detail: list[str] = field(default_factory=list)
    # Newest run per event, as chosen by classify(); main() fetches jobs/logs for THESE and never
    # re-derives the selection.
    chosen: list[dict[str, Any]] = field(default_factory=list)
    failed_run_ids: list[int] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]


# Worst-wins ordering across events (higher = worse).
_SEVERITY = {SUCCESS: 0, CANCELLED: 1, CANNOT_MEASURE: 2, FAILURE: 3}


def _run_key(run: dict[str, Any]) -> tuple[int, int]:
    return (int(run.get("run_number", 0)), int(run.get("run_attempt", 1)))


def _describe(run: dict[str, Any]) -> str:
    return (
        f"run {run.get('id')} #{run.get('run_number')} attempt {run.get('run_attempt', 1)} "
        f"event={run.get('event')} status={run.get('status')} conclusion={run.get('conclusion')}"
    )


def failing_jobs(jobs_payload: dict[str, Any]) -> list[str]:
    names = []
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") in _FAILED_CONCLUSIONS:
            names.append(str(job.get("name")))
    return names


def _matching_runs(
    runs_payload: Any, sha: str, workflow: str, event: str | None
) -> tuple[list[dict[str, Any]], int]:
    """The ONE place runs are filtered. Raises ValueError/TypeError/AttributeError on bad input."""
    if not isinstance(runs_payload, dict) or not isinstance(
        runs_payload.get("workflow_runs"), list
    ):
        raise ValueError("malformed runs payload (no workflow_runs list)")
    all_runs = runs_payload["workflow_runs"]
    total = runs_payload.get("total_count")
    if not isinstance(total, int) or total < len(all_runs):
        raise ValueError(f"malformed total_count={total!r} for {len(all_runs)} runs")
    if total > len(all_runs):
        raise ValueError(f"truncated: total_count={total} but only {len(all_runs)} runs returned")
    mine = [
        r
        for r in all_runs
        if r.get("head_sha") == sha
        and str(r.get("path", "")).rsplit("/", 1)[-1].split("@", 1)[0] == workflow
        and (event is None or r.get("event") == event)
    ]
    for r in mine:
        _run_key(r)  # validate numerics now, not later
    mine.sort(key=_run_key)
    return mine, len(all_runs)


def classify(
    sha: str,
    workflow: str,
    runs_payload: Any,
    jobs_by_run: dict[int, Any] | None = None,
    commit_message: str = "",
    event: str | None = None,
) -> Verdict:
    """Pure classification of one SHA's runs. Never raises on malformed input.

    Runs of DIFFERENT events (push vs schedule vs ...) never supersede each other: the newest run
    is chosen per event, and the verdict is the WORST across events.
    """
    v = Verdict(CANNOT_MEASURE, sha, workflow)
    jobs_by_run = jobs_by_run or {}
    try:
        mine, n_all = _matching_runs(runs_payload, sha, workflow, event)
    except (ValueError, TypeError, AttributeError) as exc:
        v.detail.append(f"malformed payload: {exc}")
        return v

    if not mine:
        v.state = NO_RUN
        if _skip_ci(commit_message):
            v.detail.append("commit message carries a skip-ci marker: no CI run was ever created")
        else:
            v.detail.append(
                f"no {workflow} run exists for this SHA (not pushed, or not yet created)"
            )
        if n_all:
            v.detail.append(f"{n_all} run(s) of other workflows/SHAs exist and were ignored")
        return v

    newest_by_event: dict[str, dict[str, Any]] = {}
    for r in mine:  # sorted ascending, so the last write per event is the newest
        newest_by_event[str(r.get("event"))] = r
    v.chosen = list(newest_by_event.values())

    open_runs = [r for r in mine if r.get("status") in _OPEN_STATUSES]
    if open_runs:
        v.state = IN_PROGRESS
        v.detail.extend(_describe(r) for r in open_runs)
        return v

    worst = SUCCESS
    for ev, newest in newest_by_event.items():
        same_event = [r for r in mine if str(r.get("event")) == ev]
        state, lines = _classify_one(newest, jobs_by_run.get(newest.get("id")))
        if len(same_event) > 1:
            lines.append(
                f"{len(same_event)} {ev} runs for this SHA; judged by the newest, earlier superseded: "
                + "; ".join(f"{r.get('id')}={r.get('conclusion')}" for r in same_event[:-1])
            )
        v.detail.append(f"[{ev}] {state}: {_describe(newest)}")
        v.detail.extend(f"  {ln}" for ln in lines)
        if state == FAILURE:
            v.failed_run_ids.append(newest["id"])
        if _SEVERITY[state] > _SEVERITY[worst]:
            worst = state
    v.state = worst
    if len(newest_by_event) > 1:
        v.detail.append(
            f"{len(newest_by_event)} events ({', '.join(newest_by_event)}): verdict is the WORST of them"
        )
    return v


def _classify_one(run: dict[str, Any], jobs_payload: Any) -> tuple[str, list[str]]:
    lines: list[str] = []
    if run.get("status") != "completed":
        return CANNOT_MEASURE, [f"unrecognised status {run.get('status')!r}"]
    conclusion = run.get("conclusion")
    jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
    if isinstance(jobs_payload, dict) and isinstance(jobs, list):
        total = jobs_payload.get("total_count")
        if isinstance(total, int) and total > len(jobs):
            lines.append(f"jobs truncated: total_count={total}, only {len(jobs)} returned")
    if conclusion == "success":
        if not isinstance(jobs, list) or not jobs:
            lines.append("success with no jobs visible: cannot confirm the run executed")
            return CANNOT_MEASURE, lines
        lines.append(f"{len(jobs)} job(s) in the run")
        return SUCCESS, lines
    if conclusion in _FAILED_CONCLUSIONS:
        names = failing_jobs(jobs_payload) if isinstance(jobs_payload, dict) else []
        lines.append("failing jobs: " + (", ".join(names) if names else "(none visible)"))
        return FAILURE, lines
    if conclusion == "cancelled":
        lines.append(
            "cancelled and no newer run of this event exists for the SHA (a superseded or "
            "concurrency-cancelled run, not a code verdict); re-run it or push the successor"
        )
        return CANCELLED, lines
    lines.append(f"conclusion {conclusion!r} is not treated as green")
    return CANNOT_MEASURE, lines


def extract_failed_tests(log_text: str, limit: int) -> list[str]:
    seen: list[str] = []
    for line in log_text.splitlines():
        m = _FAILED_LINE_RE.search(line)
        if m and m.group(0) not in seen:
            seen.append(m.group(0))
        if len(seen) >= limit:
            break
    return seen


class GhError(RuntimeError):
    pass


def _run(argv: list[str], timeout: int) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: {exc}") from exc
    if proc.returncode != 0:
        raise GhError(f"{' '.join(argv[:3])} exit {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _gh_json(args: list[str]) -> Any:
    out = _run(["gh", *args], GH_TIMEOUT_SECONDS)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise GhError(f"gh {args[0]} returned non-JSON: {exc}") from exc


def resolve_sha(sha: str | None) -> str:
    rev = sha or "HEAD"
    try:
        full = _run(
            ["git", "rev-parse", "--verify", f"{rev}^{{commit}}"], GIT_TIMEOUT_SECONDS
        ).strip()
    except GhError:
        if sha and _FULL_SHA_RE.match(sha):
            return sha
        raise
    if not _FULL_SHA_RE.match(full):
        raise GhError(f"git rev-parse returned {full!r}")
    return full


def _commit_message(sha: str) -> str:
    try:
        return _run(["git", "log", "-1", "--format=%B", sha], GIT_TIMEOUT_SECONDS)
    except GhError:
        return ""


def build_parser() -> argparse.ArgumentParser:
    epilog = "exit codes: " + ", ".join(f"{c}={s}" for s, c in EXIT_CODES.items())
    p = argparse.ArgumentParser(
        description="Print the labelled CI verdict for one commit SHA (never a windowed run list).",
        epilog=epilog,
    )
    p.add_argument("--sha", help="commit to check (default: git rev-parse HEAD)")
    p.add_argument("--workflow", default="ci.yml", help="workflow file name (default ci.yml)")
    p.add_argument(
        "--event",
        help="restrict to one event (e.g. push); default judges ALL events, worst wins",
    )
    p.add_argument("--max-failed-lines", type=int, default=30, help="bound on FAILED lines printed")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sha = args.sha or "HEAD"
    try:
        sha = resolve_sha(args.sha)
        repo = _gh_json(["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"]
        runs = _gh_json(["api", f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100"])
        message = _commit_message(sha)
        verdict = classify(sha, args.workflow, runs, None, message, args.event)
        jobs_by_run: dict[int, Any] = {}
        for run in verdict.chosen:
            if run.get("status") == "completed":
                jobs_by_run[run["id"]] = _gh_json([
                    "api",
                    f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100",
                ])
        if jobs_by_run:
            verdict = classify(sha, args.workflow, runs, jobs_by_run, message, args.event)
        for run_id in verdict.failed_run_ids:
            try:
                log = _run(["gh", "run", "view", str(run_id), "--log-failed"], LOG_TIMEOUT_SECONDS)
                tests = extract_failed_tests(log, args.max_failed_lines)
                if not tests:
                    verdict.detail.append(
                        f"run {run_id}: no pytest FAILED lines in --log-failed ({len(log)} bytes); "
                        "read the failing job's log directly"
                    )
                verdict.detail.extend(f"  run {run_id}: {t}" for t in tests)
            except GhError as exc:
                verdict.detail.append(f"run {run_id}: could not read --log-failed: {exc}")
    except (GhError, KeyError, TypeError, ValueError, AttributeError) as exc:
        verdict = Verdict(CANNOT_MEASURE, sha, args.workflow, [f"{type(exc).__name__}: {exc}"])
    print(f"CI_VERDICT: {verdict.state}  sha={verdict.sha}  workflow={verdict.workflow}")
    for line in verdict.detail:
        print(line if line.startswith(" ") else f"  {line}")
    return verdict.exit_code


if __name__ == "__main__":
    sys.exit(main())
