"""The one definition of the JavaScript / TypeScript suffix sets (layer-neutral home).

Imports NOTHING from ``tensor_grep`` (so ``repo_map``, ``js_ts_scope_gap``, the AST backend and any
later consumer can import it without a cycle or a backward layering edge). Five copies of this set
used to live in five modules; the one in ``repo_map`` lacked ``.mts``/``.cts`` so those files were
never registered and silently dropped from every symbol answer.
``tests/unit/test_ts_suffix_single_source.py`` fails when a new literal copy appears.

It lives in ``core`` because ``backends`` may not import ``cli`` (the import-edges layering
baseline); ``cli/lang_suffixes.py`` re-exports these names unchanged for its existing importers.
"""

from __future__ import annotations

JS_TS_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})

__all__ = ["JS_TS_SUFFIXES", "TS_SUFFIXES"]
