"""Bounded snapshots and confined publication shared by core and CLI consumers."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _publish_windows(path: Path, data: bytes, identity: tuple[int, int, int, int] | None) -> None:
    """Publish under pinned directory handles, refusing new-destination races atomically."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if identity is None:
            os.link(temporary, path, follow_symlinks=False)
        else:
            if _file_identity(path) != identity:
                raise OSError("confined file changed before publication")
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    identity = _file_identity(path) if os.path.lexists(path) else None
    if identity is not None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"symbol cache refuses linked cache: {path}")
        if only_if_missing:
            raise FileExistsError(f"symbol cache metadata already exists: {path}")
    _verify_parents(parents)
    with _pin_windows_parents(parents):
        if os.name == "nt":
            _publish_windows(path, data, identity)
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
                    if _file_identity(path) != identity:
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
