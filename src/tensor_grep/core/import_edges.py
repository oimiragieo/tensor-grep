"""Top-level package-to-package import graph for src/tensor_grep, used to freeze the current
layering before any extraction touches it (P13 extension, Task 07: "freeze import edges then
extract shared CLI/MCP services").

This is a dependency-free equivalent of an import-linter "contract" check: it does not enforce
a DIRECTION (P13's original import-linter adoption is still open), it only detects a NEW edge
appearing between the top-level packages (cli/core/backends/io) that wasn't present when the
baseline was captured. A CLI/MCP service extraction that accidentally introduces a fresh
cross-package dependency will fail this check even before import-linter itself lands.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

TOP_LEVEL_PACKAGES = ("cli", "core", "backends", "io")

#: The package-pair edges the frozen baseline itself labels as PRE-EXISTING LAYERING
#: VIOLATIONS (see ``docs/design/2026-09-07-import-edges-baseline.json``). The package-pair
#: freeze cannot ratchet these down: once ``("core", "cli")`` is in the baseline set, an
#: unbounded number of NEW ``core -> cli`` imports pass it, because the set records which
#: KINDS of edge exist and not how many or which modules carry them. That is the
#: "a baseline that legitimizes every regression it captures" failure. These pairs are
#: therefore additionally frozen at MODULE granularity by
#: :func:`compute_violation_module_edges`, so the violation class can only shrink.
VIOLATION_PACKAGE_EDGES = (("core", "cli"), ("backends", "cli"))


def _module_top_level_package(src_root: Path, path: Path) -> str | None:
    try:
        rel = path.relative_to(src_root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    top = rel.parts[0]
    return top if top in TOP_LEVEL_PACKAGES else None


def _imported_top_level_package(module_name: str) -> str | None:
    prefix = "tensor_grep."
    if not module_name.startswith(prefix):
        return None
    rest = module_name[len(prefix) :]
    top = rest.split(".", 1)[0]
    return top if top in TOP_LEVEL_PACKAGES else None


def _module_dotted_name(src_root: Path, path: Path) -> str:
    """Dotted module name of ``path`` as Python would import it, e.g.
    ``tensor_grep.cli.main`` or ``tensor_grep.cli`` for an ``__init__.py``.
    """
    rel = path.relative_to(src_root.parent)  # keep the leading "tensor_grep" segment
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative_import(from_module: str, level: int, target: str | None) -> str | None:
    """Resolve a relative ``from ... import ...`` (``level`` dots) rooted at ``from_module``
    (the dotted name of the importing module itself) into an absolute dotted module name, per
    Python's own relative-import resolution rules (PEP 328): one dot climbs zero package levels
    beyond the immediate package, so ``level`` packages are stripped from ``from_module``'s
    dotted path before joining ``target``.
    """
    package_parts = from_module.split(".")[:-1]  # drop the module's own leaf name
    climbed = package_parts[: len(package_parts) - (level - 1)] if level > 1 else package_parts
    if level > len(package_parts) + 1:
        return None  # climbs above the tensor_grep root -- not resolvable, not our concern
    base = ".".join(climbed)
    if not target:
        return base or None
    return f"{base}.{target}" if base else target


def _iter_cross_package_imports(src_root: Path) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(from_package, from_module, to_package, to_module)`` for every static
    cross-package import under ``src_root``.

    Both freeze checks consume this one walk, so the package-pair gate and the
    module-pair violation gate can never disagree about what an edge is.
    """
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        from_pkg = _module_top_level_package(src_root, path)
        if from_pkg is None:
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # A file this walker cannot parse is a silent false negative for the whole freeze
            # check, which defeats its purpose -- fail loud instead of skipping it.
            raise RuntimeError(f"import_edges: cannot parse {path}: {exc}") from exc
        from_module = _module_dotted_name(src_root, path)
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    resolved = _resolve_relative_import(from_module, node.level, node.module)
                    targets = [resolved] if resolved is not None else []
                elif node.module is not None:
                    targets = [node.module]
            for target in targets:
                to_pkg = _imported_top_level_package(target)
                if to_pkg is not None and to_pkg != from_pkg:
                    yield from_pkg, from_module, to_pkg, target


def compute_violation_module_edges(src_root: Path) -> set[tuple[str, str]]:
    """Return ``(from_module, to_module)`` for every import whose package pair is in
    :data:`VIOLATION_PACKAGE_EDGES`.

    This is the ratchet the package-pair freeze cannot provide. Freezing THIS set means a
    declared layering violation can only be removed, never added to -- a new
    ``core -> cli`` import fails even though ``("core", "cli")`` is already an accepted
    package-pair edge.
    """
    violations = set(VIOLATION_PACKAGE_EDGES)
    return {
        (from_module, to_module)
        for from_pkg, from_module, to_pkg, to_module in _iter_cross_package_imports(src_root)
        if (from_pkg, to_pkg) in violations
    }


def compute_import_edges(src_root: Path) -> set[tuple[str, str]]:
    """Return the set of (from_package, to_package) edges actually present under ``src_root``
    (expected to be .../src/tensor_grep), considering only the four top-level packages this
    repo's layering convention names. Edges are (source_package, imported_package); a package
    importing itself is excluded.

    Known gap (documented, not silently claimed complete): this walks ``ast.Import`` /
    ``ast.ImportFrom`` nodes only. A dynamic import (``importlib.import_module(...)``,
    ``__import__(...)``) that names a cross-package module by a string literal is NOT detected.
    Static import statements are this repo's overwhelming convention; a dynamic-import scanner
    is separate, unstarted scope for a future P13 slice.
    """
    return {
        (from_pkg, to_pkg)
        for from_pkg, _from_module, to_pkg, _to_module in _iter_cross_package_imports(src_root)
    }
