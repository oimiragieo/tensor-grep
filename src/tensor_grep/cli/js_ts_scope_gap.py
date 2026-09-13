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


def _unresolved_gap(js_ts_files: list[Path], scope: Path, exc: OSError) -> dict[str, Any]:
    """Disclose a tsconfig probe that could not be READ.

    Returning ``None`` here (the v1.119.7 behaviour) reported "correctly scoped, no gap" for
    a tree this module never managed to inspect -- a blocked instrument rendered as a clean
    negative, which is the precise failure this module was written to prevent for the
    under-count case. An unreadable ancestor means UNRESOLVED, so say so.
    """
    return {
        "language": "javascript-typescript",
        "reason": (
            "could not determine whether this JS/TS scan sits below its tsconfig.json "
            f"({type(exc).__name__}), so TypeScript path-alias resolution is UNVERIFIED"
        ),
        "files_affected": len(js_ts_files),
        "remediation": (
            f"a tsconfig.json probe at or above '{scope}' failed to read ({exc}). "
            "Re-run with the project root as PATH once the path is readable; a zero or low "
            "import_graph_consumer_count from this scan is UNRESOLVED, not proven absent."
        ),
    }


def js_ts_scope_gap(
    bounded_files: list[Path], scan_root: Path | None = None
) -> dict[str, Any] | None:
    """Return a ``resolution_gaps`` entry when JS/TS files were scanned from below a
    ``tsconfig.json``, else ``None``.

    Returns ``None`` when the scan root already holds the tsconfig (correctly scoped) and
    when no ancestor holds one (a project that genuinely has no tsconfig has no alias map to
    miss) -- so a correctly-scoped scan keeps byte-identical output.
    """
    js_ts_files = [p for p in bounded_files if p.suffix.lower() in _JS_TS_SUFFIXES]
    if not js_ts_files:
        return None

    # The SCAN ROOT is what repo_map roots `_parse_js_ts_tsconfig` at, so it is the only thing
    # that decides whether aliases resolve. Inferring it from the common parent of the matched
    # files is WRONG whenever the project keeps its sources in a subdirectory: scanning
    # `<project>` (tsconfig at the root) with every .ts under `<project>/src` yields a common
    # parent of `<project>/src`, which has no tsconfig, and this function then reported a
    # resolution gap for a correctly-scoped scan. Measured on
    # `benchmarks/bakeoff_fixtures/js_ts/tsconfig_path_alias`, which has exactly that shape.
    # `scan_root` stays optional so an unthreaded caller degrades to the old inference rather
    # than raising, but every in-tree caller now passes it.
    scope = scan_root if scan_root is not None else _common_ancestor(js_ts_files)
    if scope is None:
        return None

    try:
        if (scope / _TSCONFIG).is_file():
            return None  # correctly scoped; aliases resolve
    except OSError as exc:
        # A probe that cannot READ is UNRESOLVED, never "no gap". Collapsing an OSError to
        # None reported a clean, complete answer for a tree this function never managed to
        # inspect -- the exact silent-under-report shape this module exists to disclose.
        return _unresolved_gap(js_ts_files, scope, exc)

    ancestor = scope
    for _ in range(_MAX_ANCESTORS):
        parent = ancestor.parent
        if parent == ancestor:
            break
        ancestor = parent
        try:
            if (ancestor / _TSCONFIG).is_file():
                break
        except OSError as exc:
            return _unresolved_gap(js_ts_files, scope, exc)
    else:
        return None

    try:
        if not (ancestor / _TSCONFIG).is_file():
            return None
    except OSError as exc:
        return _unresolved_gap(js_ts_files, scope, exc)

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
