"""P13 extension (Task 07): freeze the current cli/core/backends/io import graph so a CLI/MCP
service extraction can't silently introduce a new cross-package edge. This does NOT enforce a
layering direction (some baseline edges below are pre-existing violations of the intended
direction, e.g. core->cli) -- it only catches a NEW edge that wasn't here when frozen. Burning
down the pre-existing violations is separate, unstarted P13 scope (adopting import-linter with
an enforced direction), tracked in docs/BACKLOG.md.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from tensor_grep.core.import_edges import (
    VIOLATION_PACKAGE_EDGES,
    _dynamic_import_literal,
    _resolve_relative_import,
    compute_import_edges,
    compute_unresolved_import_sites,
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('importlib.import_module("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('il.import_module("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('import_module("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('__import__("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('builtins.__import__("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        # Known, documented gaps: not statically resolvable, reported as no edge.
        ("importlib.import_module(name)", None),
        ('getattr(importlib, "import_module")("tensor_grep.cli.x")', None),
        ('importlib.import_module(".sibling")', None),
        # Resolvable keyword / package forms (Codex Sol R2).
        ('importlib.import_module(name="tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('importlib.import_module(".x", package="tensor_grep.cli")', "tensor_grep.cli.x"),
        ('importlib.import_module(".x", "tensor_grep.cli")', "tensor_grep.cli.x"),
        ('importlib.import_module("..cli.x", "tensor_grep.core")', "tensor_grep.cli.x"),
        ('__import__(".x")', None),
        # Round 3: runpy loads a module by name; __import__ with a relative `level` is not absolute.
        ('runpy.run_module("tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('run_module(mod_name="tensor_grep.cli.x")', "tensor_grep.cli.x"),
        ('__import__("cli.x", globals(), locals(), ["n"], 2)', None),
        ('__import__("cli.x", level=1)', None),
        ('__import__("tensor_grep.cli.x", globals(), locals(), [], 0)', "tensor_grep.cli.x"),
        ('__import__("tensor_grep.cli.x", level=0)', "tensor_grep.cli.x"),
    ],
)
def test_dynamic_import_literal_forms(source: str, expected: str | None) -> None:
    call = ast.parse(source).body[0].value  # type: ignore[attr-defined]
    assert isinstance(call, ast.Call)
    assert _dynamic_import_literal(call) == expected


def _make_src_tree(tmp_path: Path, core_sources: dict[str, str]) -> Path:
    root = tmp_path / "tensor_grep"
    for pkg in ("cli", "core", "backends", "io"):
        (root / pkg).mkdir(parents=True)
        (root / pkg / "__init__.py").write_text("", encoding="utf-8")
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "cli" / "runtime_paths.py").write_text("", encoding="utf-8")
    for name, source in core_sources.items():
        (root / "core" / f"{name}.py").write_text(source, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "source",
    [
        "from tensor_grep import cli\n",
        "from .. import cli\n",
        "from tensor_grep import core, cli as _c\n",
    ],
)
def test_root_from_import_of_a_package_is_a_cross_package_edge(tmp_path: Path, source: str) -> None:
    # Codex Sol R2 (HIGH): `from tensor_grep import cli` imports the cli PACKAGE but the walker
    # only looked at node.module ("tensor_grep"), so the edge was invisible.
    root = _make_src_tree(tmp_path, {"m": source})
    assert ("tensor_grep.core.m", "tensor_grep.cli") in compute_violation_module_edges(root)


def test_root_from_import_of_a_non_package_name_is_not_an_edge(tmp_path: Path) -> None:
    # Control: the name expansion must not invent edges for ordinary root attributes.
    root = _make_src_tree(tmp_path, {"m": "from tensor_grep import __version__\n"})
    assert not {e for e in compute_violation_module_edges(root) if e[0] == "tensor_grep.core.m"}


@pytest.mark.parametrize(
    ("rel_path", "source", "to_module"),
    [
        ("core/__init__.py", "from .. import cli\n", "tensor_grep.cli"),
        ("core/__init__.py", "from ..cli import runtime_paths\n", "tensor_grep.cli"),
        ("core/m.py", "from ..cli import runtime_paths\n", "tensor_grep.cli"),
    ],
)
def test_relative_imports_resolve_the_same_from_a_package_init_and_a_plain_module(
    tmp_path: Path, rel_path: str, source: str, to_module: str
) -> None:
    # Codex Sol R3 (HIGH): a package's __init__.py IS the package, so one dot means the package
    # itself, not its parent -- the old arithmetic treated it as a leaf module and lost the edge.
    root = _make_src_tree(tmp_path, {})
    target = root / rel_path
    target.write_text(source, encoding="utf-8")
    from_module = "tensor_grep." + rel_path.removesuffix(".py").removesuffix("/__init__").replace(
        "/", "."
    )
    assert (from_module, to_module) in compute_violation_module_edges(root)


def test_a_child_module_import_is_recorded_as_the_child_not_only_the_parent(
    tmp_path: Path,
) -> None:
    # Codex Sol R3 (HIGH): `from tensor_grep.cli import runtime_paths` recorded only the coarse
    # `tensor_grep.cli`, so it could hide behind an already-frozen edge to that parent.
    root = _make_src_tree(
        tmp_path,
        {"m": "from tensor_grep.cli import runtime_paths, not_a_module\n"},
    )
    edges = {e for e in compute_violation_module_edges(root) if e[0] == "tensor_grep.core.m"}
    assert ("tensor_grep.core.m", "tensor_grep.cli.runtime_paths") in edges
    # Control: a name that is not a submodule must not invent a child edge.
    assert ("tensor_grep.core.m", "tensor_grep.cli.not_a_module") not in edges


def test_unresolved_import_sites_record_each_sites_source_text(tmp_path: Path) -> None:
    root = _make_src_tree(
        tmp_path,
        {
            "a": "import importlib\nimportlib.import_module(name)\nimportlib.import_module(other)\n",
            "b": 'import importlib\ngetattr(importlib, "import_module")("tensor_grep.cli.x")\n',
            "c": 'import importlib\nimportlib.import_module("tensor_grep.cli.x")\n',
            "d": "from tensor_grep import *\n",
            "e": 'import builtins\n__import__("cli.x", globals(), locals(), [], 2)\n',
            "f": "from tensor_grep.cli import *\n",
            "g": "from tensor_grep.core import *\n",
        },
    )
    # `c` is resolvable (an edge, not unresolved); `a` has TWO opaque calls. A star import from
    # ANOTHER layer package (`f`) loads whatever its __all__ names, so it is opaque (Codex Sol
    # R4); a star import from the importer's OWN package (`g`) crosses nothing. Each site is
    # recorded by its SOURCE TEXT, not a count (Codex Sol R5), so editing a frozen call's target
    # expression changes what is recorded.
    assert compute_unresolved_import_sites(root) == {
        "tensor_grep.core.a": [
            "importlib.import_module(name)",
            "importlib.import_module(other)",
        ],
        "tensor_grep.core.b": ["getattr(importlib, 'import_module')('tensor_grep.cli.x')"],
        "tensor_grep.core.d": ["from tensor_grep import *"],
        "tensor_grep.core.e": ["__import__('cli.x', globals(), locals(), [], 2)"],
        "tensor_grep.core.f": ["from tensor_grep.cli import *"],
    }


def test_retargeting_a_frozen_opaque_call_changes_the_recorded_site(tmp_path: Path) -> None:
    # Codex Sol R5 (HIGH): the count stayed 1 when `import_module(_E[name])` was edited to import
    # `tensor_grep.cli.runtime_paths` for one name, so every baseline stayed green.
    template = (
        "from importlib import import_module\n_E = {}\ndef f(name):\n    return import_module(%s)\n"
    )
    safe = _make_src_tree(tmp_path / "safe", {"a": template % "_E[name]"})
    retargeted = _make_src_tree(
        tmp_path / "retargeted",
        {"a": template % "'tensor_grep.cli.runtime_paths' if name == 'x' else _E[name]"},
    )
    assert compute_unresolved_import_sites(safe) == {
        "tensor_grep.core.a": ["import_module(_E[name])"]
    }
    assert compute_unresolved_import_sites(retargeted) == {
        "tensor_grep.core.a": [
            "import_module('tensor_grep.cli.runtime_paths' if name == 'x' else _E[name])"
        ]
    }


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("from pkgutil import resolve_name\nresolve_name('tensor_grep.cli')\n", "tensor_grep.cli"),
        ("import pkgutil\npkgutil.resolve_name('tensor_grep.cli.main')\n", "tensor_grep.cli.main"),
        ("import pkgutil as pk\npk.resolve_name('tensor_grep.cli.main')\n", "tensor_grep.cli.main"),
        (
            "from pkgutil import resolve_name as rn\nrn('tensor_grep.cli.main')\n",
            "tensor_grep.cli.main",
        ),
        # pkgutil's `pkg.mod:attr` form names the module before the colon.
        (
            "import pkgutil\npkgutil.resolve_name('tensor_grep.cli.main:run')\n",
            "tensor_grep.cli.main",
        ),
    ],
)
def test_pkgutil_resolve_name_is_a_dynamic_import(tmp_path: Path, source: str, target: str) -> None:
    # Codex Sol R5 (HIGH): pkgutil.resolve_name IMPORTS the named module; the walker only knew
    # import_module / __import__ / run_module.
    root = _make_src_tree(tmp_path, {"m": source})
    assert ("tensor_grep.core.m", target) in compute_violation_module_edges(root)


def test_pkgutil_resolve_name_with_an_opaque_name_is_an_unresolved_site(tmp_path: Path) -> None:
    root = _make_src_tree(tmp_path, {"m": "import pkgutil\npkgutil.resolve_name(spec)\n"})
    assert compute_unresolved_import_sites(root) == {
        "tensor_grep.core.m": ["pkgutil.resolve_name(spec)"]
    }


def test_importlib_util_resolve_name_is_not_an_import(tmp_path: Path) -> None:
    # Control: importlib.util.resolve_name only RESOLVES a relative name -- it imports nothing
    # (the walker itself calls it), so matching any `resolve_name` would be a false positive.
    root = _make_src_tree(
        tmp_path,
        {"m": "import importlib.util\nimportlib.util.resolve_name('.x', 'tensor_grep.cli')\n"},
    )
    assert compute_unresolved_import_sites(root) == {}
    assert not {e for e in compute_violation_module_edges(root) if e[0] == "tensor_grep.core.m"}


def _load_unresolved_baseline() -> dict[str, list[str]]:
    data = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    return {module: list(sites) for module, sites in data["unresolved_import_sites"].items()}


def _site_multiset(sites: dict[str, list[str]]) -> Counter[tuple[str, str]]:
    return Counter((module, text) for module, texts in sites.items() for text in texts)


def test_unresolved_import_sites_can_only_shrink() -> None:
    """An import whose target cannot be resolved statically hides a possible edge, so each such
    site is frozen by its SOURCE TEXT: a new site, a second site in a frozen module, or an EDIT
    to a frozen call's arguments all show up as a site the baseline does not list.
    """
    added = _site_multiset(compute_unresolved_import_sites(_SRC_ROOT)) - _site_multiset(
        _load_unresolved_baseline()
    )
    assert not added, (
        f"New or changed unresolvable import site(s): {sorted(added)}. Resolve the target to a "
        f"literal, or re-review it and update {_BASELINE_PATH}."
    )


def test_unresolved_import_site_baseline_is_not_stale() -> None:
    gone = _site_multiset(_load_unresolved_baseline()) - _site_multiset(
        compute_unresolved_import_sites(_SRC_ROOT)
    )
    assert not gone, f"Frozen site(s) no longer present; tighten {_BASELINE_PATH}: {sorted(gone)}"


def test_namespace_and_extension_children_are_recorded_as_children(tmp_path: Path) -> None:
    # Codex Sol R4 (HIGH): `_is_submodule` only knew `.py` files and dirs with an __init__.py,
    # so an importable namespace package or compiled extension hid behind its parent edge.
    root = _make_src_tree(tmp_path, {"m": "from tensor_grep.cli import ns, ext, nothing\n"})
    (root / "cli" / "ns").mkdir()
    (root / "cli" / "ext.pyd").write_bytes(b"")
    edges = {e for e in compute_violation_module_edges(root) if e[0] == "tensor_grep.core.m"}
    assert ("tensor_grep.core.m", "tensor_grep.cli.ns") in edges
    assert ("tensor_grep.core.m", "tensor_grep.cli.ext") in edges
    assert ("tensor_grep.core.m", "tensor_grep.cli.nothing") not in edges  # control


def _reviewed_export_targets(relative: str) -> list[str]:
    tree = ast.parse((_SRC_ROOT / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if getattr(target, "id", "") == "_EXPORTS" and isinstance(node.value, ast.Dict):
                return [
                    v.value
                    for v in node.value.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
    raise AssertionError(f"no _EXPORTS dict found in {relative}")


@pytest.mark.parametrize(
    ("relative", "layer", "expected_count"),
    [("backends/__init__.py", "backends", 16), ("core/__init__.py", "core", 9)],
)
def test_reviewed_opaque_import_targets_stay_inside_their_layer(
    relative: str, layer: str, expected_count: int
) -> None:
    """The unresolved-site baseline pins the COUNT of opaque calls, not where they point
    (Codex Sol R4, HIGH): retargeting one `_EXPORTS` entry across layers would keep every
    baseline green. These two modules' lazy `import_module(_EXPORTS[name])` targets were
    reviewed as in-layer, so pin exactly that.
    """
    targets = _reviewed_export_targets(relative)
    assert len(targets) == expected_count, (
        f"{relative}: _EXPORTS now has {len(targets)} targets (reviewed: {expected_count}); "
        "re-review every new target for a cross-layer import before changing this number"
    )
    outside = [t for t in targets if not t.startswith(f"tensor_grep.{layer}.")]
    assert not outside, f"{relative} lazily imports across layers: {outside}"


def test_main_binding_late_import_targets_the_reviewed_cli_main_module() -> None:
    tree = ast.parse((_SRC_ROOT / "cli" / "_main_binding.py").read_text(encoding="utf-8"))
    values = [
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "_MAIN_MODULE" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert values == ["tensor_grep.cli.main"], f"_MAIN_MODULE changed: {values}"
