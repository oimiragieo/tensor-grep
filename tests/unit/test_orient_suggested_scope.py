"""Tests for build_orient_capsule's `suggested_scope` hint (audit #93 SUB-2).

A truncated `tg orient` scan gives an agent an incomplete map with no guidance on how to narrow
it. `suggested_scope` rolls each scanned code file's composite centrality (the same score
`central_files` ranks on -- `_file_centrality_scores`) up to its top-level directory and suggests
the clear winner -- but ONLY when the underlying repo scan was actually truncated
(``rm["scan_limit"]["possibly_truncated"]``, from `repo_map.build_repo_map`; NOT this capsule's
own simplified ``scan_limit`` int, and NOT the snippet/token-budget ``truncated`` flag), and ONLY
when there IS a clear winner. A flat/tied signal degrades to None rather than risk a misleading
guess (ranking-safety-floor discipline, memory: tensor-grep-idf-ranking-fragility-2026-06-29 -- a
wrong scope suggestion actively misdirects an agent, worse than no hint at all)."""

from pathlib import Path
from typing import Any

import tensor_grep.cli.orient_capsule as oc
from tensor_grep.cli.orient_capsule import _suggested_scope_from_map, build_orient_capsule

# ---------------------------------------------------------------------------
# Unit tests: _suggested_scope_from_map (hand-built repo maps, no filesystem)
# ---------------------------------------------------------------------------


def test_suggested_scope_picks_clear_centrality_winner() -> None:
    # core/ has a real hub (imported by 2 siblings, 6 symbols); misc/ has one isolated, symbol-less
    # file. core/'s rolled-up centrality (10) clearly beats misc/'s (0) -> suggest core/.
    root = Path("/repo")
    rm = {
        "path": str(root),
        "files": [
            str(root / "core" / "hub.py"),
            str(root / "core" / "a.py"),
            str(root / "core" / "b.py"),
            str(root / "misc" / "lonely.py"),
        ],
        "imports": [
            {"file": str(root / "core" / "a.py"), "imports": ["hub"]},
            {"file": str(root / "core" / "b.py"), "imports": ["hub"]},
        ],
        "symbols": [
            {"name": f"Sym{i}", "kind": "function", "file": str(root / "core" / "hub.py")}
            for i in range(6)
        ],
    }
    scope = _suggested_scope_from_map(rm)
    assert scope is not None
    assert scope["confidence"] == "heuristic"
    assert scope["dirs"] == [str(root / "core")]


def test_suggested_scope_none_on_tied_centrality() -> None:
    # Two directories, each a single file with exactly one symbol and no import edges -> equal
    # rolled-up centrality (1 == 1). No clear winner -> degrade to None, never guess.
    root = Path("/repo")
    rm = {
        "path": str(root),
        "files": [str(root / "dira" / "a.py"), str(root / "dirb" / "b.py")],
        "imports": [],
        "symbols": [
            {"name": "A", "kind": "function", "file": str(root / "dira" / "a.py")},
            {"name": "B", "kind": "function", "file": str(root / "dirb" / "b.py")},
        ],
    }
    assert _suggested_scope_from_map(rm) is None


def test_suggested_scope_none_when_no_subdirectories() -> None:
    # Every file lives directly at the repo root -- there is no subdirectory to re-scope into.
    root = Path("/repo")
    rm = {
        "path": str(root),
        "files": [str(root / "a.py"), str(root / "b.py")],
        "imports": [{"file": str(root / "b.py"), "imports": ["a"]}],
        "symbols": [{"name": "A", "kind": "function", "file": str(root / "a.py")}],
    }
    assert _suggested_scope_from_map(rm) is None


def test_suggested_scope_none_when_centrality_all_zero() -> None:
    # Two candidate directories exist, but neither has any import edges or symbols -- a flat
    # zero signal is exactly as "no clear winner" as a tie, and must also degrade to None.
    root = Path("/repo")
    rm = {
        "path": str(root),
        "files": [str(root / "dira" / "a.py"), str(root / "dirb" / "b.py")],
        "imports": [],
        "symbols": [],
    }
    assert _suggested_scope_from_map(rm) is None


def test_suggested_scope_none_on_empty_map() -> None:
    assert _suggested_scope_from_map({"path": "/repo", "files": []}) is None


# ---------------------------------------------------------------------------
# Integration tests: build_orient_capsule's truncation gate
# ---------------------------------------------------------------------------


