"""Tests for the multi-project-workspace-root advisory (CEO #2 auto-narrow, 2026-07-20).

CEO #2: "Complete results live on REPO/src; root/mega-repo often partial/null-symbol. Agents
shouldn't have to know that tribal knowledge. Auto-detect package roots, suggest/apply narrow
scope, or refuse with a one-line fix."

This is the SAFE, ADDITIVE, ADVISORY increment -- the full auto-apply (silently re-scanning a
narrowed sub-scope and answering as if the caller had asked for it) would break the honesty
contract and is a deliberately deferred, bigger project. `_detect_workspace_root`
(orient_capsule.py, sibling of `_detect_vendored_subtrees`) reuses the EXACT closed-vocabulary
project-marker set and child-count thresholds that already gate `tg search`'s unbounded-
workspace-root refusal (`io/directory_scanner.py`'s `BROAD_WORKSPACE_PROJECT_MARKERS` /
`BROAD_WORKSPACE_PROJECT_CHILD_THRESHOLD` / `BROAD_WORKSPACE_MARKED_ROOT_CHILD_THRESHOLD`, via
`cli/main.py`'s `_workspace_project_child_names`) rather than a second, independently hand-rolled
detector -- so `tg orient`/`tg agent`'s advisory hint and `tg search`'s hard refusal never
disagree about what "looks like a workspace root" means for the same directory. When it fires,
`workspace_root_detected: true` is stamped and `suggested_scope` is computed PROACTIVELY (the same
centrality-weighted directory rollup `_suggested_scope_from_map` already produces on a truncated
scan) -- even absent any scan truncation. The full, unscoped result is always returned UNCHANGED;
this never silently narrows or re-scans.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import tensor_grep.cli.orient_capsule as oc
from tensor_grep.cli.orient_capsule import _detect_workspace_root, build_orient_capsule

_FAR_FUTURE_DEADLINE_S = 3600.0


# ---------------------------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------------------------


def _write_sibling_projects(root: Path, names: list[str]) -> None:
    """`names` sibling top-level dirs, each carrying its own project marker directly inside it --
    the exact shape `_workspace_project_child_names` (cli/main.py) already keys the `tg search`
    workspace-root refusal on (mirrors test_medlow_main_cli.py's
    `test_m15_detector_keys_on_child_project_markers` fixture shape)."""
    for name in names:
        child = root / name
        child.mkdir(parents=True)
        (child / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        (child / "main.py").write_text(f"def run_{name}():\n    return 1\n", encoding="utf-8")


def _write_single_project(root: Path) -> None:
    """The literal CEO negative fixture: one pyproject.toml AT THE ROOT (marking the root itself),
    plus a plain src/ dir with no manifest of its own -- must never trigger."""
    (root / "pyproject.toml").write_text("[project]\nname = 'solo'\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (src / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------
# Unit tests: _detect_workspace_root (real filesystem -- mirrors _detect_vendored_subtrees's own
# real-fs-backed unit tests in test_orient_deweight_vendored.py)
# ---------------------------------------------------------------------------------------------


def test_fires_on_three_sibling_marked_projects(tmp_path: Path) -> None:
    _write_sibling_projects(tmp_path, ["proj1", "proj2", "proj3"])
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "proj1" / "main.py")]}
    assert _detect_workspace_root(rm) is True


def test_fires_with_mixed_marker_kinds_including_nested_git(tmp_path: Path) -> None:
    # go.mod / Cargo.toml / a bare nested .git -- not just pyproject.toml.
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / ".git").mkdir()
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "a" / "pyproject.toml")]}
    assert _detect_workspace_root(rm) is True


def test_single_project_repo_with_one_src_dir_does_not_fire(tmp_path: Path) -> None:
    """The literal CEO negative fixture."""
    _write_single_project(tmp_path)
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "src" / "main.py")]}
    assert _detect_workspace_root(rm) is False


def test_marked_root_with_two_manifested_children_does_not_fire(tmp_path: Path) -> None:
    # Mirrors tensor-grep's OWN repo shape (root pyproject.toml + npm/package.json +
    # rust_core/Cargo.toml -- 2 marked children): a polyglot single project must not be mistaken
    # for a multi-project workspace. The marked-root threshold (8) exists precisely for this.
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "npm").mkdir()
    (tmp_path / "npm" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "rust_core").mkdir()
    (tmp_path / "rust_core" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "npm" / "package.json")]}
    assert _detect_workspace_root(rm) is False


def test_unmarked_root_below_threshold_does_not_fire(tmp_path: Path) -> None:
    # 2 sibling marked dirs -- one below the unmarked-root threshold (3).
    _write_sibling_projects(tmp_path, ["proj1", "proj2"])
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "proj1" / "main.py")]}
    assert _detect_workspace_root(rm) is False


def test_no_path_key_never_guesses() -> None:
    assert _detect_workspace_root({"files": []}) is False


def test_nonexistent_path_returns_false() -> None:
    assert _detect_workspace_root({"path": "/does/not/exist/at/all/xyz"}) is False


def test_deadline_already_exceeded_skips_detection(tmp_path: Path) -> None:
    _write_sibling_projects(tmp_path, ["proj1", "proj2", "proj3"])
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "proj1" / "main.py")]}
    assert _detect_workspace_root(rm) is True  # sanity: fires without a deadline

    already_past = time.monotonic() - 1.0
    assert _detect_workspace_root(rm, deadline_monotonic=already_past) is False


def test_no_pressure_path_unchanged(tmp_path: Path) -> None:
    _write_sibling_projects(tmp_path, ["proj1", "proj2", "proj3"])
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "proj1" / "main.py")]}

    omitted = _detect_workspace_root(rm)
    far_future = _detect_workspace_root(
        rm, deadline_monotonic=time.monotonic() + _FAR_FUTURE_DEADLINE_S
    )

    assert omitted is True
    assert far_future is True


# ---------------------------------------------------------------------------------------------
# Bounded-cost: a wide synthetic tree must not materially add wall-clock (mirrors the
# `_detect_vendored_subtrees` bound discipline -- but this detector does exactly ONE `iterdir()`
# call on the scan root plus <= len(markers) `Path.exists()` probes PER DIRECT CHILD, so its cost
# is bounded by the root's own immediate fan-out, never by repo size / scan depth).
# ---------------------------------------------------------------------------------------------


def test_bounded_cost_on_wide_synthetic_workspace(tmp_path: Path) -> None:
    count = 300
    for i in range(count):
        child = tmp_path / f"proj_{i:05d}"
        child.mkdir()
        (child / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    rm = {"path": str(tmp_path), "files": [str(tmp_path / "proj_00000" / "pyproject.toml")]}

    start = time.monotonic()
    result = _detect_workspace_root(rm)
    elapsed = time.monotonic() - start

    assert result is True
    assert elapsed < 3.0, f"detector took {elapsed:.2f}s on a {count}-sibling synthetic tree"


# ---------------------------------------------------------------------------------------------
# Integration: build_orient_capsule_from_map / build_orient_capsule wiring
# ---------------------------------------------------------------------------------------------


def test_build_orient_capsule_surfaces_workspace_root_detected_and_suggested_scope(
    tmp_path: Path,
) -> None:
    # A real, small (non-truncated) multi-project tree -- one dir clearly denser than the others
    # so suggested_scope has a real winner to point to.
    hub = tmp_path / "core"
    hub.mkdir()
    (hub / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (hub / "hub.py").write_text(
        "def hub_fn():\n    return 1\n\n\ndef hub_fn2():\n    return 2\n", encoding="utf-8"
    )
    for name in ("siblingA", "siblingB"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (d / "lonely.py").write_text("x = 1\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_snippet_files=0)

    assert payload["workspace_root_detected"] is True
    assert payload["suggested_scope"] is not None
    assert payload["suggested_scope"]["confidence"] == "heuristic"


def test_build_orient_capsule_returns_plain_dict_no_exit_path_change(tmp_path: Path) -> None:
    # tg orient has NO exit-2 contract -- build_orient_capsule must return a plain dict, never
    # raise, on a detected-workspace-root repo (a smoke check the wiring didn't introduce an
    # accidental raise/Exit path).
    for name in ("proj1", "proj2", "proj3"):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (d / "m.py").write_text("x = 1\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_snippet_files=0)
    assert isinstance(payload, dict)
    assert payload["workspace_root_detected"] is True


def test_build_orient_capsule_single_project_workspace_root_detected_absent(
    tmp_path: Path,
) -> None:
    """THE NEGATIVE / NO-REGRESSION GUARD (literal CEO fixture): one pyproject.toml at root, a
    plain src/ dir -- must never trigger, end to end, with no monkeypatching at all."""
    _write_single_project(tmp_path)

    payload = build_orient_capsule(tmp_path, max_snippet_files=2)

    assert "workspace_root_detected" not in payload
    assert payload["suggested_scope"] is None


def test_build_orient_capsule_workspace_root_detected_forced_true_is_additive_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Isolates the causal effect of the flag on the SAME underlying repo: forcing
    `_detect_workspace_root` True must ONLY add `workspace_root_detected` (and newly populate the
    already-additive/conditional-shaped `suggested_scope`) -- every OTHER capsule field
    (central_files, entry_points, symbol_map, snippets, token_estimate, scan_limit,
    suggested_ignore, deweighted_trees, auto_deweight, routing_reason, path, truncated) must stay
    byte-identical. This is the strongest proof the wiring is additive-only, independent of
    whether any particular fixture geometry happens to trip the real detector."""
    hub = tmp_path / "core"
    hub.mkdir()
    (hub / "hub.py").write_text("def hub_fn():\n    return 1\n", encoding="utf-8")
    (hub / "leaf_a.py").write_text("from hub import hub_fn\n", encoding="utf-8")
    (tmp_path / "misc").mkdir()
    (tmp_path / "misc" / "lonely.py").write_text("x = 1\n", encoding="utf-8")

    baseline = build_orient_capsule(tmp_path, max_snippet_files=2)
    assert "workspace_root_detected" not in baseline
    assert baseline["suggested_scope"] is None

    monkeypatch.setattr(oc, "_detect_workspace_root", lambda *a, **k: True)
    forced_on = build_orient_capsule(tmp_path, max_snippet_files=2)

    assert forced_on["workspace_root_detected"] is True
    assert forced_on["suggested_scope"] is not None

    additive_keys = {"workspace_root_detected", "suggested_scope"}
    baseline_stripped = {k: v for k, v in baseline.items() if k not in additive_keys}
    forced_on_stripped = {k: v for k, v in forced_on.items() if k not in additive_keys}
    assert baseline_stripped == forced_on_stripped
