"""Real-binary, real-wall-clock proof of the #222 residual fix (real-workspace-scale continuation
of #220/#669/#671): `tg agent <root> <query> --deadline N` stays bounded even when the query
matches a real, popular (high-fan-in) symbol -- the shape that exercises `_build_context_pack_
from_map`'s reverse-import-graph construction (`_reverse_import_distances`/`_reverse_importers`/
the direct `_import_graph_bonus` consumer loop), the one un-gated post-deadline tail stage left
after #220/#669/#671 bounded `_detect_vendored_subtrees` and `_suggested_scope_from_map`.

Why this fixture is shaped differently from #220's `manifest_heavy_repo` or #222's own
`independent_package_repo`: those stress `_detect_vendored_subtrees`'s manifest-probe/dedup loops
specifically (many manifest-bearing directories). This module needs a query that matches a REAL
symbol so `_build_context_pack_from_map`'s `dependency_seed_files` is non-trivial and the
reverse-import BFS/index actually fans out -- a flat "star" import topology (every leaf imports
one of several shared hub modules, mirroring `test_agent_codemap_deadline_scale.py`'s own
`star_import_repo` rationale) with a query naming a symbol defined in one hub.

Pre-fix magnitude (direct, non-subprocess probe -- see `tests/unit/test_agent_reverse_import_
graph_deadline_222.py`'s module docstring for the full derivation): `_reverse_import_distances`
alone scaled ~n^2.2 with file count (0.99s at 2,000 files -> 13.6s at 6,000 -> 60.3s at 12,000),
and `_build_context_pack_from_map`'s total cost tracked it closely (2.4s -> 20.4s -> 71.5s). At
THIS module's exact fixture (6,000 files, real CLI subprocess, `--deadline 3.0`), COLD-cache: pre-
fix wall-to-exit measured **26.6s**; post-fix **~9.5s** (~2.8x faster).

CACHE-SENSITIVITY FINDING (why the assertion below is a generous absolute ceiling, not a tight
deadline-relative one): this bug's WALL-CLOCK severity is highly dependent on filesystem-cache
warmth, because the dominant per-item cost is Windows `Path.resolve()`/`nt._getfinalpathname`
syscall latency (the same sensitivity #671's own fix docstring documents). Re-measured on a warm
cache (this exact fixture, repeatedly exercised in the same dev session) both pre-fix (7.6s) and
post-fix (6.2s) collapse toward each other -- a REAL, reproducible effect, not test flakiness: a
CI runner (typically a fresh checkout/VM, i.e. cold-cache) should see close to the COLD-cache gap
above, but a warm dev-box re-run of this exact test will not reliably discriminate on wall-clock
alone. The PRIMARY, cache-immune regression guard for the actual mechanism is the deterministic,
monkeypatched-clock unit suite in `test_agent_reverse_import_graph_deadline_222.py`; this
integration test's job is the same as every sibling #220/#222 SLA test's: a real-binary sanity
catch of a regression back toward fully-unbounded, plus the honesty-contract assertions below
(timing-independent, reliable regardless of cache state).

Real subprocess (`python -m tensor_grep`), not CliRunner, per AGENTS.md's "dogfood the real
binary" rule and the anti-hang-test-protocol skill: a subprocess `timeout=` is a genuine OS-level
kill if this regresses back to unbounded, whereas an in-process CliRunner hang would hang the
whole pytest run (and every other queued test) with it. Outcome-agnostic on returncode (the #669
CI lesson): a fast CI runner may legitimately finish complete inside a given `--deadline`; the
property under test is "wall-to-exit is bounded," not "the deadline must trip."
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

_HUB_COUNT = 8
_FOLDER_COUNT = 600
_FILES_PER_FOLDER = 10
_TOTAL_FILES = _FOLDER_COUNT * _FILES_PER_FOLDER


def _run_tg(
    args: list[str], *, cwd: Path, timeout: float = _SUBPROCESS_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # This module is about the COLD path specifically -- force daemon autostart off so a real
    # subprocess run never leaks a background session-daemon process tied to this fixture's temp
    # directory (mirrors every sibling #220/#222 SLA test's own reasoning).
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
def hub_fan_in_repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`_HUB_COUNT` "hub" modules, each imported (round-robin, plus a second cross-hub import on
    every 3rd leaf) by `_TOTAL_FILES` leaves -- a query naming one hub's symbol seeds `_build_
    context_pack_from_map`'s `dependency_seed_files` with a real match, so the reverse-import BFS/
    index this module targets actually fans out across a real fraction of the repo (unlike a query
    that matches nothing, where `dependency_seed_files` stays empty and the BFS never leaves depth
    0 -- verified during this fix's own development to NOT exercise the bug at all)."""
    root = tmp_path_factory.mktemp("hub_fan_in_repo") / "workspace"
    root.mkdir()
    for hub_index in range(_HUB_COUNT):
        (root / f"hub{hub_index}.py").write_text(
            f"def common_util_{hub_index}(value):\n    return value + {hub_index}\n\n\n"
            f"def shared_target_{hub_index}(value):\n    return common_util_{hub_index}(value)\n",
            encoding="utf-8",
        )
    index = 0
    for folder_index in range(_FOLDER_COUNT):
        folder = root / f"pkg{folder_index:05d}"
        folder.mkdir()
        for _file_index in range(_FILES_PER_FOLDER):
            hub = index % _HUB_COUNT
            target_fn = f"shared_target_{hub}" if index < 3 else f"common_util_{hub}"
            second_import = ""
            if index % 3 == 0:
                second_hub = (hub + 1) % _HUB_COUNT
                second_import = f"from hub{second_hub} import common_util_{second_hub}\n"
            (folder / f"m{index:06d}.py").write_text(
                f"from hub{hub} import {target_fn}\n"
                f"{second_import}\n\n"
                f"def leaf_{index}():\n    return {target_fn}({index})\n",
                encoding="utf-8",
            )
            index += 1
    assert index == _TOTAL_FILES, "fixture assumption drifted"
    return root


