"""Persistent codemap locks; readers never create metadata or refresh a heartbeat.

Both output locations remain pinned and exclusively locked until the context exits.
Callers must check ``unchanged()`` before publication or reporting freshness. An inherited
fork context is unusable; the child must acquire its own context. Build/check wiring is separate.
shortcut: index identity can change after a check; pin the publication target before wiring writes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import partial
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


@contextmanager
def publication_lock(out_dir: Path, index_path: Path, *, create: bool) -> Iterator[Publication]:
    """Fail promptly on unavailable locks; only writers (``create=True``) prepare directories.

    Lock files persist indefinitely: replacing or deleting one would split mutual exclusion.
    Output and custom-index parents are deduplicated and acquired in stable lexical order.
    """
    owner_pid = os.getpid()
    output, index_parent = (
        Path(os.path.normcase(os.path.abspath(path))) for path in (out_dir, index_path.parent)
    )
    index_path = Path(os.path.abspath(index_path))
    index_name = index_path.name
    if os.name == "nt":
        index_name = index_name.split(":", 1)[0].rstrip(" .")
    if os.path.normcase(index_name) == os.path.normcase(LOCK_NAME):
        raise OSError("codemap index conflicts with publication lock")
    if any(len(path.parts) > 256 for path in (output, index_parent)):
        raise OSError("overly deep codemap publication directory")
    for path in {output, index_parent}:
        try:
            if os.path.samefile(index_path, path / LOCK_NAME):
                raise OSError("codemap index conflicts with publication lock")
        except FileNotFoundError:
            pass
    pinned: dict[Path, PinnedDirectory] = {}
    held: list[tuple[PinnedDirectory, int]] = []
    with ExitStack() as directories, ExitStack() as acquired:
        for path in sorted({output, index_parent}):
            try:
                if create:
                    try:
                        locks.atomic_write_bytes_confined(
                            Path(path.anchor), path / LOCK_NAME, b"", replace=False
                        )
                    except FileExistsError:
                        pass
                pin = directories.enter_context(PinnedDirectory(path))
                fd = locks._try_os_file_lock_with_opener(
                    partial(pin.open_regular, LOCK_NAME),
                    partial(pin.file_unchanged, LOCK_NAME),
                )
                if fd is None:
                    raise OSError("lock is contended or changed")
                try:
                    acquired.callback(_release, owner_pid, fd)
                except BaseException:
                    _release(owner_pid, fd)
                    raise
                pinned[path] = pin
                held.append((pin, fd))
            except OSError as exc:
                raise OSError(
                    f"codemap publication lock unavailable at {path / LOCK_NAME}: {exc}"
                ) from exc
        publication = Publication(pinned[output], pinned[index_parent], held, owner_pid, index_path)
        try:
            if _index_conflicts(index_path, held):
                raise OSError("codemap index conflicts with publication lock")
            if not publication.unchanged():
                raise OSError("codemap publication directories or locks changed")
            yield publication
        finally:
            publication._active = False
