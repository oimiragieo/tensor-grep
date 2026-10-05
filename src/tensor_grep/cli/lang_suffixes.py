"""The one definition of the JavaScript / TypeScript suffix sets.

Imports NOTHING from ``tensor_grep`` (so ``repo_map``, ``js_ts_scope_gap`` and any later consumer
can all import it without a cycle). Five copies of this set used to live in five modules; the one
in ``repo_map`` lacked ``.mts``/``.cts`` so those files were never registered and silently
dropped from every symbol answer. ``tests/unit/test_ts_suffix_single_source.py`` fails when a new
literal copy appears.
"""

from __future__ import annotations

JS_TS_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})

__all__ = ["JS_TS_SUFFIXES", "TS_SUFFIXES"]
