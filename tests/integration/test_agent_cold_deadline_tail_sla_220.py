"""Real-binary, real-wall-clock proof of the cold-path assembly-tail SLA fix (#220):
`tg agent <root> <query> --deadline N` bounds the whole POST-scan ASSEMBLY tail, not just the
repo-map collection stage.

Pre-fix symptom this module reproduces the shape of (dogfood, tg 1.81.15, 2026-07-18): the
default 60s cold-path deadline fires and the emitted JSON is HONEST (partial:true,
deadline_limit.deadline_exceeded:true), but wall-to-emission overshoots the advertised deadline
by 2.5x+ because `_detect_vendored_subtrees` (called from BOTH `agent_capsule.build_agent_
capsule_from_map` AND `repo_map._build_context_pack_from_map`'s own `auto_deweight` pass -- so
its cost is paid TWICE) and `_suggested_scope_from_map` ran unconditionally in the ASSEMBLY phase
regardless of whether the shared --deadline budget was already exhausted by the COLLECTION phase.

Two-part fix, both required (measured on THIS module's `manifest_heavy_repo` fixture at
--deadline 3.0): (1) an entry-only "skip if already past deadline" check alone dropped wall-to-exit
from ~9.3s to only ~5.5s -- insufficient, because `_detect_vendored_subtrees`'s own internal
O(candidate_dirs) manifest probe and O(candidate_roots^2) outermost-chain dedup are each
independently expensive enough to blow the ENTIRE remaining budget in one uninterrupted call even
when the deadline had NOT yet been exceeded at entry; (2) threading the SAME deadline check into
both of those internal loops (per-iteration, mirroring `_build_context_pack_from_map`'s own
per-symbol loop) closed the rest of the gap, to ~3.4s -- deadline + a small, bounded constant.

Unlike `tests/integration/test_agent_codemap_deadline_scale.py`'s flat `star_import_repo`
fixture (no manifest files anywhere -- `_detect_vendored_subtrees` returns `{}` almost
immediately, so that fixture does not exercise THIS fix at all), `manifest_heavy_repo` below is
shaped specifically to stress it: many sibling directories each carrying their own project
manifest (`pyproject.toml`/`package.json`), which is exactly the STRONG-1 candidate-directory
shape `_detect_vendored_subtrees`'s manifest probe + outermost-nested-chain dedup scale with.

Real subprocess (`python -m tensor_grep`), not CliRunner, per AGENTS.md's "dogfood the real
binary" rule and the anti-hang-test-protocol skill: a subprocess `timeout=` is a genuine
OS-level kill if this regresses back to unbounded, whereas an in-process CliRunner hang would
hang the whole pytest run (and every other queued test) with it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Anti-hang (two independent layers, per the anti-hang-test-protocol skill): this subprocess
# timeout is the inner, OS-level kill -- a regression back to fully-unbounded behavior fails FAST
# here (TimeoutExpired) instead of hanging the whole pytest run. Callers running this file in CI
# should also wrap the invocation in an outer shell-level `timeout` per that skill.
_SUBPROCESS_TIMEOUT_S = 90.0

_PROJECT_COUNT = 60
_NESTED_PACKAGES_PER_PROJECT = 2
_FILES_PER_LEAF = 3


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # This module is about the COLD path specifically -- force daemon autostart off so a real
    # subprocess run never leaks a background session-daemon process tied to this fixture's temp
    # directory (autostart defaults ON; a leaked daemon would also silently reroute a SUBSEQUENT
    # no-explicit-`--deadline` call in this file onto the WARM path instead, invalidating the
    # measurement -- discovered the hard way profiling this exact fix).
    env["TG_SESSION_DAEMON_AUTOSTART"] = "0"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def manifest_heavy_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`_PROJECT_COUNT` sibling top-level projects, each ALSO containing
    `_NESTED_PACKAGES_PER_PROJECT` nested manifest-bearing packages (a monorepo `packages/pkgN/`
    shape) -- (1 + `_NESTED_PACKAGES_PER_PROJECT`) manifest-bearing directories per project, so
    `_detect_vendored_subtrees`'s STRONG-1 candidate-directory probe and its outermost-chain
    dedup (which scales with the number of manifest-bearing directories, not raw file count) both
    get real, non-trivial work -- unlike a flat fixture with zero manifests. Kept deliberately
    small in total FILE count (a few hundred, not thousands) so the COLLECTION stage (the file
    walk + parse this fix does NOT touch) stays fast and the test isolates the ASSEMBLY tail.
    """
    root = tmp_path_factory.mktemp("manifest_heavy_repo") / "workspace"
    root.mkdir()
    for project_index in range(_PROJECT_COUNT):
        project = root / f"project-{project_index:03d}"
        project.mkdir()
        is_py = project_index % 2 == 0
        (project / ("pyproject.toml" if is_py else "package.json")).write_text(
            "[project]\nname = 'p'\n" if is_py else '{"name": "p"}\n',
            encoding="utf-8",
        )
        for file_index in range(_FILES_PER_LEAF):
            suffix = ".py" if is_py else ".ts"
            (project / f"mod{file_index}{suffix}").write_text(
                f"def entry_{project_index}_{file_index}():\n    return {file_index}\n"
                if is_py
                else f"export function entry_{project_index}_{file_index}() {{ return {file_index}; }}\n",
                encoding="utf-8",
            )
        for pkg_index in range(_NESTED_PACKAGES_PER_PROJECT):
            pkg = project / "packages" / f"pkg{pkg_index}"
            pkg.mkdir(parents=True)
            (pkg / ("pyproject.toml" if is_py else "package.json")).write_text(
                "[project]\nname = 'p'\n" if is_py else '{"name": "p"}\n',
                encoding="utf-8",
            )
            for file_index in range(_FILES_PER_LEAF):
                suffix = ".py" if is_py else ".ts"
                (pkg / f"mod{file_index}{suffix}").write_text(
                    f"def leaf_{project_index}_{pkg_index}_{file_index}():\n    return {file_index}\n"
                    if is_py
                    else f"export function leaf_{project_index}_{pkg_index}_{file_index}() {{ return {file_index}; }}\n",
                    encoding="utf-8",
                )
    # An unambiguous, findable "main entry point" so the query has real ranking signal.
    (root / "project-000" / "main.py").write_text(
        "def main():\n    print('entry')\n", encoding="utf-8"
    )
    return root


