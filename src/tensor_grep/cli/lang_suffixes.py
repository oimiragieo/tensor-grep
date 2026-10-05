"""Re-export of the JavaScript / TypeScript suffix sets for the ``cli`` importers.

The definition moved to ``tensor_grep.core.lang_suffixes`` (a layer-neutral module that imports
nothing from ``tensor_grep``) so ``backends`` can derive from it without a backward ``cli`` edge.
This module imports only that one module, so it keeps the original no-cycle property: ``repo_map``,
``js_ts_scope_gap`` and any later consumer can import it in any order.
``tests/unit/test_ts_suffix_single_source.py`` fails when a new literal copy appears.
"""

from __future__ import annotations

from tensor_grep.core.lang_suffixes import JS_TS_SUFFIXES, TS_SUFFIXES

__all__ = ["JS_TS_SUFFIXES", "TS_SUFFIXES"]
