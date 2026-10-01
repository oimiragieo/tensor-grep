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
    """MUTATION CONTROL. Real planted violations in a source tree, run the walker against it.

    Plants BOTH a static backward import (``from X import Y``) and a dynamic one
    (``importlib.import_module("X")``) -- each in its OWN module so the two detections are
    independent (a shared from-module would let one edge mask the other's absence) -- runs
    ``compute_violation_module_edges()`` on the sabotaged tree and verifies both appear.
    A gate never observed failing is not a gate.

    Also verifies the walker reports exactly the baseline on the clean tree (no false positives).
    """
    import shutil
    import tempfile

    # Step 1: Clean tree baseline (GREEN case)
    clean_baseline = _load_violation_baseline()
    clean_current = compute_violation_module_edges(_SRC_ROOT)
    assert clean_current == clean_baseline, "Clean tree must match baseline before mutation"

    static_edge = ("tensor_grep.core.pipeline", "tensor_grep.cli.runtime_paths")
    dynamic_edge = ("tensor_grep.core.result", "tensor_grep.cli.runtime_paths")
    assert static_edge not in clean_baseline, "Planted static edge must not pre-exist"
    assert dynamic_edge not in clean_baseline, "Planted dynamic edge must not pre-exist"

    # Step 2: Plant violations in a temp tree (RED case)
    with tempfile.TemporaryDirectory() as temp_root_str:
        temp_src = Path(temp_root_str) / "tensor_grep"
        shutil.copytree(_SRC_ROOT, temp_src, dirs_exist_ok=True)

        # Static mutation: core.pipeline gains `from tensor_grep.cli.runtime_paths import ...`
        core_pipeline = temp_src / "core" / "pipeline.py"
        assert core_pipeline.exists(), "fixture missing: core/pipeline.py required"
        lines = core_pipeline.read_text(encoding="utf-8").split("\n")
        insert_idx = next(
            (i + 1 for i, line in enumerate(lines) if "logger = logging.getLogger" in line), 0
        )
        assert insert_idx > 0, (
            "Fixture validation: 'logger = logging.getLogger' not found in core/pipeline.py. "
            "Mutation insertion point is undefined; test cannot proceed."
        )
        lines.insert(
            insert_idx,
            "from tensor_grep.cli.runtime_paths import get_work_root  # noqa: F401 MUTATION_STATIC",
        )
        core_pipeline.write_text("\n".join(lines), encoding="utf-8")

        # Dynamic mutation: core.result gains importlib.import_module("tensor_grep.cli....")
        # appended at EOF (a complete top-level statement, so it cannot split another one).
        core_result = temp_src / "core" / "result.py"
        assert core_result.exists(), "fixture missing: core/result.py required"
        original = core_result.read_text(encoding="utf-8")
        core_result.write_text(
            original.rstrip("\n")
            + "\n\nimport importlib as _mut_importlib  # MUTATION_DYNAMIC\n"
            + "_mut_dynamic = _mut_importlib.import_module('tensor_grep.cli.runtime_paths')\n",
            encoding="utf-8",
        )

        mutated_edges = compute_violation_module_edges(temp_src)

        assert static_edge in mutated_edges, (
            f"Mutation control (static) failed: planted {static_edge} was not detected. "
            f"Got edges: {sorted(mutated_edges)}"
        )
        assert dynamic_edge in mutated_edges, (
            f"Mutation control (dynamic) failed: planted {dynamic_edge} was not detected. "
            f"Got edges: {sorted(mutated_edges)}"
        )
        assert mutated_edges - clean_baseline == {static_edge, dynamic_edge}, (
            "Module-granularity freeze must reject exactly the two planted backward imports; "
            f"got extra/missing: {sorted(mutated_edges - clean_baseline)}"
        )
