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


_MAX_RG_CONFIG_BYTES = 1024 * 1024


def _ripgrep_config_follow_policy() -> bool:
    """Return ripgrep's last follow setting from its environment config.

    If ``RIPGREP_CONFIG_PATH`` is set but unreadable, non-regular, a symlink/reparse point,
    oversized (>1 MiB), or undecodable, conservatively return ``True`` so guarded roots are
    protected against an indeterminate config.
    """
    config_path = os.environ.get("RIPGREP_CONFIG_PATH")
    if not config_path:
        return False
    follow = False
    fd: int | None = None
    try:
        cfg = Path(config_path)
        st_link = cfg.lstat()
        if not stat.S_ISREG(st_link.st_mode) or (getattr(st_link, "st_file_attributes", 0) & 0x400):
            return True
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(str(cfg), flags)
        st_fd = os.fstat(fd)
        if not stat.S_ISREG(st_fd.st_mode):
            return True
        with os.fdopen(fd, "rb") as handle:
            fd = None
            raw = handle.read(_MAX_RG_CONFIG_BYTES + 1)
        if len(raw) > _MAX_RG_CONFIG_BYTES:
            return True
        for raw_line in raw.decode("utf-8").splitlines():
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
        return True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    return follow


def effective_rg_follow_policy(
    *, follow: bool = False, no_follow: bool = False, no_config: bool = False
) -> bool:
    """Mirror the follow state emitted by ``RipgrepBackend`` after rg's prepended config.

    The backend appends ``--follow`` and then ``--no-follow`` when both parsed flags are set, so
    ``no_follow`` deliberately wins here too.
    """
    if no_follow:
        return False
    if follow:
        return True
    return False if no_config else _ripgrep_config_follow_policy()


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
                if child.name.lower() not in lower_names:
                    continue
                try:
                    child_info = child.lstat()
                    is_link = stat.S_ISLNK(child_info.st_mode) or bool(
                        getattr(child_info, "st_file_attributes", 0) & 0x400
                    )
                    if (follow_symlinks or not is_link) and child.is_dir():
                        yield child.name
                except OSError:
                    # Conservatively report matching vendored names that fail classification.
                    yield child.name
        except OSError:
            continue
