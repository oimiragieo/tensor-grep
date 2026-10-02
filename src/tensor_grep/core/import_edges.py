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


#: ``(module aliases, function aliases)`` through which a module binds ``pkgutil`` and
#: ``pkgutil.resolve_name``. ``pkgutil.resolve_name`` IMPORTS the named module, but a bare
#: ``resolve_name`` is only that function when it came from pkgutil -- ``importlib.util
#: .resolve_name`` merely resolves a relative name and imports nothing -- so it is matched by
#: binding, not by name.
PkgutilScope = tuple[frozenset[str], frozenset[str]]
_NO_PKGUTIL: PkgutilScope = (frozenset(), frozenset())


def _pkgutil_scope(tree: ast.AST) -> PkgutilScope:
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "pkgutil")
        elif isinstance(node, ast.ImportFrom) and node.module == "pkgutil" and not node.level:
            functions.update(a.asname or a.name for a in node.names if a.name == "resolve_name")
    return frozenset(modules), frozenset(functions)


def _is_pkgutil_resolve_call(node: ast.Call, scope: PkgutilScope) -> bool:
    modules, functions = scope
    func = node.func
    if isinstance(func, ast.Attribute):
        return (
            func.attr == "resolve_name"
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        )
    return isinstance(func, ast.Name) and func.id in functions


def _is_dynamic_import_call(node: ast.Call, scope: PkgutilScope = _NO_PKGUTIL) -> bool:
    """True for ``import_module(...)`` / ``__import__(...)`` / ``runpy.run_module(...)`` under
    any receiver, for ``pkgutil.resolve_name(...)`` (see :data:`PkgutilScope`), and for the
    ``getattr(x, "import_module")(...)`` indirection.

    Deliberately receiver-agnostic: an unrelated ``registry.import_module("tensor_grep.cli.x")``
    also matches (a false edge) -- accepted over missing a real alias such as
    ``import importlib as il`` or ``builtins.__import__``.

    Not covered, by design: path-based loaders (``spec_from_file_location``), ``exec``/``eval``
    and ``ctypes`` -- they take no module name to resolve.
    """
    func = node.func
    if _is_pkgutil_resolve_call(node, scope):
        return True
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


def _dynamic_import_literal(node: ast.Call, scope: PkgutilScope = _NO_PKGUTIL) -> str | None:
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
    if isinstance(func, ast.Call) or not _is_dynamic_import_call(node, scope):
        return None
    callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    name = _string_arg(node, 0, ("name", "mod_name"))
    if name is None:
        return None
    if _is_pkgutil_resolve_call(node, scope):
        # pkgutil's `pkg.mod:attr` form names the module before the colon; it takes no relative
        # names, so a leading dot is unresolved.
        return None if name.startswith(".") else name.split(":", 1)[0]
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
    """True when ``dotted`` (``tensor_grep.cli.runtime_paths``) is something Python can import
    on disk under ``src_root`` -- a ``.py`` module, a package (with or without ``__init__.py``,
    i.e. a namespace package) or a compiled ``.pyd``/``.so`` extension -- so
    ``from tensor_grep.cli import runtime_paths`` can be told apart from
    ``from tensor_grep.cli import some_function``.
    """
    prefix = "tensor_grep."
    if not dotted.startswith(prefix):
        return False
    rel = src_root / dotted[len(prefix) :].replace(".", "/")
    if rel.is_dir():
        return True
    return any(
        candidate.suffix in {".py", ".pyd", ".so"} for candidate in rel.parent.glob(f"{rel.name}.*")
    )


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
        scope = _pkgutil_scope(tree)
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
                literal = _dynamic_import_literal(node, scope)
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


def compute_unresolved_import_sites(src_root: Path) -> dict[str, list[str]]:
    """``{dotted module: sorted source text of each site}`` for import sites the walker cannot
    resolve to a module name: dynamic import calls with a non-literal or relative-without-package
    target (and the ``getattr(...)`` indirection), and a star import from the package ROOT or from
    ANOTHER layer package (which imports whatever ``__all__`` names).

    Such a site may hide a layering edge, so "no edge found" is not "no dependency". Each site is
    recorded by its ``ast.unparse`` text, not a count: a count stays at 1 while the call's target
    expression is edited to import another layer, so freezing the TEXT makes any edit to a frozen
    site (as well as a new one) show up as a site the baseline does not list. The text is also
    what a reviewer reads. (``ast.unparse``, not ``ast.dump``: dump's output changed in 3.13.)
    """
    sites: dict[str, list[str]] = {}
    layer_modules = {f"tensor_grep.{pkg}" for pkg in TOP_LEVEL_PACKAGES}
    for from_pkg, from_module, is_package, tree in _iter_parsed_modules(src_root):
        scope = _pkgutil_scope(tree)
        for node in ast.walk(tree):
            opaque = False
            if isinstance(node, ast.Call):
                opaque = (
                    _is_dynamic_import_call(node, scope)
                    and _dynamic_import_literal(node, scope) is None
                )
            elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
                resolved = _resolved_from_module(node, from_module, is_package)
                # A star import loads whatever `__all__` names: the package root or ANOTHER layer
                # package may pull in children the walker cannot enumerate. Its own package is
                # not a crossing.
                opaque = resolved == "tensor_grep" or (
                    resolved in layer_modules and resolved != f"tensor_grep.{from_pkg}"
                )
            if opaque:
                sites.setdefault(from_module, []).append(ast.unparse(node))
    return {module: sorted(texts) for module, texts in sites.items()}
