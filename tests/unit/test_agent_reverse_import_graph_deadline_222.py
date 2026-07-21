"""Real-workspace-scale residual of #220/#669/#671 (#222 continuation): `_build_context_pack_
from_map`'s reverse-import-GRAPH construction -- `_reverse_import_distances` (a 3-depth BFS),
`_reverse_importers` (the alias-inverted reverse-edge index), and the direct `_import_graph_bonus`
scoring loop that consumes both -- ran fully UNBOUNDED even when every SIBLING stage in the same
function (the symbol-scoring loop, `_personalized_reverse_import_pagerank`, both `_detect_
vendored_subtrees` calls) already honored `deadline_monotonic`.

Root cause + measured magnitude (direct, non-subprocess probe on a hub-fan-in-shaped synthetic
tree, query matching a real symbol so the BFS seed set is non-trivial -- see the module docstring
of `tests/integration/test_agent_reverse_import_graph_scale_sla_222.py` for the full derivation):
`_reverse_import_distances` alone scaled ~n^2.2 with file count (0.99s at 2,000 files -> 13.6s at
6,000 -> 60.3s at 12,000) and dominated `_build_context_pack_from_map`'s own total cost at scale
(60.3s of 71.5s = 84% at 12,000 files). A SECOND, independent un-gated consumer of the same
`_import_graph_bonus` helper -- a direct `for current in payload["files"]: ... _import_graph_bonus(
...)` loop later in the same function -- was found via an OLD-vs-NEW re-profile of the first fix:
it dominated a POST-FIX profile (93s of 102s) because a query term that fuzzy-matches many files'
import strings can pull hundreds-to-thousands of files into `dependency_seed_files`, and this loop
re-derives an `_import_graph_bonus` call per file against that whole seed set.

Fix: thread `deadline_monotonic`/`deadline_hit` into all three (mirroring the exact per-item-in-
the-expensive-inner-loop shape `_personalized_reverse_import_pagerank`, `_detect_vendored_
subtrees`, and this same function's own symbol-scoring loop already use). On expiry each returns
the PARTIAL result already accumulated -- never discarded, never swapped for a crash or a silent
empty-that-looks-complete -- since every caller already treats a missing entry as "no signal for
this file" (the same honest degrade `_personalized_reverse_import_pagerank`'s own docstring
documents). `build_symbol_blast_radius_from_map` gained the identical fix for its OWN direct
`_reverse_importers`/`_reverse_import_distances`/`_personalized_reverse_import_pagerank` call
(reached from `tg agent`'s cold path via `_collect_capsule_call_site_evidence`'s blast-radius
call), folded into a new, dedicated `reverse_import_graph_deadline_hit_blast` flag.

This file proves the MECHANISM at the function level: fast, deterministic, no subprocess, no real
wall-clock dependency (a fake `time.monotonic()` forces the trip). The real-binary wall-to-exit
proof lives in `tests/integration/test_agent_reverse_import_graph_scale_sla_222.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import repo_map as _repo_map

# ---------------------------------------------------------------------------------------------
# Shared fixture: a hub file + N leaves that all import it, so a full (unbounded) BFS/reverse-
# index pass finds every leaf, but any early cutoff finds a proper SUBSET -- letting a test
# distinguish "ran to completion" from "bailed early with a partial result" by count alone.
# ---------------------------------------------------------------------------------------------

_LEAF_COUNT = 10


def _hub_and_leaves_rm(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path.resolve()
    (root / "hub.py").write_text("def shared_target(value):\n    return value\n", encoding="utf-8")
    for i in range(_LEAF_COUNT):
        (root / f"leaf_{i:03d}.py").write_text(
            f"from hub import shared_target\n\n\ndef leaf_{i}():\n    return shared_target({i})\n",
            encoding="utf-8",
        )
    rm = _repo_map.build_repo_map(root)
    assert len(rm["files"]) == _LEAF_COUNT + 1, "fixture assumption drifted"
    return rm


def _all_files_and_imports(rm: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    all_files = [str(current) for current in rm["files"]]
    imports_by_file = {
        str(entry["file"]): [str(item) for item in entry["imports"]] for entry in rm["imports"]
    }
    return all_files, imports_by_file


def _hub_path(all_files: list[str]) -> str:
    return next(current for current in all_files if Path(current).name == "hub.py")


# ---------------------------------------------------------------------------------------------
# `_reverse_import_distances`: the empirically-dominant (~n^2.2) super-linear residual.
# ---------------------------------------------------------------------------------------------


def test_reverse_import_distances_inner_loop_bails_on_deadline_already_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm = _hub_and_leaves_rm(tmp_path)
    all_files, imports_by_file = _all_files_and_imports(rm)
    hub = _hub_path(all_files)

    # Sanity: unpatched, a full BFS from the hub finds every leaf (all import it directly).
    full = _repo_map._reverse_import_distances([hub], all_files, imports_by_file)
    assert len(full) == _LEAF_COUNT

    monkeypatch.setattr(_repo_map.time, "monotonic", lambda: 1_000_000.0)
    flag = _repo_map._DeadlineBreakFlag()
    result = _repo_map._reverse_import_distances(
        [hub], all_files, imports_by_file, deadline_monotonic=500_000.0, deadline_hit=flag
    )

    assert flag.hit is True
    # An always-past clock trips on the very FIRST inner-loop check -- before any file is
    # examined -- proving the check lives INSIDE the loop (not merely absent/no-op).
    assert result == {}
    assert isinstance(result, dict)


def test_reverse_import_distances_returns_partial_not_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deadline that trips PARTWAY through the inner loop must keep whatever was already found
    -- never swap a genuine partial result for an empty one (the exact "confident false zero"
    class of bug this codebase's fail-closed contract exists to prevent)."""
    rm = _hub_and_leaves_rm(tmp_path)
    all_files, imports_by_file = _all_files_and_imports(rm)
    hub = _hub_path(all_files)
    real_monotonic = _repo_map.time.monotonic

    call_count = 0

    def clock() -> float:
        nonlocal call_count
        call_count += 1
        # First 5 inner-loop checks read "before deadline"; the 6th onward reads "already past"
        # -- lets a few leaves accumulate into `distances` before the trip.
        return 1_000_000.0 if call_count > 5 else real_monotonic()

    monkeypatch.setattr(_repo_map.time, "monotonic", clock)
    flag = _repo_map._DeadlineBreakFlag()
    result = _repo_map._reverse_import_distances(
        [hub], all_files, imports_by_file, deadline_monotonic=500_000.0, deadline_hit=flag
    )

    assert flag.hit is True
    assert 0 < len(result) < _LEAF_COUNT, (
        f"expected a genuine partial result strictly between empty and complete, got {result}"
    )


