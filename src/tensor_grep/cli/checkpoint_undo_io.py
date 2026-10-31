"""Identity checks and best-effort confined checkpoint rollback."""

from __future__ import annotations

import stat
from collections.abc import Callable
from functools import partial
from pathlib import Path

from tensor_grep.cli._index_lock import (
    _is_link_or_junction,
    atomic_write_bytes_confined,
    dir_identity,
)
from tensor_grep.io.confined import validate_confined_parent_prefix


def check_restore_target(
    root: Path, path: Path, parent_identity: tuple[int, int], leaf_identity: tuple[int, int]
) -> None:
    validate_confined_parent_prefix(root, path)
    info = path.lstat()
    if (
        dir_identity(path.parent) != parent_identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or _is_link_or_junction(path)
        or (info.st_dev, info.st_ino) != leaf_identity
    ):
        raise OSError("checkpoint restore target identity changed")


def read_for_rollback(
    root: Path, path: Path, checkpoint_id: str, read: Callable[[Path, str], bytes]
) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
    parent_identity = dir_identity(path.parent)
    info = path.lstat()
    leaf_identity = (info.st_dev, info.st_ino)
    check_restore_target(root, path, parent_identity, leaf_identity)
    data = read(path, checkpoint_id)
    check_restore_target(root, path, parent_identity, leaf_identity)
    after_read = path.lstat()
    if (info.st_size, info.st_mtime_ns) != (after_read.st_size, after_read.st_mtime_ns):
        raise OSError("checkpoint restore target changed during rollback read")
    return data, parent_identity, leaf_identity


def rollback_overwrites(
    root: Path, entries: list[tuple[Path, bytes | None, tuple[int, int], tuple[int, int]]]
) -> None:
    for path, prior_bytes, parent_identity, leaf_identity in reversed(entries):
        try:
            check = partial(check_restore_target, root, path, parent_identity, leaf_identity)
            check()
            if prior_bytes is not None:
                atomic_write_bytes_confined(root, path, prior_bytes, before_publish=check)
            else:
                # shortcut: same-user swaps can race unlink; this is best-effort rollback.
                path.unlink()
        except OSError:
            pass


def _check_removed_parent(path: Path, identity: tuple[int, int]) -> None:
    if dir_identity(path.parent) != identity:
        raise OSError("checkpoint removed parent identity changed")


def rollback_removes(root: Path, entries: list[tuple[Path, bytes, tuple[int, int]]]) -> None:
    for path, prior_bytes, parent_identity in reversed(entries):
        try:
            check = partial(_check_removed_parent, path, parent_identity)
            check()
            atomic_write_bytes_confined(
                root, path, prior_bytes, replace=False, before_publish=check
            )
        except OSError:
            pass
