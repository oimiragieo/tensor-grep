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


def _resolve_relative_import(
    from_module: str, level: int, target: str | None, *, is_package: bool = False
) -> str | None:
    """Resolve a relative ``from ... import ...`` (``level`` dots) rooted at ``from_module``
    (the dotted name of the importing module itself) into an absolute dotted module name, per
    Python's own relative-import resolution rules (PEP 328): one dot climbs zero package levels
    beyond the immediate package, so ``level`` packages are stripped from ``from_module``'s
    dotted path before joining ``target``.

    ``is_package`` is True when the importer is a package's ``__init__.py``: that file IS the
    package, so its own dotted name is already the package (``tensor_grep.core``) and must not
    have a leaf stripped, or ``from .. import cli`` there resolves one level too high.
    """
    parts = from_module.split(".")
    package_parts = parts if is_package else parts[:-1]  # a plain module drops its own leaf
    climbed = package_parts[: len(package_parts) - (level - 1)] if level > 1 else package_parts
    if level > len(package_parts) + 1:
        return None  # climbs above the tensor_grep root -- not resolvable, not our concern
    base = ".".join(climbed)
    if not target:
        return base or None
    return f"{base}.{target}" if base else target


_DYNAMIC_CALLEES = frozenset({"import_module", "__import__", "run_module"})


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """True for ``import_module(...)`` / ``__import__(...)`` / ``runpy.run_module(...)`` under
    any receiver, and for the ``getattr(x, "import_module")(...)`` indirection.

    Deliberately receiver-agnostic: an unrelated ``registry.import_module("tensor_grep.cli.x")``
    also matches (a false edge) -- accepted over missing a real alias such as
    ``import importlib as il`` or ``builtins.__import__``.

    Not covered, by design: path-based loaders (``spec_from_file_location``), ``exec``/``eval``
    and ``ctypes`` -- they take no module name to resolve.
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


def _arg(node: ast.Call, index: int, keywords: tuple[str, ...]) -> ast.expr | None:
    """Positional ``index`` argument, else the first matching keyword argument."""
    if len(node.args) > index:
        return node.args[index]
    return next((kw.value for kw in node.keywords if kw.arg in keywords), None)


def _string_arg(node: ast.Call, index: int, keywords: tuple[str, ...]) -> str | None:
    value = _arg(node, index, keywords)
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _dynamic_import_literal(node: ast.Call) -> str | None:
    """Absolute module name of a dynamic import whose target is statically known, else ``None``.

    Resolves ``importlib.import_module("x")`` / ``import_module(name="x")`` / ``__import__("x")`` /
    ``runpy.run_module("x")`` and a relative ``import_module(".x", package="pkg")`` (resolved
    against the literal package). ``__import__`` with a non-zero ``level`` is relative to its
    caller's globals and is NOT absolute, so it is unresolved. Non-literal targets, a relative
    literal with no literal package, and the ``getattr`` indirection return ``None``;
    :func:`compute_unresolved_import_sites` surfaces those instead of letting them pass as
    "no edge".
    """
    func = node.func
    if isinstance(func, ast.Call) or not _is_dynamic_import_call(node):
        return None
    callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    name = _string_arg(node, 0, ("name", "mod_name"))
    if name is None:
        return None
    if callee == "__import__":
        level = _arg(node, 4, ("level",))
        if level is not None and not (isinstance(level, ast.Constant) and level.value == 0):
            return None
    if not name.startswith("."):
        return name
    if callee != "import_module":
        return None
    package = _string_arg(node, 1, ("package",))
    if package is None:
        return None
    try:
        return importlib.util.resolve_name(name, package)
    except (ValueError, ImportError):
        return None


def _is_submodule(src_root: Path, dotted: str) -> bool:
    """True when ``dotted`` (``tensor_grep.cli.runtime_paths``) is a real module or package on
    disk under ``src_root`` -- so ``from tensor_grep.cli import runtime_paths`` can be told apart
    from ``from tensor_grep.cli import some_function``.
    """
    prefix = "tensor_grep."
    if not dotted.startswith(prefix):
        return False
    rel = src_root / dotted[len(prefix) :].replace(".", "/")
    return rel.with_suffix(".py").is_file() or (rel / "__init__.py").is_file()


def _iter_parsed_modules(src_root: Path) -> Iterator[tuple[str, str, bool, ast.Module]]:
    """Yield ``(from_package, from_module, is_package, tree)`` for every module under one of the
    four top-level packages. Every check shares this one parse so none can disagree.
    """
    for path in src_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        from_pkg = _module_top_level_package(src_root, path)
        if from_pkg is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # A file this walker cannot parse is a silent false negative for the whole freeze
            # check, which defeats its purpose -- fail loud instead of skipping it.
            raise RuntimeError(f"import_edges: cannot parse {path}: {exc}") from exc
        yield from_pkg, _module_dotted_name(src_root, path), path.name == "__init__.py", tree


def _resolved_from_module(node: ast.ImportFrom, from_module: str, is_package: bool) -> str | None:
    if node.level and node.level > 0:
        return _resolve_relative_import(from_module, node.level, node.module, is_package=is_package)
    return node.module


def _iter_cross_package_imports(src_root: Path) -> Iterator[tuple[str, str, str, str]]:
    """Yield ``(from_package, from_module, to_package, to_module)`` for every cross-package
    import under ``src_root``.

    Both freeze checks consume this one walk, so the package-pair gate and the
    module-pair violation gate can never disagree about what an edge is.
    """
    for from_pkg, from_module, is_package, tree in _iter_parsed_modules(src_root):
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolved_from_module(node, from_module, is_package)
                if resolved is not None:
                    targets = [resolved]
                    # `from tensor_grep.cli import runtime_paths` / `from tensor_grep import cli`
                    # import a CHILD module; record it too, or it hides behind the parent edge
                    # (or, for the package root, behind no edge at all).
                    for alias in node.names:
                        child = f"{resolved}.{alias.name}"
                        if alias.name != "*" and _is_submodule(src_root, child):
                            targets.append(child)
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

    Walks ``ast.Import`` / ``ast.ImportFrom`` nodes (including the child module named by a
    ``from pkg import child``) plus dynamic imports whose module name is a string literal. An
    import whose target cannot be resolved statically yields no edge here;
    :func:`compute_unresolved_import_sites` surfaces those so the gap is frozen and reviewed
    rather than silently passing as "no edge".
    """
    return {
        (from_pkg, to_pkg)
        for from_pkg, _from_module, to_pkg, _to_module in _iter_cross_package_imports(src_root)
    }


def compute_unresolved_import_sites(src_root: Path) -> dict[str, int]:
    """``{dotted module: count}`` of import sites the walker cannot resolve to a module name:
    dynamic import calls with a non-literal or relative-without-package target (and the
    ``getattr(...)`` indirection), and a star import from the package ROOT (which imports
    whatever ``tensor_grep.__all__`` names).

    Such a site may hide a layering edge, so "no edge found" is not "no dependency". Freezing the
    COUNT per module (not just the name) means a second opaque call inside an already-frozen
    module fails too.
    """
    sites: dict[str, int] = {}
    for _from_pkg, from_module, is_package, tree in _iter_parsed_modules(src_root):
        for node in ast.walk(tree):
            opaque = False
            if isinstance(node, ast.Call):
                opaque = _is_dynamic_import_call(node) and _dynamic_import_literal(node) is None
            elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                opaque = _resolved_from_module(node, from_module, is_package) == "tensor_grep"
            if opaque:
                sites[from_module] = sites.get(from_module, 0) + 1
    return sites
