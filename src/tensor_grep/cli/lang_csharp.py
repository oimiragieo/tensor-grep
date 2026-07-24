"""C# language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 1).

Second language expansion beyond the original four (python/javascript/typescript/rust), added
alongside Go. Plugs into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` +
the ``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- C# gets
its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``, with zero special-casing beyond
the couple of dispatch sites documented in the module docstring there.

Like ``lang_go.py``, this module imports NOTHING from ``repo_map.py`` (``repo_map`` -> this
module, never the reverse -- ``repo_map.py`` needs to import this module to register C#'s
``LanguageSpec`` and to call its ``csharp_parser_symbol_sources`` directly at the ``tg source``
dispatch site; a reverse import would cycle). The handful of tiny helpers this module needs from
``repo_map.py`` are duplicated here instead of imported, matching ``lang_go.py``'s own precedent.

FOUNDATIONAL SCOPE: this module lights up ``defs``/``source``/``imports``/``agent`` for ``.cs``
files (symbols: class/interface/struct/enum/record declarations as kind "class", method/
constructor declarations as kind "function"; imports: dotted ``using``-directive namespace
names). The cross-file caller-graph (``references_and_calls`` / ``file_imports_symbol_from_
definition`` / ``import_update_target`` / per-repo-root context priming for a `.csproj`/
namespace-to-file resolver) is DEFERRED to a follow-up -- this module registers those
``LanguageSpec`` fields as ``None``, exactly like Go's own ``import_update_target=None`` gap.
``tg refs``/``tg callers``/`tg blast-radius`` on a C# symbol fall through to the generic
``_regex_references_and_calls`` text-heuristic path in repo_map.py (never a crash, never a
fabricated AST-verified match) -- unaffected by this module.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended here exactly as it was for Go): C# has NO
regex-heuristic fallback. When the ``tree_sitter_c_sharp`` grammar package is not installed,
every extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent C# file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Duplicated tiny helpers -- see the module docstring: no import from repo_map.py, to avoid an
# import cycle (repo_map.py imports THIS module). Keep byte-identical to repo_map.py's twins
# (``_tree_sitter_node_text`` / ``_is_clean_symbol_name`` / ``_symbol_record``) if any of them
# ever change there.
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
# Parser factory (clone of repo_map.py's ``_rust_parser`` / lang_go.py's ``_go_parser`` shape).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _csharp_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_c_sharp
    except ImportError:
        return None

    language = tree_sitter.Language(tree_sitter_c_sharp.language())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# Type-declaration node kinds -> symbol kind "class" (matches how tensor-grep already collapses
# TS interfaces/JS classes into a single "class" bucket for defs/orient rather than minting a
# kind per C# declaration form).
_CSHARP_CLASS_NODE_TYPES = frozenset({
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
})
# Member-declaration node kinds -> symbol kind "function".
_CSHARP_FUNCTION_NODE_TYPES = frozenset({"method_declaration", "constructor_declaration"})
# ``using_directive``'s target-namespace child is always the RIGHTMOST ``identifier``/
# ``qualified_name`` child regardless of modifier -- verified against the installed
# tree_sitter_c_sharp 0.23.x grammar's node shapes for all four directive forms:
#   using System;                              -> identifier
#   using System.Collections.Generic;          -> qualified_name
#   using MyAlias = System.Text.StringBuilder;  -> [identifier "MyAlias", qualified_name TARGET]
#   using static System.Math;                   -> qualified_name (after the "static" token)
#   global using System.Linq;                   -> qualified_name (after the "global"/"using" tokens)
# In the aliased form the ALIAS name is emitted FIRST (leftmost), so taking the last matching
# child is what discriminates "the namespace actually being imported" from "the local alias" --
# never the reverse (an alias is never emitted after its target).
_CSHARP_USING_TARGET_NODE_TYPES = frozenset({"identifier", "qualified_name"})
# Informational/documentation only (Stage 0/1 convention) -- no dispatch seam reads this field
# yet; see lang_registry.LanguageSpec.def_node_kinds docstring.
_CSHARP_DEF_NODE_KINDS = (
    "class_declaration",
    "interface_declaration",
    "struct_declaration",
    "enum_declaration",
    "record_declaration",
    "method_declaration",
    "constructor_declaration",
)


def _csharp_using_directive_target(node: Any, source_bytes: bytes) -> str | None:
    """Return a ``using_directive`` node's target namespace text, or ``None``.

    See ``_CSHARP_USING_TARGET_NODE_TYPES`` docstring comment above for why "last matching
    child" is correct across all four directive forms (plain/dotted/aliased/static/global).
    """
    target: Any | None = None
    for child in node.children:
        if child.type in _CSHARP_USING_TARGET_NODE_TYPES:
            target = child
    if target is None:
        return None
    return _tree_sitter_node_text(source_bytes, target)


def csharp_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``using``-directive namespace names + type/member declarations from a C# source
    file, one AST pass.

    Defs covered: ``class_declaration``/``interface_declaration``/``struct_declaration``/
    ``enum_declaration``/``record_declaration`` (kind "class"), and ``method_declaration``/
    ``constructor_declaration`` (kind "function", including an interface's body-less method
    signature -- the C# grammar reuses ``method_declaration`` for both an abstract interface
    member and a concrete class method, discriminated only by the presence of a ``body`` field,
    which this extractor does not need to distinguish since both are legitimate definitions).
    Imports come from every ``using_directive``'s target namespace (alias/static/global
    qualifiers do not change what gets recorded -- see ``_csharp_using_directive_target``).
    """
    if path.suffix != ".cs":
        return [], []

    parser = _csharp_parser()
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
        # Explicit-stack DFS (not recursion): a pathologically deep AST must never raise
        # RecursionError -- mirrors lang_go.py's F26 fix (audit #63) precedent, applied here
        # preemptively rather than retrofitted after an incident. Children are pushed in
        # reverse so the leftmost child is popped (and thus visited) first, preserving the
        # original pre-order traversal.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "using_directive":
                target_text = _csharp_using_directive_target(node, source_bytes)
                if target_text is not None:
                    imports.append(target_text)
            elif node_type in _CSHARP_CLASS_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    name = _node_text(name_node)
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
            elif node_type in _CSHARP_FUNCTION_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                if name_node is not None:
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
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def csharp_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every declaration matching *symbol* (mirrors the Rust/JS-TS/Go
    ``*_parser_symbol_sources`` shape for the ``tg source`` command)."""
    if path.suffix != ".cs":
        return []

    parser = _csharp_parser()
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

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on csharp_imports_and_symbols's
        # `_walk` above for the rationale/precedent.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            name_node: Any | None = None
            kind: str | None = None
            if node_type in _CSHARP_CLASS_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                kind = "class"
            elif node_type in _CSHARP_FUNCTION_NODE_TYPES:
                name_node = node.child_by_field_name("name")
                kind = "function"
            if name_node is not None and kind is not None and _node_text(name_node) == symbol:
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
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


__all__ = [
    "csharp_imports_and_symbols",
    "csharp_parser_symbol_sources",
]