def _fake_rm(*, root: Path, possibly_truncated: bool, tied: bool) -> dict[str, Any]:
    """A hand-controlled repo_map.build_repo_map() result -- deterministic centrality (winner or
    exact tie) and an explicit scan_limit.possibly_truncated, decoupled from real-filesystem walk
    order so the GATING behavior can be tested precisely."""
    if tied:
        files = [str(root / "dira" / "a.py"), str(root / "dirb" / "b.py")]
        symbols = [
            {"name": "A", "kind": "function", "file": str(root / "dira" / "a.py")},
            {"name": "B", "kind": "function", "file": str(root / "dirb" / "b.py")},
        ]
        imports: list[dict[str, Any]] = []
    else:
        files = [
            str(root / "core" / "hub.py"),
            str(root / "core" / "a.py"),
            str(root / "core" / "b.py"),
            str(root / "misc" / "lonely.py"),
        ]
        symbols = [
            {"name": f"Sym{i}", "kind": "function", "file": str(root / "core" / "hub.py")}
            for i in range(6)
        ]
        imports = [
            {"file": str(root / "core" / "a.py"), "imports": ["hub"]},
            {"file": str(root / "core" / "b.py"), "imports": ["hub"]},
        ]
    return {
        "path": str(root),
        "files": files,
        "imports": imports,
        "symbols": symbols,
        "tests": [],
        "scan_limit": {
            "max_repo_files": len(files),
            "scanned_files": len(files),
            "possibly_truncated": possibly_truncated,
            "truncation_cause": "project-files" if possibly_truncated else None,
        },
    }


def test_build_orient_capsule_suggests_scope_when_truncated_with_clear_winner(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fake_rm = _fake_rm(root=tmp_path, possibly_truncated=True, tied=False)
    monkeypatch.setattr(oc._repo_map, "build_repo_map", lambda *_a, **_k: fake_rm)

    payload = build_orient_capsule(tmp_path, max_snippet_files=0)

    assert payload["suggested_scope"] is not None
    assert payload["suggested_scope"]["confidence"] == "heuristic"
    assert payload["suggested_scope"]["dirs"] == [str(tmp_path / "core")]


def test_build_orient_capsule_suggested_scope_absent_on_complete_scan(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Same clear-winner file structure as above, but the scan is COMPLETE: there is nothing to
    # narrow, so no suggestion must be emitted even though the centrality signal is strong.
    fake_rm = _fake_rm(root=tmp_path, possibly_truncated=False, tied=False)
    monkeypatch.setattr(oc._repo_map, "build_repo_map", lambda *_a, **_k: fake_rm)

    payload = build_orient_capsule(tmp_path, max_snippet_files=0)

    assert payload["suggested_scope"] is None


def test_build_orient_capsule_suggested_scope_null_when_truncated_but_flat(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Truncated AND a real (tied) centrality signal -- must degrade to None, not guess.
    fake_rm = _fake_rm(root=tmp_path, possibly_truncated=True, tied=True)
    monkeypatch.setattr(oc._repo_map, "build_repo_map", lambda *_a, **_k: fake_rm)

    payload = build_orient_capsule(tmp_path, max_snippet_files=0)

    assert payload["suggested_scope"] is None


def test_build_orient_capsule_suggested_scope_absent_on_real_small_repo(tmp_path: Path) -> None:
    # No monkeypatching -- a genuinely small repo (well under the default scan cap) never
    # truncates, so suggested_scope must be None end-to-end through the real build_repo_map walk.
    (tmp_path / "main.py").write_text("def run():\n    pass\n", encoding="utf-8")
    (tmp_path / "helper.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_tokens=500)

    assert payload["suggested_scope"] is None


def test_build_orient_capsule_suggested_scope_is_well_formed_on_real_truncated_scan(
    tmp_path: Path,
) -> None:
    # Real (non-mocked) truncation via a small --max-repo-files: whichever directory ends up
    # "winning" the real walk, the field must be either None or a well-formed {dirs, confidence}
    # hint -- never a raw exception, never a malformed shape.
    for sub in ("alpha", "beta"):
        d = tmp_path / sub
        d.mkdir()
        for i in range(6):
            (d / f"m{i}.py").write_text(f"def f_{sub}_{i}():\n    return {i}\n", encoding="utf-8")

    payload = build_orient_capsule(tmp_path, max_repo_files=4, max_snippet_files=0)

    assert payload["scan_limit"] == 4
    scope = payload["suggested_scope"]
    if scope is not None:
        assert scope["confidence"] == "heuristic"
        assert isinstance(scope["dirs"], list) and scope["dirs"]
