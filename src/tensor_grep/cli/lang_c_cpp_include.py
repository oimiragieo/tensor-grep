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

from tensor_grep.cli.repo_map_input_provenance import is_file_with_receipt, resolved_input_path

_INCLUDE_RE = re.compile(
    r'^[ \t]*#[ \t]*include[ \t]*(?:"([^"]+)"|<([^>]+)>)',
    re.MULTILINE,
)
_RAW_LITERAL_PREFIXES = ('u8R"', 'uR"', 'UR"', 'LR"', 'R"')

_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx"})
_HEADER_SUFFIXES = frozenset({".h", ".hh", ".hpp", ".hxx"})


def _splice_physical_lines(source: str) -> str:
    """Apply C/C++ phase-2 backslash-newline splicing in one pass."""
    pieces: list[str] = []
    cursor = 0
    start = 0
    size = len(source)
    while cursor < size:
        if source[cursor] != "\\" or cursor + 1 >= size:
            cursor += 1
            continue
        following = source[cursor + 1]
        splice_end = cursor + 2
        if following == "\r" and splice_end < size and source[splice_end] == "\n":
            splice_end += 1
        elif following not in "\r\n":
            cursor += 1
            continue
        pieces.append(source[start:cursor])
        cursor = splice_end
        start = cursor
    if not pieces:
        return source
    pieces.append(source[start:])
    return "".join(pieces)


def _raw_literal_end(source: str, start: int) -> int | None:
    """Return the end of a raw literal, consuming an unterminated valid opener to EOF."""
    prefix = next(
        (candidate for candidate in _RAW_LITERAL_PREFIXES if source.startswith(candidate, start)),
        None,
    )
    if prefix is None:
        return None
    delimiter_start = start + len(prefix)
    cursor = delimiter_start
    while cursor < len(source) and cursor - delimiter_start <= 16:
        char = source[cursor]
        if char == "(":
            delimiter = source[delimiter_start:cursor]
            terminator = f'){delimiter}"'
            closing = source.find(terminator, cursor + 1)
            return len(source) if closing < 0 else closing + len(terminator)
        if char.isspace() or char in ")\\":
            return None
        cursor += 1
    return None


def _quoted_literal_end(source: str, start: int, quote: str) -> int:
    cursor = start + 1
    while cursor < len(source):
        char = source[cursor]
        if char == "\\" and cursor + 1 < len(source):
            cursor += 2
            continue
        if char == quote:
            return cursor + 1
        if char in "\r\n":
            return cursor
        cursor += 1
    return cursor


def _mask_span(masked: list[str], source: str, start: int, end: int, fill: str) -> None:
    for cursor in range(start, end):
        if source[cursor] not in "\r\n":
            masked[cursor] = fill


def _mask_comments_and_raw_literals(source: str) -> str:
    """Mask non-code with disjoint one-pass spans; raw-literal lookup never rescans a suffix."""
    source = _splice_physical_lines(source)
    masked = list(source)
    cursor = 0
    size = len(source)
    while cursor < size:
        raw_end = _raw_literal_end(source, cursor)
        if raw_end is not None:
            # Unlike comments, a raw literal is a real token. Non-whitespace fill prevents a
            # following same-line #include from becoming a fabricated directive after masking.
            _mask_span(masked, source, cursor, raw_end, "x")
            cursor = raw_end
            continue
        if source.startswith("//", cursor):
            end = cursor + 2
            while end < size and source[end] not in "\r\n":
                end += 1
            _mask_span(masked, source, cursor, end, " ")
            cursor = end
            continue
        if source.startswith("/*", cursor):
            closing = source.find("*/", cursor + 2)
            end = size if closing < 0 else closing + 2
            _mask_span(masked, source, cursor, end, " ")
            cursor = end
            continue
        if (
            source[cursor] == "'"
            and cursor > 0
            and cursor + 1 < size
            and source[cursor - 1] in "0123456789abcdefABCDEF"
            and source[cursor + 1] in "0123456789abcdefABCDEF"
            and not (cursor >= 2 and source[cursor - 2 : cursor] == "u8")
        ):
            cursor += 1
            continue
        if source[cursor] in "\"'":
            cursor = _quoted_literal_end(source, cursor, source[cursor])
            continue
        cursor += 1
    return "".join(masked)


def iter_include_directives(source: str) -> list[tuple[str, bool]]:
    """Return ``(target, is_system)`` pairs for every quote/angle ``#include`` in *source*."""
    source = _mask_comments_and_raw_literals(source)
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
        importer = resolved_input_path(importer_path)
        root = resolved_input_path(repo_root) if repo_root is not None else None
    except OSError:
        return None

    search: list[Path] = []
    if not is_system:
        search.append(importer.parent)
    search.extend(_repo_include_roots(root))

    seen: set[Path] = set()
    for base in search:
        try:
            base_resolved = resolved_input_path(base)
        except OSError:
            continue
        if base_resolved in seen:
            continue
        seen.add(base_resolved)
        candidate = resolved_input_path(base_resolved / target)
        if root is not None:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
        if is_file_with_receipt(candidate):
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
        definition = resolved_input_path(definition_path)
        importer = resolved_input_path(importer_path)
    except OSError:
        return False

    acceptable: set[Path] = {definition}
    for sibling in definition_header_siblings(definition):
        try:
            if is_file_with_receipt(sibling):
                acceptable.add(resolved_input_path(sibling))
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
        resolved_dirs = {resolved_input_path(directory) for directory in definition_dirs}
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
