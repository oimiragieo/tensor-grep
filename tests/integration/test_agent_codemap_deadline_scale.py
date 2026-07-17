"""Real-binary, real-wall-clock proof of dogfood finding 1 (HIGH): `tg agent`/`tg codemap`
(and, for free, `tg edit-plan`) bound the WHOLE-repo path through --deadline, not just the
initial repo-map scan, and stamp a fail-closed `partial` on truncation instead of silently
reporting exit 0.

Pre-fix symptom this module reproduces the shape of: `--deadline` threaded into
``build_repo_map`` (the scan), which finished in budget, but the POST-MAP stages (context-pack
graph/symbol scoring shared by agent/context/edit-plan; the folders_with_no_mapped_files re-walk
+ per-folder render loop for codemap) ran fully UNBOUNDED and UNSTAMPED -- `tg agent ROOT Q
--deadline 8` ran ~20s at exit 0, partial=None (a silent deadline breach); `tg codemap ROOT
--deadline 3` ran ~28s.

Real subprocess (`python -m tensor_grep`), not CliRunner, per AGENTS.md's "dogfood the real
binary" rule and the anti-hang-test-protocol skill: a subprocess `timeout=` is a genuine
OS-level kill if a fix regresses back to unbounded, whereas an in-process CliRunner hang would
hang the whole pytest run (and every other queued test) with it.

KNOWN, OUT-OF-SCOPE FINDING (documented here, not fixed by this PR -- see the module docstring
of ``src/tensor_grep/cli/agent_capsule.py``'s ``_collect_capsule_call_site_evidence_from_map``
call chain and ``codemap.py``'s ``_exclude_output_paths``): profiling this fixture at ~2000
files surfaced a SEPARATE, pre-existing unbounded cost dominated by repeated ``Path.resolve()``
(Windows ``nt._getfinalpathname``) calls -- `agent`'s validation-file/test-runner detection
(``_precomputed_validation_files_for_root``, reached via the call-site-evidence collector) and
`codemap`'s output-path exclusion (``_exclude_output_paths``). Neither is named in this PR's
scope (``_personalized_reverse_import_pagerank``, ``_collect_outbound_dependencies``, the
codemap tail, the F4 60s default) and neither threads a deadline at all. Because of it, the
WALL-CLOCK assertions below are deliberately generous (they catch a genuine hang/regression to
fully-unbounded, not a tight deadline-adherence claim this PR cannot back) -- what IS tightly
proven, and is this PR's actual contract, is that a truncated run is HONESTLY reported
(exit 2, partial/partial_reason, never a silent exit-0 completeness lie).
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
# timeout is the inner, OS-level kill -- a regression back to fully-unbounded behavior fails
# FAST here (TimeoutExpired) instead of hanging the whole pytest run. Callers running this file
# in CI should also wrap the invocation in an outer shell-level `timeout` per that skill.
_SUBPROCESS_TIMEOUT_S = 90.0

_FOLDER_COUNT = 200
_FILES_PER_FOLDER = 10
_TOTAL_FILES = _FOLDER_COUNT * _FILES_PER_FOLDER + 1  # +1 for hub.py


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # None of these tests are about the warm-daemon path (that is
    # test_orient_agent_daemon.py / test_cli_deadline_coverage_gaps.py's job) -- force it off so
    # a real subprocess run never autostarts a background daemon process here (autostart
    # defaults ON; leaving it on would leak a stray process per test run and add nondeterminism).
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
def star_import_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """~2000 files (per the plan's RED-test ask) across 200 folders, with a "star" import
    topology: every leaf imports the same ``hub.py`` (dense reverse-import fan-in -- hub.py's
    reverse-importer set has ~2000 entries, the worst case for pagerank's pre-fix per-node
    ``sorted(reverse_importers.get(current))`` recomputed 12x, which the council fix hoists
    once). Only the first 3 leaves actually CALL ``shared_target`` (the query target below) --
    keeps the query's own caller-scan cheap (few real callers) while `hub.py`'s IMPORT fan-in
    (a FILE-level edge, independent of which specific name was imported) stays maximal, so this
    fixture stresses pagerank specifically rather than an unrelated caller-enumeration cost.
    """
    root = tmp_path_factory.mktemp("star_import_repo") / "project"
    root.mkdir()
    (root / "hub.py").write_text(
        "def common_util(value):\n    return value\n\n\n"
        "def shared_target(value):\n    return common_util(value)\n",
        encoding="utf-8",
    )
    index = 0
    for folder_index in range(_FOLDER_COUNT):
        folder = root / f"pkg{folder_index:04d}"
        folder.mkdir()
        for _file_index in range(_FILES_PER_FOLDER):
            target_fn = "shared_target" if index < 3 else "common_util"
            (folder / f"m{index:05d}.py").write_text(
                f"from hub import {target_fn}\n\n\n"
                f"def leaf_{index}():\n    return {target_fn}({index})\n",
                encoding="utf-8",
            )
            index += 1
    return root


# ---------------------------------------------------------------------------------------------
# `tg agent`: a tight --deadline must terminate the WHOLE post-map path honestly (exit 2,
# partial=True), not silently exit 0 with partial=None (the pre-fix "~20s exit 0" symptom).
# ---------------------------------------------------------------------------------------------


def test_agent_tight_deadline_exits_2_with_partial_true(star_import_repo: Path) -> None:
    # Deliberately at the CLI's own enforced floor (`--deadline` has `min=0.1` on every command
    # that defines it -- see main.py): OS file-cache warmth AND shared-CI-runner speed both vary
    # run-to-run (a repeat run against the same fixture directory can be several times faster
    # than a cold-cache run, and a lightly-loaded runner can be several times faster than a
    # loaded one), so a "moderately tight" budget was observed to be flaky in CI -- run
    # 29547883118 completed this fixture's WHOLE post-map path (scan + capsule render + call-site
    # evidence) in under 500ms on an ubuntu-latest/macos-latest py3.11 runner (exit 0, no
    # truncation) while the same workload took ~3.6s on a separate ubuntu-latest py3.12 runner the
    # same run. 0.5s was not tight enough; 0.1s is the tightest budget the CLI accepts and is
    # crossed deterministically regardless of cache state or runner speed.
    deadline = 0.1
    started_at = time.monotonic()
    result = _run_tg(
        [
            "agent",
            str(star_import_repo),
            "shared_target",
            # Raised well above _TOTAL_FILES so the file-COUNT scan_limit ceiling never fires --
            # this test is isolating the DEADLINE mechanism, not the pre-existing file-cap one.
            "--max-repo-files",
            "5000",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=star_import_repo,
    )
    elapsed = time.monotonic() - started_at

    # Generous, NOT a tight deadline-adherence claim (see module docstring's documented,
    # out-of-scope Path.resolve() finding) -- this catches a genuine regression to fully
    # unbounded (which would run into the subprocess timeout above and raise TimeoutExpired, an
    # even louder failure), while tolerating the known separate overhead.
    assert elapsed < 60.0, (
        f"tg agent ran {elapsed:.1f}s against a {deadline}s --deadline on a "
        f"{_TOTAL_FILES}-file repo -- looks fully unbounded, not just slow"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    # THE fix's actual contract: never silently report exit 0 / partial=None on a truncated run.
    assert payload.get("partial") is True, result.stdout
    # Not an exact-shape check: whichever stage crossed the deadline first (the SCAN itself, at
    # this tight a budget, most likely) stamps its OWN (possibly richer) deadline_limit shape --
    # `build_context_pack_from_map`'s self-stamp only originates the generic
    # `{"deadline_exceeded": True}` shape via setdefault, deliberately never clobbering a
    # richer one already present. Either way this key must be True.
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout
    assert payload.get("result_incomplete") is True, result.stdout


def test_agent_default_deadline_still_completes_and_is_bounded(star_import_repo: Path) -> None:
    """F4: even with NO explicit --deadline, the new 60s cold-path default must still let a
    real (if unusually dense) ~2000-file repo finish -- proving the default isn't so tight it
    breaks a legitimate whole-repo call, while still being a real, finite bound (never the
    pre-existing fully-unbounded behavior)."""
    started_at = time.monotonic()
    result = _run_tg(
        ["agent", str(star_import_repo), "shared_target", "--max-repo-files", "5000", "--json"],
        cwd=star_import_repo,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 60.0, f"tg agent (default --deadline) ran {elapsed:.1f}s -- expected <60s"
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is not True, (
        "a real run finishing on its own should not ALSO claim a deadline cutoff"
    )


# ---------------------------------------------------------------------------------------------
# `tg codemap`: same honesty contract for the MAP-level scan AND the post-map tail
# (folders_with_no_mapped_files re-walk + per-folder render loop).
# ---------------------------------------------------------------------------------------------


def test_codemap_tight_deadline_exits_2_with_partial_reason_deadline(
    star_import_repo: Path,
) -> None:
    # At the CLI's own enforced floor (min=0.1) for the same cache-warmth-and-runner-speed
    # determinism reason as the agent test above.
    deadline = 0.1
    out_dir = star_import_repo / "docs" / "code-map"
    started_at = time.monotonic()
    result = _run_tg(
        [
            "codemap",
            str(star_import_repo),
            "--out",
            str(out_dir),
            "--max-repo-files",
            "5000",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=star_import_repo,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 60.0, (
        f"tg codemap ran {elapsed:.1f}s against a {deadline}s --deadline -- looks fully "
        "unbounded, not just slow"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True, result.stdout
    assert payload.get("partial_reason") == "deadline", result.stdout
    assert payload.get("remediation"), result.stdout
    # Still a VALID, browsable (partial) map -- the tail bounding must never corrupt output.
    assert Path(payload["index"]).is_file()


def test_codemap_default_no_deadline_flag_completes(star_import_repo: Path) -> None:
    """Golden-parity companion: `tg codemap`'s OWN CLI default (#153, DEFAULT_CLI_DEADLINE_
    SECONDS=60.0, pre-existing and unchanged by this PR) must still comfortably complete this
    fixture -- a regression tightening it would show up here as an unexpected partial=True."""
    out_dir = star_import_repo / "docs" / "code-map-default"
    started_at = time.monotonic()
    result = _run_tg(
        [
            "codemap",
            str(star_import_repo),
            "--out",
            str(out_dir),
            "--max-repo-files",
            "5000",
            "--json",
        ],
        cwd=star_import_repo,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 60.0, f"tg codemap (default --deadline) ran {elapsed:.1f}s -- expected <60s"
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is False, result.stdout


# ---------------------------------------------------------------------------------------------
# `tg edit-plan`: SCOPE per the plan -- edit-plan flows through the SAME shared
# `_build_context_pack_from_map` seam agent/context do (`repo_map.build_context_edit_plan_
# from_map` -> `build_context_pack_from_map`), so it inherits the pagerank + self-stamp fix
# "for free". Proven here with its own exit-2 test rather than left as an unverified assumption.
# ---------------------------------------------------------------------------------------------


def test_edit_plan_tight_deadline_exits_2_with_partial_true_for_free(
    star_import_repo: Path,
) -> None:
    # At the CLI's own enforced floor (min=0.1) for the same cache-warmth-and-runner-speed
    # determinism reason as the agent test above -- this test in particular runs LAST in the
    # module, by which point every file in the fixture has already been read (and OS-cached)
    # repeatedly by the earlier agent/codemap tests, so a multi-second deadline that reliably
    # crossed on a cold cache was observed NOT to cross here.
    deadline = 0.1
    started_at = time.monotonic()
    result = _run_tg(
        [
            "edit-plan",
            str(star_import_repo),
            "shared_target",
            "--max-repo-files",
            "5000",
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=star_import_repo,
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 60.0, (
        f"tg edit-plan ran {elapsed:.1f}s against a {deadline}s --deadline -- looks fully "
        "unbounded, not just slow"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("partial") is True, result.stdout
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout
