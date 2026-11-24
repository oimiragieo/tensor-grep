"""LocAgent-style symbol-reachable pruning for reverse import blast-radius graphs."""

from __future__ import annotations

import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

_MAX_PRUNER_FILE_BYTES = 512 * 1024


def _file_mentions_symbol(path_str: str, pattern: re.Pattern[str]) -> bool:
    """Return True if `path_str` mentions `pattern`, failing open on unreadable/oversized files."""
    from tensor_grep.cli import repo_map

    try:
        if os.path.getsize(path_str) > _MAX_PRUNER_FILE_BYTES:
            return True
    except OSError:
        return True
    try:
        text = repo_map._read_source_text_cached(path_str)
    except (OSError, ValueError):
        try:
            text = Path(path_str).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
    return bool(pattern.search(text))


def prune_symbol_unreachable_importers(
    symbol_name: str,
    definition_files: list[str] | set[str],
    reverse_importers: dict[str, set[str]],
    all_files: list[str] | set[str],
    *,
    imports_by_file: dict[str, list[str]] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: Any = None,
) -> dict[str, set[str]]:
    """Restrict `reverse_importers` to files reachable from `definition_files` via `symbol_name`.

    Starting from `definition_files`, traverses an importing file `F` only if `F`
    mentions `symbol_name` as a whole word in its source text (failing open on
    unreadable/oversized files or deadline exhaustion). Prevents a helper in a shared
    module from exploding through hub files into unrelated importers.
    """
    _ = all_files, imports_by_file
    if not symbol_name or not definition_files:
        return {target: set(importers) for target, importers in reverse_importers.items()}

    pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
    reachable: set[str] = {str(path) for path in definition_files}
    queue: deque[str] = deque(reachable)
    mention_cache: dict[str, bool] = {}

    while queue:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            return {target: set(importers) for target, importers in reverse_importers.items()}
        current = queue.popleft()
        for importer in reverse_importers.get(current, ()):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                if deadline_hit is not None:
                    deadline_hit.hit = True
                return {target: set(importers) for target, importers in reverse_importers.items()}
            if importer in reachable:
                continue
            mentions = mention_cache.get(importer)
            if mentions is None:
                mentions = _file_mentions_symbol(importer, pattern)
                mention_cache[importer] = mentions
            if mentions:
                reachable.add(importer)
                queue.append(importer)

    return {
        target: {importer for importer in importers if importer in reachable}
        for target, importers in reverse_importers.items()
        if target in reachable
    }
