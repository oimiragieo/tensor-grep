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

import os
import stat
from collections.abc import Iterator
from pathlib import Path

_RG_SHORT_OPTIONS_WITH_VALUES = frozenset("ABCEMdefgjmrtT")


def _ripgrep_config_follow_policy() -> bool:
    """Return ripgrep's last follow setting from its environment config, if readable.

    Ripgrep prepends one argument per non-comment config line to the real command line. An
    unreadable or malformed config makes ripgrep refuse before scanning, so it cannot enable a
    traversal that this guard needs to predict.
    """
    config_path = os.environ.get("RIPGREP_CONFIG_PATH")
    if not config_path:
        return False
    follow = False
    try:
        with Path(config_path).open(encoding="utf-8") as handle:
            for raw_line in handle:
                argument = raw_line.strip()
                if not argument or argument.startswith("#"):
                    continue
                if argument in {"-L", "--follow"}:
                    follow = True
                elif argument == "--no-follow":
                    follow = False
                elif argument.startswith("-") and not argument.startswith("--"):
                    for option in argument[1:]:
                        if option == "L":
                            follow = True
                        if option in _RG_SHORT_OPTIONS_WITH_VALUES:
                            break
    except (OSError, UnicodeError):
        return False
    return follow


def effective_rg_follow_policy(
    *, follow: bool = False, no_follow: bool = False, no_config: bool = False
) -> bool:
    """Mirror the follow state emitted by ``RipgrepBackend`` after rg's prepended config.

    The backend appends ``--follow`` and then ``--no-follow`` when both parsed flags are set, so
    ``no_follow`` deliberately wins here too.
    """
    enabled = False if no_config else _ripgrep_config_follow_policy()
    if follow:
        enabled = True
    if no_follow:
        enabled = False
    return enabled


def iter_top_level_vendored_dirs(
    paths: list[str],
    vendored_names: frozenset[str],
    follow_symlinks: bool = False,
) -> Iterator[str]:
    """Yield the on-disk name of every top-level child directory of ``paths`` whose
    lowercased name is in ``vendored_names``.

    O(top-level-entries) per path: never walks past one level (``Path.iterdir()`` only).
    A path that is empty, ``"-"``, or looks like a flag (starts with ``-``) is skipped, matching
    both prior adapters. An unreadable/nonexistent path is skipped via a swallowed ``OSError``
    rather than raising -- preserving both prior adapters' fail-open-on-cleanup-error policy
    exactly (this is a *diagnostic* probe, not a refusal decision).

    Callers choose their own aggregation and pass the effective search follow policy. A vendored
    symlink or Windows directory reparse point is irrelevant to a default no-follow scan but must
    remain guarded when it will be read.
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
                child_info = child.lstat()
                is_link = stat.S_ISLNK(child_info.st_mode) or bool(
                    getattr(child_info, "st_file_attributes", 0) & 0x400
                )
                if (
                    (follow_symlinks or not is_link)
                    and child.is_dir()
                    and child.name.lower() in lower_names
                ):
                    yield child.name
        except OSError:
            continue
