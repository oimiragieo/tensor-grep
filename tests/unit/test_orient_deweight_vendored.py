"""Tests for auto-de-weighting of bundled vendor/skill/generated CODE subtrees in `tg orient`
centrality (#132 / #55 PR6).

A subtree fires ONLY on STRONG-1 (nested package manifest) AND (STRONG-2 (import island) OR WEAK
(name prior)). The de-weight multiplies a subtree file's composite centrality by
``_DEWEIGHT_FACTOR`` -- it LOWERS the score, it never EXCLUDES the file, so a genuinely central
vendored file can still surface. A monorepo subproject that has a manifest but IS imported across
the repo is protected by the import-island test (de-weight would hide real product code).

`_detect_vendored_subtrees` reads the real filesystem (it checks ``root.is_dir()`` and each
candidate directory for a manifest via ``.exists()``), so these tests build real ``tmp_path``
trees with on-disk manifests -- unlike the hand-built-``rm`` centrality tests which never touch
disk.
"""

from pathlib import Path
from typing import Any

from tensor_grep.cli.orient_capsule import (
    _DEWEIGHT_FACTOR,
    _central_files_from_map,
    _detect_vendored_subtrees,
    _file_centrality_scores,
)


def _rm(root: Path, files: list[Path], imports: dict[Path, list[str]]) -> dict[str, Any]:
    """Build a repo-map dict rooted at a real (resolved) directory."""
    return {
        "path": str(root),
        "files": [str(f) for f in files],
        "imports": [{"file": str(f), "imports": mods} for f, mods in imports.items()],
        "symbols": [],
    }


def test_fires_on_manifest_plus_import_island(tmp_path: Path) -> None:
    # `bundled/` has its own pyproject.toml (STRONG-1). No file OUTSIDE bundled/ imports into it,
    # and its name is NOT a vendor-prior -> fires on manifest + import-island (STRONG-2) alone.
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "bundled" / "lib.py", root / "bundled" / "helper.py"],
        {root / "bundled" / "lib.py": ["helper"]},  # internal edge only
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "bundled") in trees
    reasons = trees[str(root / "bundled")]["reasons"]
    assert any(r.startswith("nested-manifest:") for r in reasons)
    assert "import-island" in reasons
    assert not any(r.startswith("name-prior:") for r in reasons)


def test_fires_on_manifest_plus_name_prior_even_when_not_island(tmp_path: Path) -> None:
    # `node_modules/` has a manifest AND is imported from outside (app.js imports its module, so it
    # is NOT an island) -- but "node_modules" is a name-prior, so it still fires.
    root = tmp_path.resolve()
    (root / "node_modules").mkdir()
    (root / "node_modules" / "package.json").write_text('{"name": "dep"}\n')
    rm = _rm(
        root,
        [root / "app.js", root / "node_modules" / "dep.js"],
        {root / "app.js": ["dep"]},  # OUTSIDE importer -> not an island
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "node_modules") in trees
    reasons = trees[str(root / "node_modules")]["reasons"]
    assert any(r.startswith("name-prior:") for r in reasons)
    assert "import-island" not in reasons


def test_skips_manifest_only_monorepo_subproject(tmp_path: Path) -> None:
    # THE false-positive guard: `packages/core/` has a manifest but is imported across the repo
    # (app.py imports its module) AND has a neutral name (no vendor-prior) -> NOT an island, NO
    # name prior -> NOT de-weighted. De-weighting a genuine subproject would hide real product code.
    root = tmp_path.resolve()
    (root / "packages" / "core").mkdir(parents=True)
    (root / "packages" / "core" / "pyproject.toml").write_text("[project]\nname = 'core'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "packages" / "core" / "mod.py"],
        {root / "app.py": ["mod"]},  # imported from outside the subtree
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "packages" / "core") not in trees
    assert trees == {}


def test_skips_when_no_manifest(tmp_path: Path) -> None:
    # `vendor/` matches a name-prior but has NO manifest -> STRONG-1 fails -> never fires. The name
    # prior is only a tie-breaker, never sufficient on its own.
    root = tmp_path.resolve()
    (root / "vendor").mkdir()
    rm = _rm(root, [root / "app.py", root / "vendor" / "lib.py"], {})
    assert _detect_vendored_subtrees(rm) == {}


