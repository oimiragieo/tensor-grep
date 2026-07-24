"""PHP language extractor for tensor-grep's multi-language symbol graph (PATH A Stage 1).

Sibling of ``lang_go.py`` (see that module's docstring for the full "PATH A Stage 1" framing).
Plugs into the ``lang_registry`` seam Stage 0 built (see ``lang_registry.py`` + the
``lang_registry.register_language(...)`` calls near the bottom of ``repo_map.py``) -- PHP gets
its OWN ``LanguageSpec`` entry, registered from ``repo_map.py``.

SCOPE (deliberately narrower than Go's Stage 1 landing): this module extracts DEFS + IMPORTS
only -- ``php_imports_and_symbols`` (the ``.py``/``.go`` "one AST pass" shape) and
``php_parser_symbol_sources`` (the ``tg source`` companion, mirroring
``lang_go.go_parser_symbol_sources``). The cross-file caller-graph
(``references_and_calls`` / ``file_imports_symbol_from_definition`` / ``import_update_target`` /
``prime_repo_context``) that Go's Stage 1 landing also shipped is DEFERRED to a follow-up --
PHP's ``LanguageSpec`` registers all four of those callables as ``None``. This is a strict
subset of Go's shape, not a new shape: ``_language_coverage_gaps_for_universe`` in repo_map.py
already treats ``import_update_target is None`` as an honest ``resolution_gaps`` entry (see the
"audit #81 #4" comment on that function), so `tg callers`/`tg blast-radius` stay honest about
PHP's current lack of reverse-import resolution instead of silently reading as a proven zero.

Like ``lang_go.py``, this module imports NOTHING from ``repo_map.py`` (``repo_map`` ->
``lang_php``, never the reverse -- ``repo_map.py`` needs to import this module to register PHP's
``LanguageSpec`` and to call ``php_imports_and_symbols``/``php_parser_symbol_sources`` directly at
the couple of per-language dispatch sites that mirror how it calls the Go equivalents; a reverse
import would cycle). The handful of tiny helpers this module needs from ``repo_map.py`` are
duplicated here instead of imported, matching ``lang_go.py``'s own precedent.

FAIL-CLOSED CONTRACT (Stage 0 honesty floor, extended to PHP like Go): PHP has NO
regex-heuristic fallback. When the ``tree_sitter_php`` grammar package is not installed, every
extractor in this module returns empty ([]/([], [])) rather than degrading to a regex/text
heuristic (unlike JS/TS/Rust, which all fall back to regex extraction when their own tree-sitter
grammar is missing). ``LanguageSpec.provenance_when_missing="grammar-missing"`` (NOT
``"regex-heuristic"``) is what makes ``_language_coverage_gaps_for_universe`` in repo_map.py
treat a grammar-absent PHP file as a genuine ``resolution_gaps`` entry instead of silently
reporting zero matches as if the symbol just did not exist.

GRAMMAR VARIANT: ``tree_sitter_php`` exposes two language functions -- ``language_php_only()``
(pure PHP, no surrounding markup) and ``language_php()`` (the full grammar: PHP embedded in an
HTML document). This module uses ``language_php()`` so a template-style ``.php`` file (HTML +
``<?php ... ?>`` blocks, common in real web-app repos) parses the same as a bare ``<?php`` file
instead of erroring on the HTML it doesn't expect.

KNOWN EXTRACTION GAPS (documented, not silent): ``namespace_use_clause`` import extraction only
recognizes the two forms named in the design (``use Foo\\Bar;`` / ``use Foo\\Bar as Baz;`` -- a
clause with a ``qualified_name`` child). Group-use (``use App\\Shared\\{Foo, Bar as Baz};``,
where each inner clause only carries a bare ``name`` child, not a ``qualified_name``) and
``use function ...`` / ``use const ...`` imports are not extracted -- verified via direct grammar
probing (they do not carry a ``qualified_name`` child in the shape this walk expects), not
guessed. Both degrade safely to "this clause contributes no import" rather than emitting a wrong
or partial path.
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
# ``lang_go.py``'s own copies of the same three -- if any of them ever change there.
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
def _php_parser() -> Any | None:
    try:
        import tree_sitter
        import tree_sitter_php
    except ImportError:
        return None

    # See the module docstring's "GRAMMAR VARIANT" note: language_php() (not
    # language_php_only()) so a template-style .php file (HTML + <?php ... ?>) parses too.
    language = tree_sitter.Language(tree_sitter_php.language_php())
    return tree_sitter.Parser(language)


# ---------------------------------------------------------------------------
# Defs + imports: one tree-sitter pass per file.
# ---------------------------------------------------------------------------

# node types a PHP def can appear as -- informational/documentation only (mirrors
# lang_go._GO_DEF_NODE_KINDS' role), matching LanguageSpec.def_node_kinds' "Stage 0:
# informational only" contract.
_PHP_DEF_NODE_KINDS = (
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
    "function_definition",
    "method_declaration",
)

_PHP_CLASS_LIKE_KINDS = frozenset({
    "class_declaration",
    "interface_declaration",
    "trait_declaration",
    "enum_declaration",
})
_PHP_FUNCTION_LIKE_KINDS = frozenset({"function_definition", "method_declaration"})


def php_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract ``use`` import paths + class/interface/trait/enum/function/method definitions
    from a PHP source file, one AST pass (mirrors ``lang_go.go_imports_and_symbols``'s shape).

    Defs covered: ``class_declaration``/``interface_declaration``/``trait_declaration``/
    ``enum_declaration`` (kind "class") and ``function_definition``/``method_declaration`` (kind
    "function"). Imports come from every ``namespace_use_clause``'s ``qualified_name`` child's
    raw text -- PHP's namespace separator is a BACKSLASH (``\\``), not a dot, so the recorded
    string is e.g. ``"App\\Contracts\\Named"``, preserved as-written (never rewritten to
    dot-form) so it feeds the reverse-import alias graph the same way Python's dotted
    ``node.module`` does today. An ``as`` alias is not recorded (matching how
    ``_python_imports_and_symbols`` records the source module path, not a locally bound name).
    See the module docstring's "KNOWN EXTRACTION GAPS" note for the two import forms this
    deliberately does not cover.
    """
    if path.suffix != ".php":
        return [], []

    parser = _php_parser()
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
        # so the leftmost child is popped (visited) first, preserving pre-order traversal.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            if node_type == "namespace_use_clause":
                qualified_name_node = next(
                    (child for child in node.children if child.type == "qualified_name"),
                    None,
                )
                if qualified_name_node is not None:
                    imports.append(_node_text(qualified_name_node))
            elif node_type in _PHP_CLASS_LIKE_KINDS:
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
            elif node_type in _PHP_FUNCTION_LIKE_KINDS:
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


