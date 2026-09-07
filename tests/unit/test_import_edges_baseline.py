"""P13 extension (Task 07): freeze the current cli/core/backends/io import graph so a CLI/MCP
service extraction can't silently introduce a new cross-package edge. This does NOT enforce a
layering direction (some baseline edges below are pre-existing violations of the intended
direction, e.g. core->cli) -- it only catches a NEW edge that wasn't here when frozen. Burning
down the pre-existing violations is separate, unstarted P13 scope (adopting import-linter with
an enforced direction), tracked in docs/BACKLOG.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.core.import_edges import _resolve_relative_import, compute_import_edges

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "tensor_grep"
_BASELINE_PATH = _REPO_ROOT / "docs" / "design" / "2026-09-07-import-edges-baseline.json"


def _load_baseline() -> set[tuple[str, str]]:
    data = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    return {tuple(edge) for edge in data["edges"]}


def test_baseline_file_is_valid_json_edge_list() -> None:
    baseline = _load_baseline()
    assert baseline, "baseline must not be empty -- this repo has real cross-package imports"
    for from_pkg, to_pkg in baseline:
        assert from_pkg in {"cli", "core", "backends", "io"}
        assert to_pkg in {"cli", "core", "backends", "io"}
        assert from_pkg != to_pkg


def test_no_new_import_edges_beyond_frozen_baseline() -> None:
    current = compute_import_edges(_SRC_ROOT)
    baseline = _load_baseline()
    new_edges = current - baseline
    assert not new_edges, (
        f"New cross-package import edge(s) not in the frozen baseline: {sorted(new_edges)}. "
        f"If this is intentional, update {_BASELINE_PATH} in the same change and explain why "
        "in the commit message -- a silent new edge is exactly what this freeze exists to catch."
    )


def test_relative_import_crossing_a_package_boundary_resolves_correctly() -> None:
    # Codex Luna audit finding (HIGH): a naive walker that skips every relative import can miss
    # a cross-package edge, e.g. `tensor_grep.cli.sub.mod` doing `from ...core import config`
    # (3 dots: strip 2 package levels off cli.sub.mod's own package, cli.sub -> cli, then climb
    # one more -> tensor_grep) resolves to `tensor_grep.core.config`, which crosses cli->core.
    assert _resolve_relative_import("tensor_grep.cli.sub.mod", 3, "core") == "tensor_grep.core"
    # A same-package relative import (`from . import sibling` inside cli.main) must NOT be
    # reported as crossing anything.
    assert _resolve_relative_import("tensor_grep.cli.main", 1, None) == "tensor_grep.cli"


def test_current_graph_matches_baseline_exactly() -> None:
    # Stronger than the "no new edges" check above: also flags a REMOVED edge, so the baseline
    # stays an accurate map of reality rather than a monotonically-growing allowlist.
    current = compute_import_edges(_SRC_ROOT)
    baseline = _load_baseline()
    assert current == baseline, (
        f"Import graph drifted from the frozen baseline.\n"
        f"Added: {sorted(current - baseline)}\n"
        f"Removed (baseline is now stale, tighten it): {sorted(baseline - current)}"
    )
