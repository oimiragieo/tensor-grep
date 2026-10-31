"""Pin codemap output directories during bounded reads and confined writes."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from tensor_grep.cli.diff_impact_git import BlobOverCap, read_regular_file_confined


class PinnedDirectory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._held = ExitStack()
        self._fds: list[int] = []
        self._names: list[str] = []

    def __enter__(self) -> PinnedDirectory:
        try:
            if not self.path.is_absolute() or len(self.path.parts) > 256:
                raise OSError("invalid or overly deep output directory")
            if sys.platform == "win32":
                self._pin_windows()
            else:
                self._pin_posix()
        except BaseException:
            self._held.close()
            raise
        return self

    def __exit__(self, *args: object) -> None:
        self._held.close()

    def _pin_windows(self) -> None:
        from tensor_grep.cli import managed_runtime_reader as reader
        from tensor_grep.cli import session_daemon_winsec as winsec

        current = Path(self.path.anchor)
        if not self.path.is_absolute() or not current.anchor:
            raise OSError("invalid output directory")
        handle = winsec.open_no_follow(str(current), directory=True, share=3)
        if handle is None:
            raise OSError("cannot pin output root")
        self._held.callback(winsec.close_handle, handle)
        parent_final = winsec.final_path(handle)
        if reader._final_key(parent_final) != reader._final_key(str(current)):
            raise OSError("output root changed")
        for name in self.path.parts[1:]:
            current /= name
            handle = winsec.open_no_follow(str(current), directory=True, share=3)
            if handle is None:
                raise OSError("cannot pin output directory")
            self._held.callback(winsec.close_handle, handle)
            final = winsec.final_path(handle)
            if not reader._is_direct_child(final, parent_final, name):
                raise OSError("output directory changed")
            parent_final = final

    def _pin_posix(self) -> None:
        directory = getattr(os, "O_DIRECTORY", None)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if directory is None or nofollow is None:
            raise OSError("safe directory open unavailable")
        flags = os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.path.anchor, flags)
        self._fds.append(fd)
        self._held.callback(os.close, fd)
        for name in self.path.parts[1:]:
            fd = os.open(name, flags, dir_fd=fd)
            self._held.callback(os.close, fd)
            self._fds.append(fd)
            self._names.append(name)

    def unchanged(self) -> bool:
        if sys.platform == "win32":
            return True  # held handles deny rename and deletion of every ancestor
        try:
            for parent, fd, name in zip(self._fds[:-1], self._fds[1:], self._names, strict=True):
                entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                opened = os.fstat(fd)
                if not stat.S_ISDIR(entry.st_mode) or (entry.st_dev, entry.st_ino) != (
                    opened.st_dev,
                    opened.st_ino,
                ):
                    return False
        except OSError:
            return False
        return True

    def read(self, name: str, cap: int) -> bytes:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise OSError("invalid output filename")
        if sys.platform == "win32":
            return read_regular_file_confined(self.path, Path(name), cap)
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise OSError("safe file open unavailable")
        fd = os.open(
            name,
            os.O_RDONLY | nofollow | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
            dir_fd=self._fds[-1],
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("output is not a regular file")
            chunks: list[bytes] = []
            remaining = cap + 1
            while remaining:
                block = os.read(fd, min(65536, remaining))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            data = b"".join(chunks)
            if len(data) > cap:
                raise BlobOverCap("output exceeds verification cap")
            return data
        finally:
            os.close(fd)

    def write(self, name: str, data: bytes, *, validate: Callable[[], None]) -> None:
        """Publish through this pin; the caller's validator must raise if its context is invalid."""
        from tensor_grep.cli._index_lock import atomic_write_bytes_confined

        if Path(name).name != name or name in {"", ".", ".."}:
            raise OSError("invalid output filename")
        validate()
        atomic_write_bytes_confined(
            self.path,
            self.path / name,
            data,
            parent_fd=None if sys.platform == "win32" else self._fds[-1],
            before_publish=validate,
        )
        validate()

    def open_regular(self, name: str) -> int:
        """Return an owned read-only descriptor beneath this pin, refusing non-regular files."""
        if Path(name).name != name or name in {"", ".", ".."}:
            raise OSError("invalid output filename")
        if sys.platform == "win32":
            import msvcrt

            from tensor_grep.cli import session_daemon_winsec as winsec

            handle = winsec.open_no_follow(str(self.path / name))
            if handle is None:
                raise OSError("cannot open existing regular output file")
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                winsec.close_handle(handle)
                raise
        else:
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self._fds[-1],
            )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("output is not a regular file")
        except BaseException:
            os.close(fd)
            raise
        return fd

    def file_unchanged(self, name: str, fd: int) -> bool:
        """Check that a pinned regular-file descriptor still names the same directory entry."""
        try:
            current = (
                os.stat(self.path / name, follow_symlinks=False)
                if sys.platform == "win32"
                else os.stat(name, dir_fd=self._fds[-1], follow_symlinks=False)
            )
            opened = os.fstat(fd)
            return stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (
                opened.st_dev,
                opened.st_ino,
            )
        except OSError:
            return False
