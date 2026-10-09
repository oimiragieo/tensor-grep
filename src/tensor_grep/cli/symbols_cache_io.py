"""Bounded source snapshots and confined publication for the rebuildable symbol cache."""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from tensor_grep.cli._index_lock import (
    WriteAuthorization,
    atomic_write_bytes_anchored,
    dir_identity,
    file_identity,
    write_authorizations,
)
from tensor_grep.cli._index_lock import _unlock_and_close as _unlock_and_close


def _real_components(root: Path, path: Path) -> list[tuple[Path, tuple[int, int]]]:
    relative = path.relative_to(root)
    components = [
        root,
        *(root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts))),
    ]
    identities = []
    for component in components:
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode) or (getattr(info, "st_file_attributes", 0) & 0x400):
            raise OSError(f"symbol cache refuses link/junction parent: {component}")
        identities.append((component, (info.st_dev, info.st_ino)))
    return identities


def _verify_parents(parents: list[tuple[Path, tuple[int, int]]]) -> None:
    for path, identity in parents:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or (info.st_dev, info.st_ino) != identity
        ):
            raise OSError(f"symbol cache parent changed: {path}")


@contextmanager
def _pin_windows_parents(parents: list[tuple[Path, tuple[int, int]]]) -> Iterator[None]:
    """Directory handles deny rename/delete while filename-based Windows I/O runs."""
    if sys.platform != "win32":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    handles = []
    try:
        for path, _ in parents:
            handle = create(str(path), 0, 3, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        _verify_parents(parents)
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def read_confined(root: Path, path: Path, limit: int) -> bytes:
    """Reject special files/links and bind bounded bytes to the opened object identity."""
    parents = _real_components(root, path)
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or getattr(initial, "st_file_attributes", 0) & 0x400:
        raise OSError(f"symbol cache refuses non-regular source: {path}")
    if initial.st_size > limit:
        raise OSError(f"symbol cache file exceeds {limit} bytes: {path}")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise OSError(f"symbol cache source changed before read: {path}")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    _verify_parents(parents)
    observed = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if (
        len(data) > limit
        or observed
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        or not stat.S_ISREG(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & 0x400
    ):
        raise OSError(f"symbol cache source changed during read: {path}")
    return data


def publish_confined(root: Path, path: Path, data: bytes, *, only_if_missing: bool = False) -> None:
    parents = _real_components(root, path)
    identity = file_identity(path) if os.path.lexists(path) else None
    if identity is not None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"symbol cache refuses linked cache: {path}")
        if only_if_missing:
            raise FileExistsError(f"symbol cache metadata already exists: {path}")
    authorization = WriteAuthorization(
        str(path), identity, dir_identity(path.parent), "symbol cache"
    )
    _verify_parents(parents)
    with _pin_windows_parents(parents):
        if os.name == "nt":
            with write_authorizations([authorization]):
                atomic_write_bytes_anchored(path, data, mode=0o600)
        else:
            parent_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            temporary = f".{path.name}.{uuid4().hex}.tmp"
            try:
                _verify_parents(parents)
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                _verify_parents(parents)
                if identity is None:
                    os.link(
                        temporary,
                        path.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                else:
                    if file_identity(path) != identity:
                        raise OSError("symbol cache changed before publication")
                    os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                os.close(parent_fd)
    _verify_parents(parents)


def prepare_directory(root: Path, directory: Path) -> None:
    """Create only known cache components, refusing pre-existing links and junctions."""
    for count in range(1, len(directory.relative_to(root).parts) + 1):
        current = root.joinpath(*directory.relative_to(root).parts[:count])
        _real_components(root, current)
        parents = _real_components(root, current)
        with _pin_windows_parents(parents):
            if os.name == "nt":
                current.mkdir(exist_ok=True)
            else:
                parent_fd = os.open(
                    current.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                try:
                    _verify_parents(parents)
                    try:
                        os.mkdir(current.name, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                finally:
                    os.close(parent_fd)
        _real_components(root, current / "probe")


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