def test_central_files_deweights_but_keeps_the_file(tmp_path: Path) -> None:
    # `bundled/hub.py` is highly central WITHIN its island (imported by two siblings). With
    # de-weight ON its composite score is multiplied by _DEWEIGHT_FACTOR -- but it is still PRESENT
    # in central_files (de-weight != exclude).
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [
            root / "bundled" / "hub.py",
            root / "bundled" / "a.py",
            root / "bundled" / "b.py",
        ],
        {root / "bundled" / "a.py": ["hub"], root / "bundled" / "b.py": ["hub"]},
    )
    raw = _central_files_from_map(rm, max_central_files=10, auto_deweight=False)
    dew = _central_files_from_map(rm, max_central_files=10, auto_deweight=True)

    hub = str(root / "bundled" / "hub.py")
    raw_hub = next(f for f in raw if f["file"] == hub)
    dew_hub = next(f for f in dew if f["file"] == hub)  # still present -> de-weight, not exclude
    assert raw_hub["score"] > 0
    assert dew_hub["score"] == round(raw_hub["score"] * _DEWEIGHT_FACTOR, 6)


def test_file_centrality_scores_are_raw_not_deweighted(tmp_path: Path) -> None:
    # `_file_centrality_scores` (which `_suggested_scope_from_map` reads) must return the RAW
    # composite score -- the de-weight lives in `_central_files_from_map`, so suggested_scope keeps
    # its unmodified signal.
    root = tmp_path.resolve()
    (root / "bundled").mkdir()
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "bundled" / "hub.py", root / "bundled" / "a.py", root / "bundled" / "b.py"],
        {root / "bundled" / "a.py": ["hub"], root / "bundled" / "b.py": ["hub"]},
    )
    _code_files, centrality = _file_centrality_scores(rm)
    hub = str(root / "bundled" / "hub.py")
    # hub.py: fan_in=2 (a,b import it) + fan_out=0 + density=0 = 2.0, RAW (not * _DEWEIGHT_FACTOR)
    assert centrality[hub] == 2.0


def test_skips_graph_invisible_subproject_with_no_import_edges(tmp_path: Path) -> None:
    # A non-Python subproject -- e.g. `rust_core/` with its own Cargo.toml + .rs files -- is INVISIBLE
    # to the Python-centric stem import graph, so it has ZERO import edges. That trivially satisfies
    # "externally isolated" but is NOT an import island (no internal cohesion), so with a neutral name
    # it must NOT be de-weighted -- else a legitimate Rust/Go crate at the repo root gets buried.
    # Regression for the agent-capsule rust-language-hint hardcase (#525 CI: rust_core was falsely
    # de-weighted, so a Python file won primary_target over src/lib.rs).
    root = tmp_path.resolve()
    (root / "rust_core").mkdir()
    (root / "rust_core" / "Cargo.toml").write_text("[package]\nname = 'rc'\nversion = '0.1.0'\n")
    rm = _rm(
        root,
        [
            root / "app.py",
            root / "rust_core" / "src" / "lib.rs",
            root / "rust_core" / "src" / "mod.rs",
        ],
        {},  # no resolvable import edges anywhere (Rust files; the Python stem graph is blind)
    )
    assert _detect_vendored_subtrees(rm) == {}


def test_deeply_nested_subtree_file_counts_as_a_member(tmp_path: Path) -> None:
    # A file NESTED several levels inside a detected subtree (`bundled/pkg/mod.py`) must be counted as
    # a tree member. The import-island verdict here HINGES on it: `lib.py` imports `mod` (an internal
    # edge) so the island fires ONLY if the nested `mod.py` is in `tree_files`. Guards the lexical
    # tuple-prefix membership test (`parts[:depth] == prefix`) that replaced the per-file, per-subtree
    # `_path_is_relative_to` resolve() check (perf, 2026-07-11) -- a prefix bug that dropped nested
    # files would silently turn this island into a non-island.
    root = tmp_path.resolve()
    (root / "bundled" / "pkg").mkdir(parents=True)
    (root / "bundled" / "pyproject.toml").write_text("[project]\nname = 'bundled'\n")
    rm = _rm(
        root,
        [root / "app.py", root / "bundled" / "lib.py", root / "bundled" / "pkg" / "mod.py"],
        {
            root / "bundled" / "lib.py": ["mod"]
        },  # internal edge lib -> pkg/mod (both inside bundled/)
    )
    trees = _detect_vendored_subtrees(rm)
    assert str(root / "bundled") in trees
    assert "import-island" in trees[str(root / "bundled")]["reasons"]
