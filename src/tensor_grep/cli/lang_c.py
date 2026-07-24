"""C language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 3).

Ninth language expansion (top-10 language-support campaign, Phase 1 of C/C++ -- C++ is a
SEPARATE follow-up, out of scope here). Sibling of ``lang_go.py``/``lang_csharp.py``/
``lang_php.py`` (see those modules' docstrings for the full "PATH A Stage 1" framing). Plugs
into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` + the
``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- C gets its
OWN ``LanguageSpec`` entry, registered from ``repo_map.py``.

FOUNDATIONAL SCOPE (same tier Java/PHP/C#/Go landed at): this module lights up
``defs``/``source``/``imports``/``agent`` for ``.c`` files -- function definitions AND
prototypes (kind "function"), struct/union/enum definitions (kind "class", per the fail-closed
struct/union/enum -> "class" mapping this campaign's other languages already use), and typedefs
(kind "type", mirroring Go's ``type_spec`` -> "type" kind). The cross-file caller-graph
(``references_and_calls``/``file_imports_symbol_from_definition``/``import_update_target``/
``prime_repo_context``) is DEFERRED to a follow-up -- this module registers all four of those
``LanguageSpec`` fields as ``None``, exactly like PHP's own precedent. ``tg refs``/``tg
callers``/``tg blast-radius`` on a C symbol fall through to the generic
``_regex_references_and_calls`` text-heuristic path in repo_map.py (never a crash, never a
fabricated AST-verified match) -- unaffected by this module.

``.h`` is DELIBERATELY NOT claimed by this module. ``_provider_language_for_path`` (the LSP
provider dispatch, repo_map.py) already assigns every C/C++ header suffix (``.h``, ``.hh``,
``.hpp``, ``.hxx``) to ``"cpp"`` -- since tree-sitter-cpp is a strict grammar superset of C, a
future ``lang_cpp.py`` (Phase 2, not built here) is the natural owner of ``.h`` so that pure-C
headers still parse under the C++ grammar. Registering ``.h`` here too would make this module's
``language_id="c"`` disagree with that pre-existing "cpp" assignment and fail
``test_target_and_provider_language_agree_with_registry``.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended here exactly as it was for Go/PHP/C#): C
has NO regex-heuristic fallback. When the ``tree_sitter_c`` grammar package is not installed,
every extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent C file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

``#include`` IS a parse-tree node (tree-sitter-c does not run a preprocessor, so it never strips
or expands a ``#include`` directive) -- ``preproc_include`` with a ``path`` field whose node
SHAPE varies by include form (live-verified against a real tree_sitter_c 0.24.2 parse, not
guessed from the grammar README):
  ``#include <stdio.h>``     -> path field type ``system_lib_string``, text ``"<stdio.h>"``
  ``#include "local.h"``     -> path field type ``string_literal``, nested ``string_content``
  ``#include MACRO_HEADER``  -> path field type ``identifier`` (macro-expanded form)
  ``#include COMBINE(a,b)``  -> path field type ``call_expression`` (macro-combined form)
``_c_include_target_text`` strips the ``<...>``/``"..."`` delimiters where present so the
recorded ``module`` string is the bare target (``"stdio.h"``, not ``"<stdio.h>"``), matching how
every other language's extractor here records a delimiter-free module string. Resolution stays
HONEST-UNRESOLVED: repo_map.py's ``_resolve_raw_import_entry`` reports every row
``resolved=None, external=False`` (never a fabricated path, never a fabricated ``external=True``)
-- true ``#include`` -> file resolution has no standardized C manifest to resolve against (no
``go.mod``/``composer.json``/``.csproj`` equivalent) and stays deferred to BACKLOG, same as the
go/php/csharp resolvers.

DECLARATOR NAME RESOLUTION (the one genuinely new wrinkle vs Go/PHP/C#): a C declarator can nest
a name arbitrarily deep -- ``int *make_ptr(void)`` wraps ``function_declarator`` inside
``pointer_declarator``; ``typedef void (*FuncPtr)(int);`` wraps a ``parenthesized_declarator``
(exposing NO named "declarator" field of its own, unlike every other wrapper) around a
``pointer_declarator`` around the ``type_identifier`` leaf. ``_c_declarator_name_node`` handles
both: it follows the named "declarator" field where one exists, and falls back to the single
NAMED child when a wrapper (like ``parenthesized_declarator``) exposes none. Live-verified against
real parses of plain/pointer/array/function-pointer declarators (including the
``typedef char *(*ComplexFuncPtr)(int, char);`` double-wrap case) before being written here --
see the module's originating PR description for the exact fixtures probed.

A ``declaration`` node is ambiguous by TYPE ALONE -- it is a function PROTOTYPE
(``int add(int a, int b);``) or a plain variable declaration (``int counter = 0;``,
``extern int flag;``) depending purely on whether its declarator chain passes through a
``function_declarator``. ``_c_declarator_name_node`` also returns that boolean so the extractor
can gate: only a ``declaration`` whose chain passed through ``function_declarator`` is emitted
(as kind "function"); a plain variable declaration is silently excluded from the symbol table,
matching this module's foundational scope (top-level variables are not a tracked symbol kind
here, mirroring every other ``lang_*.py`` module -- none of them track module-level variables
either, except Go's explicit ``const_spec``/``var_spec`` kinds, which this module does not add).
A ``struct_specifier``/``union_specifier``/``enum_specifier`` is similarly ambiguous by type
alone -- ``struct Foo;`` (forward declaration) and ``struct Foo *p`` (usage as a type) both parse
as the SAME node type with no ``body`` field, indistinguishable from a real definition except by
checking for a ``body`` field's presence -- only a body-bearing specifier is emitted.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle (repo_map.py imports THIS module). Keep byte-identical to repo_map.py's twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` / ``_symbol_record``) -- and to
# ``lang_go.py``/``lang_csharp.py``/``lang_php.py``'s own copies of the same three -- if any of
# them ever change there.
# ---------------------------------------------------------------------------

_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _is_clean_symbol_name(name: str) -> bool:
    return bool(_CLEAN_SYMBOL_NAME_RE.match(name))


def _tree_sitter_node_text(source_bytes: bytes, node: Any) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _symbol_record(
    *,
    name: str,
    kind: str,
    file: Path,
    start_line: int,
    end_line: int | None = None,
) -> dict[str, Any]:
    normalized_end_line = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": kind,
        "file": str(file),
        "line": start_line,
        "start_line": start_line,
        "end_line": normalized_end_line,
    }


# ---------------------------------------------------------------------------
# Parser factory (clone of lang_go.py's ``_go_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _c_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_c
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_c.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# struct/union/enum specifiers collapse to kind "class" -- the fail-closed cross-language mapping
# this campaign's other struct-bearing languages already use (C#'s own struct/interface/enum ->
# "class" precedent).
_C_CLASS_LIKE_KINDS = frozenset({"struct_specifier", "union_specifier", "enum_specifier"})
# Node types a C def can appear as -- informational/documentation only (mirrors
# lang_go._GO_DEF_NODE_KINDS' role), matching LanguageSpec.def_node_kinds' "Stage 0:
# informational only" contract.
_C_DEF_NODE_KINDS = (
    "function_definition",
    "declaration",
    "struct_specifier",
    "union_specifier",
    "enum_specifier",
    "type_definition",
)
# _c_declarator_name_node's backstop hop limit: no real C declarator nests anywhere close to this
# deep (the deepest live-verified fixture -- `typedef char *(*ComplexFuncPtr)(int, char);`, a
# pointer-returning function-pointer typedef -- took 5 hops: pointer_declarator ->
# function_declarator -> parenthesized_declarator -> pointer_declarator -> type_identifier); this
# only guards against an unforeseen future grammar cycle, never hit in practice.
_MAX_DECLARATOR_HOPS = 64


def _c_declarator_name_node(declarator: Any) -> tuple[Any | None, bool]:
    """Descend a declarator chain to its innermost identifier/type_identifier NAME node, tracking
    whether the chain passed through a ``function_declarator`` along the way.

    Live-verified against real tree_sitter_c 0.24.2 parses (see the module docstring's
    "DECLARATOR NAME RESOLUTION" section) across every shape this module's callers hit:
    plain (``identifier``/``type_identifier`` directly), pointer (``pointer_declarator``),
    array (``array_declarator``), function (``function_declarator``, whose OWN "declarator"
    field is the plain name), pointer-to-function (``pointer_declarator`` wrapping
    ``function_declarator``), and the function-pointer typedef's ``parenthesized_declarator``
    (which, uniquely among these, exposes NO named "declarator" field -- the loop falls back to
    the wrapper's single NAMED child, which resolves through to the inner
    ``pointer_declarator``/name).

    Returns ``(None, seen_function)`` if no name-bearing leaf was found (e.g. an abstract
    declarator with no name, such as a bare ``void`` parameter) -- callers must check for
    ``None`` before using the result.
    """
    seen_function = False
    current = declarator
    hops = 0
    while current is not None and hops < _MAX_DECLARATOR_HOPS:
        hops += 1
        if current.type in {"identifier", "type_identifier", "field_identifier"}:
            return current, seen_function
        if current.type == "function_declarator":
            seen_function = True
        next_node = current.child_by_field_name("declarator")
        if next_node is not None:
            current = next_node
            continue
        named_children = [child for child in current.children if child.is_named]
        if len(named_children) == 1:
            current = named_children[0]
            continue
        return None, seen_function
    return None, seen_function


def _c_include_target_text(path_field: Any, source_bytes: bytes) -> str | None:
    """Return a ``preproc_include`` node's target text, quote/bracket-stripped where the include
    form carries delimiters. See the module docstring's ``#include`` node-shape table for the
    four forms this handles."""
    if path_field is None:
        return None
    if path_field.type == "system_lib_string":
        raw = _tree_sitter_node_text(source_bytes, path_field)
        if len(raw) >= 2 and raw[0] == "<" and raw[-1] == ">":
            return raw[1:-1]
        return raw
    if path_field.type == "string_literal":
        content_node = next(
            (child for child in path_field.children if child.type == "string_content"),
            None,
        )
        if content_node is not None:
            return _tree_sitter_node_text(source_bytes, content_node)
        # Fallback (mirrors lang_go.py's F11 quote-stripping fallback): an empty ``#include ""``
        # or an unusual grammar build with no ``string_content`` child still yields a clean
        # module string instead of silently dropping the row.
        raw = _tree_sitter_node_text(source_bytes, path_field)
        if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
            return raw[1:-1]
        return raw
    # identifier (macro name, e.g. MACRO_HEADER) / call_expression (macro-combined, e.g.
    # COMBINE(a,b)) / anything else: no delimiters to strip -- the raw text IS the honest
    # include target (this module never fabricates a resolved path for these either way; see
    # repo_map.py's `_resolve_raw_import_entry` "c" branch).
    return _tree_sitter_node_text(source_bytes, path_field)


def c_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``#include`` targets + function/struct/union/enum/typedef definitions from a C
    source file, one AST pass (mirrors ``lang_go.go_imports_and_symbols``'s shape).

    Defs covered: ``function_definition`` (kind "function"), a ``declaration`` whose declarator
    chain passes through a ``function_declarator`` (a prototype, kind "function" -- a plain
    variable declaration is excluded, see the module docstring), ``struct_specifier``/
    ``union_specifier``/``enum_specifier`` WITH a body (kind "class" -- a body-less
    forward-declaration/usage-as-type is excluded), and ``type_definition`` (kind "type", one
    record per declarator so ``typedef int A, B;`` yields both). Imports come from every
    ``preproc_include`` directive's target text (see ``_c_include_target_text``).
    """
    if path.suffix != ".c":
        return [], []

    parser = _c_parser()
    if parser is None:
        return [], []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS instead of recursion, matching lang_go.py's walkers (F26 precedent:
        # a pathologically deep AST can never raise RecursionError). Children pushed in reverse
        # so the leftmost child is popped (visited) first, preserving pre-order traversal. A
        # plain (non-recursive) stack walk also naturally reaches nodes nested inside
        # `preproc_ifdef`/`preproc_if`/`preproc_elif` guards (live-verified: both branches of an
        # `#ifdef`/`#else` parse simultaneously as ordinary children) -- no special-casing needed.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _c_include_target_text(path_field, source_bytes)
                if target:
                    imports.append(target)
            elif node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "declaration":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _c_declarator_name_node(declarator)
                    if not is_function or name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="function",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type in _C_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if body_field is not None and name_field is not None:
                    name = _node_text(name_field)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="class",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is None:
                        continue
                    name = _node_text(name_node)
                    if _is_clean_symbol_name(name):
                        symbols.append(
                            _symbol_record(
                                name=name,
                                kind="type",
                                file=path,
                                start_line=node.start_point[0] + 1,
                                end_line=node.end_point[0] + 1,
                            )
                        )
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


