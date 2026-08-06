"""Shared C/C++ ``#include`` path resolution (F7 Task 11 wave 3).

One engine, two thin adapters (``lang_c.c_file_imports_symbol_from_definition`` /
``lang_cpp.cpp_file_imports_symbol_from_definition``). No import of ``repo_map`` (avoids the
cycle every language module documents).

Resolution contract (fail closed -- never fabricate an on-disk path):

- Quoted ``#include "..."``: search the importer's directory first, then *repo_root* and
  conventional ``include`` / ``inc`` roots under it (when *repo_root* is supplied).
- Angle ``#include <...>``: only those same repo-rooted include directories (NOT the importer
  directory) -- a system header like ``<stdio.h>`` that is not present under the repo stays
  unresolved.
- Macro / call-form includes (no quote/angle delimiters) are ignored -- tree-sitter records
  them but we cannot resolve them without a preprocessor.
- A resolved include matches a definition when it IS the definition path, OR when the
  definition is a translation-unit source (``.c`` / ``.cc`` / ``.cpp`` / ``.cxx``) and the
  include resolves to a same-stem header sibling in that definition's directory
  (``.h`` / ``.hh`` / ``.hpp`` / ``.hxx``). Same-stem decoys in OTHER directories do not match.
"""

from __future__ import annotations

import re
from pathlib import Path

_INCLUDE_RE = re.compile(
    r'^[ \t]*#[ \t]*include[ \t]*(?:"([^"]+)"|<([^>]+)>)',
    re.MULTILINE,
)

_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx"})
_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx"})


def iter_include_directives(source: str) -> list[tuple[str, bool]]:
    """Return ``(target, is_system)`` pairs for every quote/angle ``#include`` in *source*."""
    results: list[tuple[str, bool]] = []
    for match in _INCLUDE_RE.finditer(source):
        quoted, angled = match.group(1), match.group(2)
        if quoted is not None:
            results.append((quoted, False))
        elif angled is not None:
            results.append((angled, True))
    return results


def _repo_include_roots(repo_root: Path | None) -> list[Path]:
    if repo_root is None:
        return []
    roots = [repo_root]
    for name in ("include", "inc"):
        roots.append(repo_root / name)
    return roots


def resolve_include_target(
    importer_path: Path,
    target: str,
    *,
    is_system: bool,
    repo_root: Path | str | None = None,
) -> Path | None:
    """Resolve *target* against include search roots. Returns None when not found in-repo."""
    target = target.strip().replace("\\", "/")
    if not target or target.startswith("/") or re.match(r"^[A-Za-z]:/", target):
        # Absolute / drive-rooted includes are toolchain-owned; refuse rather than invent.
        return None

    try:
        importer = importer_path.expanduser().resolve()
        root = Path(repo_root).expanduser().resolve() if repo_root is not None else None
    except OSError:
        return None

    search: list[Path] = []
    if not is_system:
        search.append(importer.parent)
    search.extend(_repo_include_roots(root))

    seen: set[Path] = set()
    for base in search:
        try:
            base_resolved = base.resolve()
        except OSError:
            continue
        if base_resolved in seen:
            continue
        seen.add(base_resolved)
        candidate = (base_resolved / target).resolve()
        if root is not None:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
        if candidate.is_file():
            return candidate
    return None


def definition_header_siblings(definition_path: Path) -> list[Path]:
    """Same-stem header siblings beside a ``.c``/``.cpp``/… definition (empty for headers)."""
    if definition_path.suffix.lower() not in _SOURCE_SUFFIXES:
        return []
    parent = definition_path.parent
    stem = definition_path.stem
    return [parent / f"{stem}{suffix}" for suffix in sorted(_HEADER_SUFFIXES)]


def file_includes_definition(
    importer_path: Path,
    source: str,
    definition_path: str,
    repo_root: Path | str | None = None,
) -> bool:
    """True iff *importer_path*'s includes resolve to *definition_path* (or its header sibling)."""
    try:
        definition = Path(definition_path).expanduser().resolve()
        importer = importer_path.expanduser().resolve()
    except OSError:
        return False

    acceptable: set[Path] = {definition}
    for sibling in definition_header_siblings(definition):
        try:
            if sibling.is_file():
                acceptable.add(sibling.resolve())
        except OSError:
            continue

    for target, is_system in iter_include_directives(source):
        resolved = resolve_include_target(
            importer, target, is_system=is_system, repo_root=repo_root
        )
        if resolved is not None and resolved in acceptable:
            return True
    return False


def include_resolves_into_definition_dirs(
    importer_path: Path,
    source: str,
    definition_dirs: frozenset[str] | set[str],
    repo_root: Path | str | None = None,
) -> bool:
    """True iff any resolved include lands inside one of *definition_dirs*.

    Used by ``c_references_and_calls`` / ``cpp_references_and_calls`` to elevate bare
    cross-file calls to the include-path confirmed band when *definition_dirs* is the
    selected-definition set supplied by ``repo_map``.
    """
    if not definition_dirs:
        return False
    try:
        resolved_dirs = {Path(directory).expanduser().resolve() for directory in definition_dirs}
    except OSError:
        return False

    for target, is_system in iter_include_directives(source):
        resolved = resolve_include_target(
            importer_path, target, is_system=is_system, repo_root=repo_root
        )
        if resolved is None:
            continue
        if resolved.parent in resolved_dirs:
            return True
    return False


__all__ = [
    "definition_header_siblings",
    "file_includes_definition",
    "include_resolves_into_definition_dirs",
    "iter_include_directives",
    "resolve_include_target",
]
