"""Compatibility facade for confined I/O and bounded symbol-cache locking."""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tensor_grep.cli._index_lock import _unlock_and_close
from tensor_grep.io.confined import (
    _pin_windows_parents as _pin_windows_parents,
)
from tensor_grep.io.confined import (
    _real_components as _real_components,
)
from tensor_grep.io.confined import (
    _verify_parents as _verify_parents,
)
from tensor_grep.io.confined import (
    prepare_directory as prepare_directory,
)
from tensor_grep.io.confined import (
    publish_confined as publish_confined,
)
from tensor_grep.io.confined import (
    read_confined as read_confined,
)


@contextmanager
def cache_lock(root: Path, path: Path, deadline: float | None) -> Iterator[None]:
    """Bound lock contention to 250 ms or the caller's remaining monotonic budget."""
    lock_path = path.with_suffix(".lock")
    if not os.path.lexists(lock_path):
        try:
            publish_confined(root, lock_path, b"0")
        except FileExistsError:
            pass  # another cache writer published the same lock; validate it below
    parents = _real_components(root, lock_path)
    initial = lock_path.lstat()
    if not stat.S_ISREG(initial.st_mode) or getattr(initial, "st_file_attributes", 0) & 0x400:
        raise OSError("symbol cache refuses linked lock")
    end = (
        min(time.monotonic() + 0.25, deadline) if deadline is not None else time.monotonic() + 0.25
    )
    while True:
        _verify_parents(parents)
        held = _try_cache_lock(lock_path, (initial.st_dev, initial.st_ino))
        if held is not None:
            try:
                _verify_parents(parents)
                yield
            finally:
                _unlock_and_close(held)
            return
        if time.monotonic() >= end:
            raise OSError("symbol cache lock deadline exceeded")
        time.sleep(min(0.01, max(0.0, end - time.monotonic())))


def _try_cache_lock(path: Path, identity: tuple[int, int]) -> int | None:
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    transferred = False
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        if (
            (opened.st_dev, opened.st_ino) != identity
            or (current.st_dev, current.st_ino) != identity
            or getattr(current, "st_file_attributes", 0) & 0x400
        ):
            raise OSError("symbol cache lock identity changed")
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return None
        transferred = True
        return descriptor
    finally:
        if not transferred:
            os.close(descriptor)
