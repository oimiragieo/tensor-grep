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
    0  SUCCESS         newest run for the SHA completed with conclusion=success
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
_FAILED_LINE_RE = re.compile(r"\bFAILED tests/\S+")
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

GH_TIMEOUT_SECONDS = 60
LOG_TIMEOUT_SECONDS = 180
GIT_TIMEOUT_SECONDS = 20


@dataclass
class Verdict:
    state: str
    sha: str
    workflow: str
    detail: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]


def _run_key(run: dict[str, Any]) -> tuple[int, int]:
    return (int(run.get("run_number", 0)), int(run.get("run_attempt", 1)))


def _describe(run: dict[str, Any]) -> str:
    return (
        f"run {run.get('id')} #{run.get('run_number')} attempt {run.get('run_attempt', 1)} "
        f"status={run.get('status')} conclusion={run.get('conclusion')}"
    )


def failing_jobs(jobs_payload: dict[str, Any]) -> list[str]:
    names = []
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") in _FAILED_CONCLUSIONS:
            names.append(str(job.get("name")))
    return names


def classify(
    sha: str,
    workflow: str,
    runs_payload: Any,
    jobs_payload: Any = None,
    commit_message: str = "",
) -> Verdict:
    """Pure classification of one SHA's runs. Never raises on malformed input."""
    v = Verdict(CANNOT_MEASURE, sha, workflow)
    if not isinstance(runs_payload, dict) or not isinstance(
        runs_payload.get("workflow_runs"), list
    ):
        v.detail.append("malformed runs payload (no workflow_runs list)")
        return v
    all_runs = runs_payload["workflow_runs"]
    total = runs_payload.get("total_count")
    if not isinstance(total, int) or total < len(all_runs):
        v.detail.append(f"malformed total_count={total!r} for {len(all_runs)} runs")
        return v
    if total > len(all_runs):
        v.detail.append(f"truncated: total_count={total} but only {len(all_runs)} runs returned")
        return v
    try:
        mine = [
            r
            for r in all_runs
            if r.get("head_sha") == sha
            and str(r.get("path", "")).rsplit("/", 1)[-1].split("@", 1)[0] == workflow
        ]
        mine.sort(key=_run_key)
    except (AttributeError, TypeError, ValueError):
        v.detail.append("malformed run entry")
        return v

    if not mine:
        v.state = NO_RUN
        if _SKIP_CI_RE.search(commit_message or ""):
            v.detail.append("commit message carries a skip-ci marker: no CI run was ever created")
        else:
            v.detail.append(
                f"no {workflow} run exists for this SHA (not pushed, or not yet created)"
            )
        others = len(all_runs)
        if others:
            v.detail.append(
                f"{others} run(s) of other workflows exist for the SHA and were ignored"
            )
        return v

    newest = mine[-1]
    open_runs = [r for r in mine if r.get("status") in _OPEN_STATUSES]
    if open_runs:
        v.state = IN_PROGRESS
        for r in open_runs:
            v.detail.append(_describe(r))
        return v

    v.detail.append(_describe(newest))
    if len(mine) > 1:
        v.detail.append(
            f"{len(mine)} runs exist for this SHA; judged by the newest, earlier ones superseded: "
            + "; ".join(f"{r.get('id')}={r.get('conclusion')}" for r in mine[:-1])
        )
    if newest.get("status") != "completed":
        v.detail.append(f"unrecognised status {newest.get('status')!r}")
        return v

    conclusion = newest.get("conclusion")
    if conclusion == "success":
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, dict) else None
        if not isinstance(jobs, list) or not jobs:
            v.detail.append("success with no jobs visible: cannot confirm the run executed")
            return v
        v.state = SUCCESS
        v.detail.append(f"{len(jobs)} job(s) in the run")
    elif conclusion in _FAILED_CONCLUSIONS:
        v.state = FAILURE
        names = failing_jobs(jobs_payload) if isinstance(jobs_payload, dict) else []
        v.detail.append("failing jobs: " + (", ".join(names) if names else "(none visible)"))
    elif conclusion == "cancelled":
        v.state = CANCELLED
        v.detail.append(
            "cancelled and no newer run exists for this SHA (a superseded or concurrency-"
            "cancelled run, not a code verdict); re-run it or push the successor"
        )
    else:
        v.detail.append(f"conclusion {conclusion!r} is not treated as green")
    return v


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
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], epilog=epilog)
    p.add_argument("--sha", help="commit to check (default: git rev-parse HEAD)")
    p.add_argument("--workflow", default="ci.yml", help="workflow file name (default ci.yml)")
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
        verdict = classify(sha, args.workflow, runs, None, message)
        jobs = None
        newest = None
        if isinstance(runs, dict) and isinstance(runs.get("workflow_runs"), list):
            mine = [
                r
                for r in runs["workflow_runs"]
                if r.get("head_sha") == sha
                and str(r.get("path", "")).rsplit("/", 1)[-1].split("@", 1)[0] == args.workflow
            ]
            if mine:
                newest = max(mine, key=_run_key)
        if newest is not None and newest.get("status") == "completed":
            jobs = _gh_json(["api", f"repos/{repo}/actions/runs/{newest['id']}/jobs?per_page=100"])
            verdict = classify(sha, args.workflow, runs, jobs, message)
        tests: list[str] = []
        if verdict.state == FAILURE and newest is not None:
            try:
                log = _run(
                    ["gh", "run", "view", str(newest["id"]), "--log-failed"], LOG_TIMEOUT_SECONDS
                )
                tests = extract_failed_tests(log, args.max_failed_lines)
                if not tests:
                    verdict.detail.append(
                        f"no pytest FAILED lines in --log-failed ({len(log)} bytes); "
                        "read the failing job's log directly"
                    )
            except GhError as exc:
                verdict.detail.append(f"could not read --log-failed: {exc}")
            verdict.detail.extend(f"  {t}" for t in tests)
    except (GhError, KeyError, TypeError) as exc:
        verdict = Verdict(CANNOT_MEASURE, sha, args.workflow, [f"{type(exc).__name__}: {exc}"])
    print(f"CI_VERDICT: {verdict.state}  sha={verdict.sha}  workflow={verdict.workflow}")
    for line in verdict.detail:
        print(f"  {line}" if not line.startswith("  ") else line)
    return verdict.exit_code


if __name__ == "__main__":
    sys.exit(main())
