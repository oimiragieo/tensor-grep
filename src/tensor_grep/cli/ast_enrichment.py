"""AST Container Enrichment for search matches.

Extracts the enclosing AST syntax container (function, method, class)
for search hits using tree-sitter or AST symbol parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map

AST_ENRICH_FILE_LIMIT = 100


def enrich_match_with_container(
    file_path: Path | str,
    line_number: int,
    symbols_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any] | None:
    """Return the nearest enclosing AST container (function, class, method) for a file and line number.

    Returns dict with keys: 'name', 'kind', 'range' [start_line, end_line], or None if top-level / unparseable.
    """
    path_obj = Path(file_path)
    norm_path = str(path_obj.resolve())

    symbols: list[dict[str, Any]]
    if symbols_cache is not None and norm_path in symbols_cache:
        symbols = symbols_cache[norm_path]
    else:
        try:
            _, symbols = repo_map._imports_and_symbols_for_path(path_obj)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError):
            symbols = []
        if symbols_cache is not None:
            symbols_cache[norm_path] = symbols

    candidates: list[dict[str, Any]] = []
    for s in symbols:
        start_line = s.get("start_line", s.get("line"))
        end_line = s.get("end_line", start_line)
        if isinstance(start_line, int) and isinstance(end_line, int):
            if start_line <= line_number <= end_line:
                candidates.append(s)

    if not candidates:
        return None

    # Pick the smallest enclosing span
    candidates.sort(
        key=lambda s: (
            int(s.get("end_line", s.get("line", 0))) - int(s.get("start_line", s.get("line", 0)))
        )
    )
    smallest = candidates[0]
    start = int(smallest.get("start_line", smallest.get("line", 0)))
    end = int(smallest.get("end_line", start))
    return {
        "name": str(smallest.get("name", "")),
        "kind": str(smallest.get("kind", "")),
        "range": [start, end],
    }


def enrich_search_items_with_containers(
    files: list[Path | str],
    items: list[dict[str, Any]],
    file_limit: int = AST_ENRICH_FILE_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich a list of search result items with their enclosing AST container.

    Bounded by file_limit unique files to protect latency on large codebases.
    """
    unique_files: list[str] = []
    seen_files: set[str] = set()
    for item in items:
        p = item.get("path") or item.get("file")
        if p and str(p) not in seen_files:
            seen_files.add(str(p))
            unique_files.append(str(p))

    selected_files = set(unique_files[:file_limit])
    truncated = len(unique_files) > file_limit

    symbols_cache: dict[str, list[dict[str, Any]]] = {}
    enriched_items: list[dict[str, Any]] = []
    enriched_count = 0

    for item in items:
        p = str(item.get("path") or item.get("file") or "")
        line_no = item.get("line_number", item.get("line"))
        if p in selected_files and isinstance(line_no, int):
            container = enrich_match_with_container(p, line_no, symbols_cache)
            enriched_item = dict(item)
            if container:
                enriched_item["container"] = container
                enriched_count += 1
            enriched_items.append(enriched_item)
        else:
            enriched_items.append(item)

    diagnostics = {
        "total_files": len(unique_files),
        "parsed_files": min(len(unique_files), file_limit),
        "enriched_items": enriched_count,
        "truncated": truncated,
    }
    return enriched_items, diagnostics