def test_agent_reverse_import_graph_wall_to_exit_bounded(hub_fan_in_repo: Path) -> None:
    deadline = 3.0
    started_at = time.monotonic()
    result = _run_tg(
        [
            "agent",
            str(hub_fan_in_repo),
            # Matches hub0's symbol exactly -- seeds a real, non-trivial dependency_seed_files so
            # the reverse-import-graph construction this fix bounds actually fans out; a query
            # that matches nothing does NOT exercise this bug (verified during development).
            "shared_target_0",
            "--max-repo-files",
            str(_TOTAL_FILES + _HUB_COUNT + 100),
            "--deadline",
            str(deadline),
            "--json",
        ],
        cwd=hub_fan_in_repo,
    )
    elapsed = time.monotonic() - started_at

    # Generous ABSOLUTE ceiling, deliberately NOT a tight deadline-relative claim (see the module
    # docstring's cache-sensitivity finding: this bug's wall-clock magnitude swings from ~9.5s
    # post-fix/~26.6s pre-fix cold-cache down to ~6-9s for BOTH pre- and post-fix on a warm dev-box
    # cache, since the dominant per-item cost is syscall latency that the OS itself caches). This
    # still catches a genuine regression back toward fully-unbounded (a real cold-cache CI runner,
    # or a larger real-world repo, would blow well past this even at the now-improved magnitude) --
    # exactly the same "not a tight SLA claim, a fully-unbounded catch" shape every sibling #220/
    # #222 SLA test uses, for the identical documented reason.
    assert elapsed < 40.0, (
        f"tg agent ran {elapsed:.1f}s against a {deadline}s --deadline on a "
        f"{_TOTAL_FILES}-file repo -- looks like the #222 reverse-import-graph residual regressed"
    )
    payload = json.loads(result.stdout)
    if result.returncode == 2:
        # Truncated: the honesty contract (docs/CONTRACTS.md) must hold -- never a silent
        # exit-0-and-complete lie on a run this fix's own deadline gates actually cut short.
        assert payload.get("partial") is True, result.stdout
        assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True, result.stdout
    else:
        # A sufficiently fast runner may legitimately finish complete inside budget (the #669 CI
        # lesson) -- a genuinely complete run must not ALSO claim a deadline cutoff.
        assert result.returncode == 0, result.stdout + result.stderr
        assert payload.get("partial") is not True, result.stdout