# #74-follow-up: `tg imports` foundational-tier extractor (mirrors repo_map.py's
# `_java_imports_with_lines` shape/role exactly). One row per `preproc_include` STATEMENT with
# its 1-based line number -- same extraction source as `c_imports_and_symbols` above (every
# `preproc_include`'s target text via `_c_include_target_text`), just line-tagged instead of
# deduped into a flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "c"
# branch keeps every row unresolved (resolved=None, external=False) -- true `#include` -> file
# resolution has no standardized C manifest to resolve against (see the module docstring), so a
# real path is not guessable without fabricating one.
def c_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".c":
        return []

    parser = _c_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    entries: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on c_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "preproc_include":
                path_field = node.child_by_field_name("path")
                target = _c_include_target_text(path_field, source_bytes)
                if target:
                    entries.append({
                        "module": target,
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def c_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every function/struct/union/enum/typedef matching *symbol* (mirrors
    the Go/C#/PHP ``*_parser_symbol_sources`` shape for the ``tg source`` command).

    A function/typedef appearing both as a prototype/forward form and a full definition emits a
    source block for EACH matching AST node (no dedup/preference between them) -- the same
    "every real AST node is a legitimate hit" behavior C#'s own module already ships (an
    interface method and its class implementation sharing a name both resolve as separate
    ``tg source`` blocks)."""
    if path.suffix != ".c":
        return []

    parser = _c_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    def _append(node: Any, kind: str) -> None:
        block = _node_text(node)
        if block and not block.endswith("\n"):
            block = f"{block}\n"
        sources.append({
            "name": symbol,
            "kind": kind,
            "file": str(path),
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "source": block,
        })

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on c_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "function_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type == "declaration":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, is_function = _c_declarator_name_node(declarator)
                    if is_function and name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "function")
                        break
            elif node_type in _C_CLASS_LIKE_KINDS:
                body_field = node.child_by_field_name("body")
                name_field = node.child_by_field_name("name")
                if (
                    body_field is not None
                    and name_field is not None
                    and _node_text(name_field) == symbol
                ):
                    _append(node, "class")
            elif node_type == "type_definition":
                for declarator in node.children_by_field_name("declarator"):
                    name_node, _seen_function = _c_declarator_name_node(declarator)
                    if name_node is not None and _node_text(name_node) == symbol:
                        _append(node, "type")
                        break
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


__all__ = [
    "c_imports_and_symbols",
    "c_imports_with_lines",
    "c_parser_symbol_sources",
]
