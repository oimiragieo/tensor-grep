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
import sys
from collections.abc import Iterator
from pathlib import Path

_RG_SHORT_OPTIONS_WITH_VALUES = frozenset("ABCEMdefgjmrtT")


_MAX_RG_CONFIG_BYTES = 1024 * 1024


def open_regular_nontraversing_fd(path: Path) -> int | None:
    """Open ``path`` without following leaf symlinks or Windows reparse points."""
    fd: int | None = None
    try:
        st_link = path.lstat()
        if not stat.S_ISREG(st_link.st_mode) or (getattr(st_link, "st_file_attributes", 0) & 0x400):
            return None
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            create_file = kernel32.CreateFileW
            create_file.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            create_file.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = create_file(
                os.path.abspath(path),
                0x80000000,
                0x00000007,
                None,
                3,
                0x00200000,
                None,
            )
            if handle in (None, ctypes.c_void_p(-1).value):
                return None
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
                handle = None
            finally:
                if handle is not None:
                    close_handle(handle)
        else:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(str(path), flags)
        st_fd = os.fstat(fd)
        if not stat.S_ISREG(st_fd.st_mode) or (getattr(st_fd, "st_file_attributes", 0) & 0x400):
            os.close(fd)
            fd = None
            return None
        result_fd = fd
        fd = None
        return result_fd
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


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
    fd = open_regular_nontraversing_fd(Path(config_path))
    if fd is None:
        return True
    try:
        with os.fdopen(fd, "rb") as handle:
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


def _is_vendored_child_dir(child: Path, follow_symlinks: bool) -> bool:
    try:
        child_info = child.lstat()
        is_link = stat.S_ISLNK(child_info.st_mode) or bool(
            getattr(child_info, "st_file_attributes", 0) & 0x400
        )
        return (follow_symlinks or not is_link) and child.is_dir()
    except OSError:
        # Conservatively report matching vendored names that fail classification.
        return True


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
                if _is_vendored_child_dir(child, follow_symlinks):
                    yield child.name
        except OSError:
            continue
