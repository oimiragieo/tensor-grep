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

from tensor_grep.core.import_edges import (
    VIOLATION_PACKAGE_EDGES,
    _resolve_relative_import,
    compute_import_edges,
    compute_violation_module_edges,
)

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


def _load_violation_baseline() -> set[tuple[str, str]]:
    data = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    return {tuple(edge) for edge in data["violation_module_edges"]}


def test_declared_layering_violations_can_only_shrink() -> None:
    """The package-pair freeze above is structurally blind to MORE of the violation it exists
    to burn down: ``("core", "cli")`` is an accepted baseline edge, so an unbounded number of
    new ``core -> cli`` imports pass it. Measured 2026-09-12: six such imports existed, and
    ``core.query_analyzer -> cli.runtime_paths`` had been added AFTER the freeze and passed.

    This freezes the violations at MODULE granularity, where the ratchet actually bites.
    """
    current = compute_violation_module_edges(_SRC_ROOT)
    baseline = _load_violation_baseline()
    added = current - baseline
    assert not added, (
        f"New import(s) into a package this repo declares a layering violation: "
        f"{sorted(added)}. This list is a burn-down, not an allowlist -- route through the "
        f"lower layer instead. Adding an entry to {_BASELINE_PATH} needs the same scrutiny "
        "as disabling a test, justified in the commit message."
    )


def test_violation_baseline_is_not_stale() -> None:
    """A burned-down violation must be REMOVED from the baseline, or the file silently
    re-admits it later (the 'baseline that legitimizes every regression it captures' trap).
    """
    current = compute_violation_module_edges(_SRC_ROOT)
    baseline = _load_violation_baseline()
    removed = baseline - current
    assert not removed, (
        f"These declared violations no longer exist and must be dropped from "
        f"{_BASELINE_PATH}: {sorted(removed)}"
    )


def test_violation_baseline_only_lists_declared_violation_pairs() -> None:
    """Guards the baseline against being widened by editing it rather than the code: every
    frozen entry must actually belong to a pair named in VIOLATION_PACKAGE_EDGES.
    """
    declared = set(VIOLATION_PACKAGE_EDGES)
    for from_module, to_module in _load_violation_baseline():
        pair = (from_module.split(".")[1], to_module.split(".")[1])
        assert pair in declared, (
            f"{from_module} -> {to_module} is frozen as a layering violation but {pair} is "
            f"not in VIOLATION_PACKAGE_EDGES {sorted(declared)}"
        )


def test_a_new_backward_import_would_be_caught() -> None:
    """MUTATION CONTROL. Real planted violation in source tree, run walker against it.

    Plants a backward import (``core -> cli``) into a temp source tree, runs
    ``compute_violation_module_edges()`` against the sabotaged tree, and verifies it detects
    the new violation. A gate never observed failing is not a gate; this test proves the
    walker catches violations at module granularity.

    Also verifies the test passes on the clean tree (no false positives).
    """
    import shutil
    import tempfile

    # Step 1: Clean tree baseline (GREEN case)
    clean_baseline = _load_violation_baseline()
    clean_current = compute_violation_module_edges(_SRC_ROOT)
    assert clean_current == clean_baseline, (
        "Clean tree must match baseline before mutation"
    )

    # Step 2: Plant violation in temp tree (RED case)
    with tempfile.TemporaryDirectory() as temp_root_str:
        temp_root = Path(temp_root_str)
        temp_src = temp_root / "tensor_grep"

        # Copy the entire src tree to temp
        shutil.copytree(_SRC_ROOT, temp_src, dirs_exist_ok=True)

        # Plant a backward import: core.pipeline imports from cli.runtime_paths
        core_pipeline = temp_src / "core" / "pipeline.py"
        assert core_pipeline.exists(), "fixture missing: core/pipeline.py required for mutation test"

        original_content = core_pipeline.read_text(encoding="utf-8")
        # Add import after logger setup line
        lines = original_content.split("\n")
        insert_idx = 0
        for i, line in enumerate(lines):
            if "logger = logging.getLogger" in line:
                insert_idx = i + 1
                break

        # Insert the mutation import
        planted_import = "from tensor_grep.cli.runtime_paths import get_work_root  # noqa: F401 MUTATION"
        lines.insert(insert_idx, planted_import)
        mutated_content = "\n".join(lines)
        core_pipeline.write_text(mutated_content, encoding="utf-8")

        # Run walker on mutated tree
        mutated_edges = compute_violation_module_edges(temp_src)

        # Verify new edge was detected
        planted_edge = ("tensor_grep.core.pipeline", "tensor_grep.cli.runtime_paths")
        assert planted_edge in mutated_edges, (
            f"Mutation control failed: planted violation {planted_edge} was not detected. "
            f"Got edges: {sorted(mutated_edges)}"
        )

        # Verify it's actually a NEW edge (not in clean baseline)
        assert planted_edge not in clean_baseline, (
            "Planted edge should not exist in clean baseline"
        )
        assert mutated_edges - clean_baseline, (
            "Module-granularity freeze must reject the new backward import"
        )
