"""Disclose a JS/TS scan that was scoped BELOW its ``tsconfig.json``.

`repo_map` resolves TypeScript path aliases (`@/foo`) through `_parse_js_ts_tsconfig(root)`,
which looks for `tsconfig.json` at the SCAN ROOT only. Point `tg` at a subdirectory of a TS
project -- `tg callers ./src Foo` instead of `tg callers . Foo` -- and the aliases silently
stop resolving, so `import_graph_consumers` under-reports with no stated cause.

Measured 2026-09-12 on a 110-file Next.js corpus: scoped at `<project>/src`,
`import_graph_consumer_count` was **0** for a component that `layout.tsx` really imports;
scoped at `<project>` (where `tsconfig.json` lives) the same query returned **1** and named
the file. Same symbol, same tree -- only the scope differed, and nothing in the payload said
so.

A quiet under-count is the failure shape this repo's honesty floor exists to prevent, so
this emits a `resolution_gaps` entry naming the real cause instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_JS_TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}
_TSCONFIG = "tsconfig.json"
# Bound the upward walk so a scan on a deep path cannot turn into an unbounded stat storm.
_MAX_ANCESTORS = 24


def _common_ancestor(paths: list[Path]) -> Path | None:
    try:
        return Path(os.path.commonpath([str(p.parent) for p in paths]))
    except (ValueError, OSError):
        # Mixed drives on Windows, or an unreadable path: no single scope to reason about.
        return None


def js_ts_scope_gap(bounded_files: list[Path]) -> dict[str, Any] | None:
    """Return a ``resolution_gaps`` entry when JS/TS files were scanned from below a
    ``tsconfig.json``, else ``None``.

    Returns ``None`` when the scan root already holds the tsconfig (correctly scoped) and
    when no ancestor holds one (a project that genuinely has no tsconfig has no alias map to
    miss) -- so a correctly-scoped scan keeps byte-identical output.
    """
    js_ts_files = [p for p in bounded_files if p.suffix.lower() in _JS_TS_SUFFIXES]
    if not js_ts_files:
        return None

    scope = _common_ancestor(js_ts_files)
    if scope is None:
        return None

    try:
        if (scope / _TSCONFIG).is_file():
            return None  # correctly scoped; aliases resolve
    except OSError:
        return None

    ancestor = scope
    for _ in range(_MAX_ANCESTORS):
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent
        try:
            if (ancestor / _TSCONFIG).is_file():
                break
        except OSError:
            return None
    else:
        return None

    try:
        if not (ancestor / _TSCONFIG).is_file():
            return None
    except OSError:
        return None

    return {
        "language": "javascript-typescript",
        "reason": (
            "scan was scoped below the project's tsconfig.json, so TypeScript path aliases "
            "(e.g. '@/x') could not be resolved and import_graph_consumers may under-report"
        ),
        "files_affected": len(js_ts_files),
        "remediation": (
            f"tsconfig.json was found at '{ancestor}' but the scan root was '{scope}'. "
            "Re-run with the project root as PATH so alias imports resolve; a zero or low "
            "import_graph_consumer_count from this scan is UNRESOLVED, not proven absent."
        ),
    }
