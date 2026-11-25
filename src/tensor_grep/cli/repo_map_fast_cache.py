"""Modular helpers for canonical cache root, fast Python import matching, and importer tagging."""

from __future__ import annotations

import ast
import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Each entry: (is_from_import, module_name, level, tuple_of_(name, asname))
_ImportSummary = tuple[tuple[bool, str | None, int, tuple[tuple[str, str | None], ...]], ...]


def canonical_symbol_cache_root(
    scope: Path,
    infer_project_root: Callable[[Path], Path],
    authenticated_root: Path | None,
) -> Path:
    """Anchor `.tg_cache` to `_infer_project_root(scope)` within confinement bounds."""
    if authenticated_root is not None:
        if os.path.normcase(os.path.abspath(scope)) == os.path.normcase(
            os.path.abspath(authenticated_root)
        ):
            return scope
    cache_root = infer_project_root(scope)
    temp_root = Path(tempfile.gettempdir()).resolve()
    try:
        scope.resolve().relative_to(temp_root)
        scope_in_temp = True
    except ValueError:
        scope_in_temp = False
    if scope_in_temp:
        try:
            temp_root.relative_to(cache_root.resolve())
            escaped_above_temp = True
        except ValueError:
            escaped_above_temp = False
        if escaped_above_temp:
            cache_root = scope if scope.is_dir() else scope.parent
    if authenticated_root is not None:
        try:
            cache_root.relative_to(authenticated_root)
        except ValueError:
            cache_root = authenticated_root
    return cache_root


_IMPORT_SUMMARY_CACHE: dict[str, _ImportSummary | None] = {}
_IMPORT_SUMMARY_CACHE_MAX = 512


def clear_python_import_summary_cache() -> None:
    _IMPORT_SUMMARY_CACHE.clear()


def _get_or_compute_import_summary(
    cache_key: str,
    source: str,
    cached_ast_parse: Callable[[str], ast.Module],
) -> _ImportSummary | None:
    if cache_key in _IMPORT_SUMMARY_CACHE:
        return _IMPORT_SUMMARY_CACHE[cache_key]
    try:
        tree = cached_ast_parse(source)
    except SyntaxError:
        summary: _ImportSummary | None = None
    else:
        records: list[tuple[bool, str | None, int, tuple[tuple[str, str | None], ...]]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                records.append((
                    False,
                    None,
                    0,
                    tuple((alias.name, alias.asname) for alias in node.names),
                ))
            elif isinstance(node, ast.ImportFrom):
                records.append((
                    True,
                    node.module,
                    int(node.level or 0),
                    tuple((alias.name, alias.asname) for alias in node.names),
                ))
        summary = tuple(records)
    if len(_IMPORT_SUMMARY_CACHE) >= _IMPORT_SUMMARY_CACHE_MAX:
        _IMPORT_SUMMARY_CACHE.clear()
    _IMPORT_SUMMARY_CACHE[cache_key] = summary
    return summary


def python_file_imports_symbol_fast(
    source: str,
    symbol: str,
    definition_path: str,
    module_path_matches_definition: Callable[[str, str], bool],
    cached_ast_parse: Callable[[str], ast.Module],
) -> bool:
    """Check whether Python `source` imports `symbol` from `definition_path` using digest-keyed import summaries."""
    digest = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()
    summary = _get_or_compute_import_summary(f"{digest}:{len(source)}", source, cached_ast_parse)
    if summary is None:
        return False
    for is_from, module, level, names in summary:
        if not is_from:
            if any(module_path_matches_definition(name, definition_path) for name, _ in names):
                return True
        elif module:
            if not module_path_matches_definition(module, definition_path):
                continue
            if any(name in {"*", symbol} or asname == symbol for name, asname in names):
                return True
        elif level:
            if any(module_path_matches_definition(name, definition_path) for name, _ in names):
                return True
    return False


def tag_and_count_importers(
    edges: list[dict[str, Any]],
    is_test_file: Callable[[Path], bool],
) -> tuple[int, int]:
    """Tag each importer edge with `is_test` and return `(production_count, test_count)`."""
    prod_count = 0
    test_count = 0
    for edge in edges:
        is_test = bool(is_test_file(Path(str(edge["file"]))))
        edge["is_test"] = is_test
        if is_test:
            test_count += 1
        else:
            prod_count += 1
    return prod_count, test_count


def importers_all_files(repo_map: dict[str, Any]) -> list[str]:
    """Return deduplicated source and test files from a repo map for reverse-importer lookup."""
    return list(
        dict.fromkeys([
            *(str(current) for current in repo_map.get("files", [])),
            *(str(current) for current in repo_map.get("tests", [])),
        ])
    )


def finalize_importers_payload(
    payload: dict[str, Any],
    edges: list[dict[str, Any]],
    is_test_file: Callable[[Path], bool],
) -> None:
    """Tag importer edges with `is_test` and populate importer counts on payload."""
    prod_count, test_count = tag_and_count_importers(edges, is_test_file)
    payload["importers"] = edges
    payload["importer_files"] = sorted(dict.fromkeys(str(item["file"]) for item in edges))
    payload["importer_count"] = len(edges)
    payload["production_importer_count"] = prod_count
    payload["test_importer_count"] = test_count