# #74-follow-up: `tg imports` foundational-tier extractor (mirrors repo_map.py's
# `_java_imports_with_lines` shape/role exactly). One row per `namespace_use_clause` STATEMENT
# with its 1-based line number -- same extraction source/gaps as `php_imports_and_symbols` above
# (only a clause with a `qualified_name` child is recorded; see the module docstring's "KNOWN
# EXTRACTION GAPS" note for the group-use / `use function` / `use const` forms this does not
# cover), just line-tagged instead of deduped into a flat list.
#
# Deliberately NOT resolved to a target file: repo_map.py's `_resolve_raw_import_entry` "php"
# branch keeps every row unresolved, because PHP namespace-to-file resolution needs a PSR-4/
# composer.json autoload-map reader that does not exist yet (this module's `LanguageSpec`
# registers both `import_update_target` and `prime_repo_context` as `None` -- see repo_map.py),
# so a real path is not guessable without fabricating one.
def php_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    if path.suffix != ".php":
        return []

    parser = _php_parser()
    if parser is None:
        return []

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)

    def _node_text(node: Any) -> str:
        return _tree_sitter_node_text(source_bytes, node)

    entries: list[dict[str, Any]] = []

    def _walk(root: Any) -> None:
        # Explicit-stack DFS -- see the identical comment on php_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "namespace_use_clause":
                qualified_name_node = next(
                    (child for child in node.children if child.type == "qualified_name"),
                    None,
                )
                if qualified_name_node is not None:
                    entries.append({
                        "module": _node_text(qualified_name_node),
                        "line": node.start_point[0] + 1,
                    })
            stack.extend(reversed(node.children))

    _walk(tree.root_node)
    return entries


def php_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """Full source text of every class/interface/trait/enum/function/method matching *symbol*
    (mirrors the Go/Rust/JS-TS ``*_parser_symbol_sources`` shape for the ``tg source``
    command)."""
    if path.suffix != ".php":
        return []

    parser = _php_parser()
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
        # Explicit-stack DFS -- see the identical comment on php_imports_and_symbols's `_walk`.
        stack = [root]
        while stack:
            node = stack.pop()
            node_type = node.type
            name_node: Any | None = None
            kind: str | None = None
            if node_type in _PHP_CLASS_LIKE_KINDS:
                name_node = node.child_by_field_name("name")
                kind = "class"
            elif node_type in _PHP_FUNCTION_LIKE_KINDS:
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
    "php_imports_and_symbols",
    "php_parser_symbol_sources",
]
