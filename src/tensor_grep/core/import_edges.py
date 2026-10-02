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
import importlib.util
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


_DYNAMIC_CALLEES = frozenset({"import_module", "__import__"})


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """True for ``import_module(...)`` / ``__import__(...)`` under any receiver, and for the
    ``getattr(x, "import_module")(...)`` indirection.

    Deliberately receiver-agnostic: an unrelated ``registry.import_module("tensor_grep.cli.x")``
    also matches (a false edge) -- accepted over missing a real alias such as
    ``import importlib as il`` or ``builtins.__import__``.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _DYNAMIC_CALLEES
    if isinstance(func, ast.Name):
        return func.id in _DYNAMIC_CALLEES
    if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "getattr":
        name_arg = func.args[1] if len(func.args) > 1 else None
        return (
            isinstance(name_arg, ast.Constant)
            and isinstance(name_arg.value, str)
            and name_arg.value in _DYNAMIC_CALLEES
        )
    return False


def _string_arg(node: ast.Call, index: int, keyword: str) -> str | None:
    """Positional ``index`` or keyword ``keyword`` argument when it is a string literal."""
    value: ast.expr | None = node.args[index] if len(node.args) > index else None
    if value is None:
        value = next((kw.value for kw in node.keywords if kw.arg == keyword), None)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _dynamic_import_literal(node: ast.Call) -> str | None:
    """Absolute module name of a dynamic import whose target is statically known, else ``None``.

    Resolves ``importlib.import_module("x")`` / ``import_module(name="x")`` / ``__import__("x")``
    and a relative ``import_module(".x", package="pkg")`` (resolved against the literal package).
    Non-literal targets, a relative literal with no literal package, and the ``getattr``
    indirection return ``None``; :func:`compute_unresolved_dynamic_import_modules` surfaces those
    instead of letting them pass as "no edge".
    """
    func = node.func
    if isinstance(func, ast.Call) or not _is_dynamic_import_call(node):
        return None
    name = _string_arg(node, 0, "name")
    if name is None:
        return None
    if not name.startswith("."):
        return name
    callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if callee != "import_module":
        return None  # __import__'s relativity is a separate `level` argument, not a leading dot
    package = _string_arg(node, 1, "package")
    if package is None:
        return None
    try:
        return importlib.util.resolve_name(name, package)
    except (ValueError, ImportError):
        return None


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
                resolved = None
                if node.level and node.level > 0:
                    resolved = _resolve_relative_import(from_module, node.level, node.module)
                elif node.module is not None:
                    resolved = node.module
                targets = [resolved] if resolved is not None else []
                if resolved == "tensor_grep":
                    # `from tensor_grep import cli` / `from .. import cli` import the cli PACKAGE;
                    # the module alone ("tensor_grep") names no layer, so expand the names.
                    targets += [f"tensor_grep.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Call):
                literal = _dynamic_import_literal(node)
                if literal is not None:
                    targets = [literal]
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

    Walks ``ast.Import`` / ``ast.ImportFrom`` nodes plus dynamic imports whose module name is a
    string literal (``importlib.import_module("x")``, ``import_module("x")``, ``__import__("x")``).
    A dynamic import with a NON-literal target cannot be resolved statically and yields no edge
    here; :func:`compute_unresolved_dynamic_import_modules` surfaces those modules so the gap is
    frozen and reviewed rather than silently passing as "no edge".
    """
    return {
        (from_pkg, to_pkg)
        for from_pkg, _from_module, to_pkg, _to_module in _iter_cross_package_imports(src_root)
    }


def compute_unresolved_dynamic_import_modules(src_root: Path) -> set[str]:
    """Dotted names of modules containing a dynamic import call whose target is NOT statically
    resolvable (variable / f-string argument, relative literal without a literal package, or the
    ``getattr(...)`` indirection).

    Such a call may hide a layering edge the walker cannot see, so "no edge found" is not "no
    dependency". Freezing this set by module name makes a NEW opaque import call fail until it is
    resolved to a literal or consciously waived.
    """
    unresolved: set[str] = set()
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts or _module_top_level_package(src_root, path) is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"import_edges: cannot parse {path}: {exc}") from exc
        module = _module_dotted_name(src_root, path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and _is_dynamic_import_call(node)
                and _dynamic_import_literal(node) is None
            ):
                unresolved.add(module)
                break
    return unresolved
