"""Shared top-level vendored-root probe (AGT-06, Task 08).

`cli/bootstrap.py`'s `_search_paths_include_vendored_root` (a short-circuit boolean) and
`cli/main.py`'s `_root_top_level_vendored_dir_names` (sorted, deduplicated diagnostic names)
independently re-implemented the identical "does any of these paths have a vendored dir as a
direct child" scan: same skip rules (empty/``-``/flag-looking path), same one-level
``Path.iterdir()`` walk (never recurses), same ``OSError``-swallows-and-continues policy, same
vendored-name set from ``io/scan_limits.py``. Only their *aggregation* differs -- one wants the
first hit, the other wants every distinct name, sorted case-insensitively.

This module factors out the shared iteration so the two adapters can never drift out of sync,
while leaving each adapter's own return shape, short-circuit/collection behavior, and calling
convention (argv parsing, ``SearchConfig`` interpretation) untouched in its own file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path


def iter_top_level_vendored_dirs(paths: list[str], vendored_names: frozenset[str]) -> Iterator[str]:
    """Yield the on-disk name of every top-level child directory of ``paths`` whose
    lowercased name is in ``vendored_names``.

    O(top-level-entries) per path: never walks past one level (``Path.iterdir()`` only).
    A path that is empty, ``"-"``, or looks like a flag (starts with ``-``) is skipped, matching
    both prior adapters. An unreadable/nonexistent path is skipped via a swallowed ``OSError``
    rather than raising -- preserving both prior adapters' fail-open-on-cleanup-error policy
    exactly (this is a *diagnostic* probe, not a refusal decision).

    Callers choose their own aggregation: short-circuit on the first item (``bootstrap.py``) or
    collect/dedupe/sort every item (``main.py``).
    """
    lower_names = {name.lower() for name in vendored_names}
    for raw_path in paths:
        if not raw_path or raw_path == "-" or raw_path.startswith("-"):
            continue
        path = Path(raw_path)
        try:
            if not path.is_dir():
                continue
            for child in path.iterdir():
                if child.is_dir() and child.name.lower() in lower_names:
                    yield child.name
        except OSError:
            continue
