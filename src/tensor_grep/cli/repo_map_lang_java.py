"""Java-specific import and symbol extraction lifted out of `repo_map`.

Split out of `repo_map.py` under docs/design/2026-08-19-split-floor-escape.md. `_java_parser`
deliberately stays in `repo_map`: the test suite monkeypatches it there, so a moved copy would
be the unpatched one.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.cli import lang_java

# Route A late binding (docs/design/2026-08-19-split-floor-escape.md). `_self` is
# `tensor_grep.cli.repo_map`, NOT this module: the test suite patches names there, and a
# bare call resolved through this file's globals would run the unpatched original while the
# test still passed. A plain import would be circular (repo_map imports this module at its
# top), so the runtime branch resolves through sys.modules on each attribute read. The
# TYPE_CHECKING branch never executes; it is what keeps mypy on the real signatures.
if TYPE_CHECKING:
    from tensor_grep.cli import repo_map as _self
else:

    class _RepoMapProxy:
        """Late-binding view of `tensor_grep.cli.repo_map`."""

        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            module = sys.modules.get("tensor_grep.cli.repo_map")
            if module is None:
                module = importlib.import_module("tensor_grep.cli.repo_map")
            return getattr(module, name)

    _self = _RepoMapProxy()


def _java_import_declaration_text(node: Any, source_bytes: bytes) -> str:
    text = _self._tree_sitter_node_text(source_bytes, node).strip()
    match = _self._JAVA_IMPORT_STRIP_RE.match(text)
    return match.group(1).strip() if match else text


def _java_imports_and_symbols(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """Foundational-tier Java extractor: classes/interfaces/enums/records/methods/constructors
    plus raw import declarations, in ONE tree walk (mirrors `_python_imports_and_symbols`'s
    combined shape, since Java -- like Python -- has no separate regex-heuristic extractor to
    split imports from symbols the way the JS/TS/Rust split does).

    Fail-closed like Go (mirrors `_python_imports_and_symbols`'s guard): parser-None or a
    read/parse error returns ``([], [])``, never a partial regex degrade -- Java has no regex
    fallback (see the `.java` branch in `_imports_and_symbols_for_path` below).
    """
    if path.suffix not in _self._JAVA_SUFFIXES:
        return [], []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return [], []
    _source, source_bytes, tree = parsed

    imports: list[str] = []
    symbols: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type == "import_declaration":
            imports.append(_java_import_declaration_text(node, source_bytes))
        elif node.type in _self._JAVA_SYMBOL_KIND_MAP:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None:
                name = _node_text(name_node)
                if _self._is_clean_symbol_name(name):
                    symbols.append(
                        _self._symbol_record(
                            name=name,
                            kind=_self._JAVA_SYMBOL_KIND_MAP[node.type],
                            file=path,
                            start_line=node.start_point[0] + 1,
                            end_line=node.end_point[0] + 1,
                        )
                    )
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    imports = sorted(dict.fromkeys(imports))
    symbols.sort(key=lambda item: (item["file"], item["line"], item["kind"], item["name"]))
    return imports, symbols


def _java_parser_symbol_sources(path: Path, symbol: str) -> list[dict[str, Any]]:
    """`tg source` extractor for Java -- exact source block for a named class/interface/enum/
    record/method/constructor. Mirrors `_rust_parser_symbol_sources` exactly, reusing the shared
    cached parse product (`_parsed_source_and_tree`) instead of re-parsing directly."""
    if path.suffix not in _self._JAVA_SUFFIXES:
        return []

    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed
    sources: list[dict[str, Any]] = []

    def _node_text(node: Any) -> str:
        return _self._tree_sitter_node_text(source_bytes, node)

    def _walk(node: Any) -> None:
        if node.type in _self._JAVA_SYMBOL_KIND_MAP:
            name_node = node.child_by_field_name("name")
            if name_node is None:
                for child in node.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is not None and _node_text(name_node) == symbol:
                block = _node_text(node)
                if block and not block.endswith("\n"):
                    block = f"{block}\n"
                sources.append({
                    "name": symbol,
                    "kind": _self._JAVA_SYMBOL_KIND_MAP[node.type],
                    "file": str(path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": block,
                })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    sources.sort(key=lambda item: (item["file"], item["start_line"], item["kind"], item["name"]))
    return sources


def _java_imports_with_lines(path: Path) -> list[dict[str, Any]]:
    """`tg imports` extractor for Java -- one row per `import_declaration` STATEMENT with its
    1-based line number (mirrors `_rust_imports_with_lines`'s shape/role exactly, but tree-sitter
    -backed rather than regex-backed since Java has no regex fallback)."""
    if path.suffix not in _self._JAVA_SUFFIXES:
        return []
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    if file_size > _self._max_parse_bytes():
        return []
    parsed = _self._parsed_source_and_tree(str(path))
    if parsed is None:
        return []
    _source, source_bytes, tree = parsed

    entries: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if node.type == "import_declaration":
            entries.append({
                "module": _java_import_declaration_text(node, source_bytes),
                "line": node.start_point[0] + 1,
            })
        for child in node.children:
            _walk(child)

    _walk(tree.root_node)
    return entries


def _java_references_and_calls_for_registry(
    path: Path,
    symbol: str,
    repo_root: Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Task 11A / F7 wave 1: forward definition_dirs so a Java receiver whose type resolves
    # through package/import into a selected definition's package directory earns the
    # cross-file confirmed band. `_java_parser()` is called HERE (not duplicated inside
    # lang_java.py) so grammar-presence has exactly one source of truth -- see lang_java.py's
    # module docstring for why a second factory would be unsafe.
    return lang_java.java_references_and_calls(
        path,
        symbol,
        repo_root,
        parser=_self._java_parser(),
        definition_dirs=definition_dirs,
    )