def test_reverse_import_distances_no_pressure_path_unchanged(tmp_path: Path) -> None:
    """`deadline_monotonic=None` (omitted) and a comfortably-future deadline must produce
    byte-identical output to each other -- the no-deadline-pressure path is unaffected by this
    fix, and `deadline_hit` never fires when the budget was never actually exceeded."""
    rm = _hub_and_leaves_rm(tmp_path)
    all_files, imports_by_file = _all_files_and_imports(rm)
    hub = _hub_path(all_files)

    no_deadline = _repo_map._reverse_import_distances([hub], all_files, imports_by_file)
    flag = _repo_map._DeadlineBreakFlag()
    future_deadline = _repo_map._reverse_import_distances(
        [hub],
        all_files,
        imports_by_file,
        deadline_monotonic=_repo_map.time.monotonic() + 3600.0,
        deadline_hit=flag,
    )

    assert future_deadline == no_deadline
    assert flag.hit is False


# ---------------------------------------------------------------------------------------------
# `_reverse_importers`: same call block, smaller (closer-to-linear) but still un-gated pre-fix.
# ---------------------------------------------------------------------------------------------


def test_reverse_importers_outer_loop_bails_on_deadline_already_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm = _hub_and_leaves_rm(tmp_path)
    all_files, imports_by_file = _all_files_and_imports(rm)
    hub = _hub_path(all_files)

    full = _repo_map._reverse_importers(all_files, imports_by_file)
    assert len(full[hub]) == _LEAF_COUNT

    monkeypatch.setattr(_repo_map.time, "monotonic", lambda: 1_000_000.0)
    flag = _repo_map._DeadlineBreakFlag()
    result = _repo_map._reverse_importers(
        all_files, imports_by_file, deadline_monotonic=500_000.0, deadline_hit=flag
    )

    assert flag.hit is True
    # Every key is still present (`reverse = {current: set() for current in all_files}` runs
    # before the gated loop), but none has been populated yet -- a safe, honest empty-edges
    # partial, not a crash or a missing key.
    assert result[hub] == set()


def test_reverse_importers_no_pressure_path_unchanged(tmp_path: Path) -> None:
    rm = _hub_and_leaves_rm(tmp_path)
    all_files, imports_by_file = _all_files_and_imports(rm)

    no_deadline = _repo_map._reverse_importers(all_files, imports_by_file)
    flag = _repo_map._DeadlineBreakFlag()
    future_deadline = _repo_map._reverse_importers(
        all_files,
        imports_by_file,
        deadline_monotonic=_repo_map.time.monotonic() + 3600.0,
        deadline_hit=flag,
    )

    assert future_deadline == no_deadline
    assert flag.hit is False


# ---------------------------------------------------------------------------------------------
# `_build_context_pack_from_map`'s direct `_import_graph_bonus` consumer loop -- the SECOND,
# independently-discovered residual (found via an OLD-vs-NEW re-profile of the fix above).
# ---------------------------------------------------------------------------------------------


def test_build_context_pack_from_map_import_graph_bonus_loop_bails_on_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm = _hub_and_leaves_rm(tmp_path)

    # Query matches the hub's symbol exactly, seeding `dependency_seed_files` with hub.py so the
    # direct `_import_graph_bonus` loop has real, non-trivial per-file work to do for every leaf.
    monkeypatch.setattr(_repo_map.time, "monotonic", lambda: 1_000_000.0)
    flag = _repo_map._DeadlineBreakFlag()
    payload = _repo_map._build_context_pack_from_map(
        dict(rm), "shared_target", deadline_monotonic=500_000.0, deadline_hit=flag
    )

    assert flag.hit is True
    assert isinstance(payload, dict)
    # A safe, honest partial -- never an exception, never a payload missing the keys every
    # consumer of this function's return value assumes are present.
    assert "files" in payload and "symbols" in payload