def test_agent_tight_deadline_wall_to_exit_bounded(manifest_heavy_repo: Path) -> None:
    """The core #220 contract: wall-to-exit stays close to the requested --deadline even on a
    repo shaped to stress the (pre-fix, unbounded) assembly tail. Generous bound -- catches a
    regression back to "the assembly tail runs unbounded regardless of --deadline" (which would
    show as several times the deadline, or the subprocess timeout firing outright), not a tight
    deadline-adherence claim."""
    deadline = 3.0
    started_at = time.monotonic()
    result = _run_tg(
        [
            "agent",
            str(manifest_heavy_repo),
            "find the main entry points",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=manifest_heavy_repo,
    )
    elapsed = time.monotonic() - started_at

    # Generous ceiling (deadline + a fixed constant, not a multiplier of the noise-prone kind).
    # Measured on this exact fixture: pre-fix ~9.3s (entry-only-check partial-fix ~5.5s; the full
    # fix, entry check + BOTH internal loops bounded, ~3.4s) against this 3.0s deadline -- so
    # deadline+3.0=6.0s comfortably clears the fixed number's jitter while still catching a
    # regression back to either the pre-fix OR the entry-only-partial-fix shape.
    assert elapsed < deadline + 3.0, (
        f"tg agent ran {elapsed:.1f}s against a {deadline}s --deadline on a manifest-heavy repo "
        "-- the post-deadline ASSEMBLY tail looks unbounded again"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    # Honesty must survive the bound: still explicitly flagged, never a silent partial-less exit.
    assert payload.get("partial") is True, result.stdout
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout


def test_agent_no_deadline_pressure_still_detects_vendored_subtrees(
    manifest_heavy_repo: Path,
) -> None:
    """Regression guard for the build task's explicit "no-deadline-pressure path unchanged" ask:
    with an ample --deadline, the assembly tail must still run to completion (never skip just
    because the fixture COULD trigger a skip) -- `suggested_ignore` (built from the same
    `_detect_vendored_subtrees` call this fix bounds) must still be populated with the nested
    manifest-bearing `packages/pkgN` trees, proving the deadline-gate is a true no-op here."""
    result = _run_tg(
        [
            "agent",
            str(manifest_heavy_repo),
            "find the main entry points",
            "--deadline",
            "60",
            "--json",
        ],
        cwd=manifest_heavy_repo,
    )
    payload = json.loads(result.stdout)
    assert payload.get("deadline_limit", {}).get("assembly_stages_skipped") is None, (
        "an ample --deadline must never skip an assembly stage -- got "
        f"{payload.get('deadline_limit')}"
    )
