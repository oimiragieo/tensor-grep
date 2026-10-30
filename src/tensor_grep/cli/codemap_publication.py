"""Persistent codemap locks; readers never create metadata or refresh a heartbeat.

Both output locations remain pinned and exclusively locked until the context exits.
Callers must check ``unchanged()`` before publication or reporting freshness. An inherited
fork context is unusable; the child must acquire its own context. Build/check wiring is separate.
shortcut: index identity can change after a check; pin the publication target before wiring writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.cli import _index_lock as locks
from tensor_grep.cli.codemap_pinned import PinnedDirectory

LOCK_NAME = ".codemap.lock"


def _index_conflicts(index_path: Path, held: list[tuple[PinnedDirectory, int]]) -> bool:
    try:
        index_stat = index_path.stat()
    except FileNotFoundError:
        return False
    return any(os.path.samestat(index_stat, os.fstat(fd)) for _, fd in held)


@dataclass
class Publication:
    output: PinnedDirectory
    index_parent: PinnedDirectory
    _held: list[tuple[PinnedDirectory, int]]
    _owner_pid: int
    _index_path: Path
    _active: bool = True

    def unchanged(self) -> bool:
        try:
            return (
                self._active
                and locks._outer_release_allowed(self._owner_pid)
                and all(
                    pin.unchanged() and pin.file_unchanged(LOCK_NAME, fd) for pin, fd in self._held
                )
                and not _index_conflicts(self._index_path, self._held)
            )
        except OSError:
            return False


def _release(owner_pid: int, fd: int) -> None:
    # The fork hook already closed inherited lock descriptors; their numbers may be reused.
    if locks._outer_release_allowed(owner_pid):
        locks.release_os_file_lock(fd)