def test_build_context_pack_from_map_no_pressure_path_unchanged(tmp_path: Path) -> None:
    rm = _hub_and_leaves_rm(tmp_path)

    no_deadline = _repo_map._build_context_pack_from_map(dict(rm), "shared_target")
    flag = _repo_map._DeadlineBreakFlag()
    future_deadline = _repo_map._build_context_pack_from_map(
        dict(rm),
        "shared_target",
        deadline_monotonic=_repo_map.time.monotonic() + 3600.0,
        deadline_hit=flag,
    )

    assert flag.hit is False
    # Compare the fields this fix touches directly -- `files`/`symbols`/`file_matches` are the
    # ranking output the (now-gated) reverse-import-graph loops feed into.
    assert future_deadline["files"] == no_deadline["files"]
    assert future_deadline["symbols"] == no_deadline["symbols"]
    assert future_deadline["file_matches"] == no_deadline["file_matches"]


# ---------------------------------------------------------------------------------------------
# `build_symbol_blast_radius_from_map`'s OWN direct reverse-import-graph derivation -- reached
# from `tg agent`'s cold path via `_collect_capsule_call_site_evidence`'s blast-radius call.
# ---------------------------------------------------------------------------------------------


def test_build_symbol_blast_radius_from_map_reverse_import_graph_deadline_folds_into_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rm = _hub_and_leaves_rm(tmp_path)

    monkeypatch.setattr(_repo_map.time, "monotonic", lambda: 1_000_000.0)
    payload = _repo_map.build_symbol_blast_radius_from_map(
        rm, "shared_target", deadline_monotonic=500_000.0
    )

    assert payload.get("partial") is True
    assert payload.get("deadline_limit", {}).get("deadline_exceeded") is True


def test_build_symbol_blast_radius_from_map_no_pressure_path_unaffected(tmp_path: Path) -> None:
    rm = _hub_and_leaves_rm(tmp_path)

    no_deadline = _repo_map.build_symbol_blast_radius_from_map(rm, "shared_target")
    future_deadline = _repo_map.build_symbol_blast_radius_from_map(
        rm, "shared_target", deadline_monotonic=_repo_map.time.monotonic() + 3600.0
    )

    assert no_deadline.get("partial") is not True
    assert future_deadline.get("partial") is not True
    assert future_deadline["callers"] == no_deadline["callers"]


# ---------------------------------------------------------------------------------------------
# Validation-runner detection's fallback `_iter_repo_files` walk (`_detect_validation_runners_
# from_root`, `_discover_validation_tests_for_primary_file`, `_has_python_validation_fallback_
# evidence`, `_raw_validation_plan_for_tests`'s own "no tests" branch): a SEPARATE, smaller,
# COUNT-bounded (not super-linear) but previously un-deadlined redundant-walk gap found while
# verifying the primary fix end-to-end via the real CLI.
# ---------------------------------------------------------------------------------------------


def test_detect_validation_runners_from_root_fallback_walk_forwards_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `precomputed_file_paths` is None (forcing the `_iter_repo_files` fallback), the
    deadline this function already accepts must reach that fallback call -- regression guard for
    the #222 residual fix (previously the fallback call omitted both kwargs entirely)."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    real_iter_repo_files = _repo_map._iter_repo_files

    def spy(root: Path, **kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return real_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(_repo_map, "_iter_repo_files", spy)
    flag = _repo_map._DeadlineBreakFlag()
    _repo_map._detect_validation_runners_from_root(
        tmp_path, precomputed_file_paths=None, deadline_monotonic=500_000.0, deadline_hit=flag
    )

    assert captured.get("deadline_monotonic") == 500_000.0
    assert captured.get("deadline_hit") is flag


def test_raw_validation_plan_for_tests_no_tests_branch_forwards_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same regression guard for `_raw_validation_plan_for_tests`'s "no tests" `else` branch
    (line ~10352 at authoring time) -- a fourth, independently-discovered occurrence of the exact
    same gap, missed by the first three fixes because this one uses `explicit_root`/`local_files`
    local names instead of `root`/`all_files`/`candidate_files`."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    captured: dict[str, Any] = {}
    real_iter_repo_files = _repo_map._iter_repo_files

    def spy(root: Path, **kwargs: Any) -> list[Path]:
        captured.update(kwargs)
        return real_iter_repo_files(root, **kwargs)

    monkeypatch.setattr(_repo_map, "_iter_repo_files", spy)
    _repo_map._raw_validation_plan_for_tests(
        [],  # no tests -> exercises the "no tests" branch
        repo_root=tmp_path,
        precomputed_file_paths=None,
        deadline_monotonic=500_000.0,
        deadline_hit=_repo_map._DeadlineBreakFlag(),
    )

    assert captured.get("deadline_monotonic") == 500_000.0
