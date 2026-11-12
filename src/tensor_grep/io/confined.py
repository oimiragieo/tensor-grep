"""Bounded snapshots and confined publication shared by core and CLI consumers."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

_READ_CHUNK_BYTES = 64 * 1024


def _read_limited_stream(stream: BinaryIO, limit: int) -> bytes:
    """Read through EOF or one byte beyond the limit without a limit-sized request."""
    remaining = limit + 1
    chunks: list[bytes] = []
    while remaining:
        chunk = stream.read(min(remaining, _READ_CHUNK_BYTES))
        if chunk is None:
            raise OSError("confined source read returned no data")
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _publish_windows(
    path: Path,
    data: bytes,
    identity: tuple[int, int, int, int] | None,
    before_create: Callable[[], None] | None,
    temp_ready: Callable[[int, Path], None] | None,
    before_publish: Callable[[], None] | None,
    on_published: Callable[[], None] | None,
    replace_open_pinned_target: bool,
) -> tuple[int, int]:
    """Publish under pinned directory handles, refusing new-destination races atomically."""
    from tensor_grep.io.windows_publication import (
        discard_open,
        open_temporary,
        rename_open,
    )

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    if before_create is not None:
        before_create()
    descriptor = open_temporary(temporary)
    published = False
    try:
        opened = os.fstat(descriptor)
        owned_identity = (opened.st_dev, opened.st_ino)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
        if temp_ready is not None:
            temp_ready(descriptor, temporary)
        os.fsync(descriptor)
        if before_publish is not None:
            before_publish()
        if identity is not None and _file_identity(path) != identity:
            raise OSError("confined file changed before publication")
        if identity is None:
            rename_open(descriptor, path, replace=False)
        elif replace_open_pinned_target:
            from tensor_grep.io.windows_publication import replace_open

            replace_open(descriptor, path)
        else:
            rename_open(descriptor, path, replace=True)
        published = True
        if on_published is not None:
            on_published()
        return owned_identity
    finally:
        try:
            if not published:
                discard_open(descriptor)
        finally:
            os.close(descriptor)


def _verify_published(parent_fd: int, name: str, descriptor: int, data: bytes) -> None:
    """POSIX has no portable source-handle rename; never report raced publication as success."""
    opened = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or (opened.st_dev, opened.st_ino) != (
        current.st_dev,
        current.st_ino,
    ):
        raise OSError("confined publication source identity changed; cache is untrusted")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = len(data)
    while remaining:
        block = os.read(descriptor, min(65536, remaining))
        if not block:
            raise OSError("confined publication bytes changed; cache is untrusted")
        digest.update(block)
        remaining -= len(block)
    if os.read(descriptor, 1) or digest.digest() != hashlib.sha256(data).digest():
        raise OSError("confined publication bytes changed; cache is untrusted")


def _cleanup_owned_temp(parent_fd: int, name: str, descriptor: int) -> None:
    """Leave detected unknown inodes; a same-user swap can still race the check and unlink."""
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    opened = os.fstat(descriptor)
    if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise OSError(f"confined temporary was substituted; left unknown object untouched: {name}")
    os.unlink(name, dir_fd=parent_fd)


def _confined_relative_parts(
    root: Path, path: Path, *, allow_root: bool = False
) -> tuple[str, ...]:
    """Validate lexical containment before observing any filesystem metadata."""
    parts = path.relative_to(root).parts
    if os.name == "nt" and path.parts[: len(root.parts)] != root.parts:
        raise OSError("confined path must use the exact trusted root spelling")
    if ".." in parts:
        raise OSError("confined path contains parent traversal")
    if not parts and not allow_root:
        raise OSError("confined path must be below trusted root")
    return parts


def _real_components(root: Path, path: Path) -> list[tuple[Path, tuple[int, int]]]:
    relative_parts = _confined_relative_parts(root, path)
    components = [
        root,
        *(root.joinpath(*relative_parts[:i]) for i in range(1, len(relative_parts))),
    ]
    identities: list[tuple[Path, tuple[int, int]]] = []
    for component in components:
        try:
            info = component.lstat()
        except FileNotFoundError:
            try:
                _verify_parents(identities)
            except FileNotFoundError as parent_exc:
                raise OSError(
                    "symbol cache parent changed while checking missing suffix"
                ) from parent_exc
            raise
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


def validate_confined_parent_prefix(root: Path, path: Path) -> None:
    """Check existing real parent components, allowing a still-missing suffix."""
    try:
        _real_components(root, path)
    except FileNotFoundError:
        return


def _verify_open_directory(descriptor: int, parents: list[tuple[Path, tuple[int, int]]]) -> None:
    opened = os.fstat(descriptor)
    if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != parents[-1][1]:
        raise OSError("confined opened parent identity differs from verified directory")


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
    try:
        initial = path.lstat()
    except FileNotFoundError:
        try:
            _verify_parents(parents)
        except FileNotFoundError as exc:
            raise OSError(f"confined source changed during read: {path}") from exc
        raise
    if not stat.S_ISREG(initial.st_mode) or getattr(initial, "st_file_attributes", 0) & 0x400:
        raise OSError(f"symbol cache refuses non-regular source: {path}")
    if initial.st_size > limit:
        raise OSError(f"symbol cache file exceeds {limit} bytes: {path}")
    try:
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
            data = _read_limited_stream(stream, limit)
            after = os.fstat(stream.fileno())
        current = path.lstat()
        _verify_parents(parents)
    except FileNotFoundError as exc:
        raise OSError(f"confined source changed during read: {path}") from exc
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


def publish_confined(
    root: Path,
    path: Path,
    data: bytes,
    *,
    only_if_missing: bool = False,
    before_create: Callable[[], None] | None = None,
    temp_ready: Callable[[int, Path], None] | None = None,
    before_publish: Callable[[], None] | None = None,
    on_published: Callable[[], None] | None = None,
    parent_fd: int | None = None,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Publish rebuildable advisory bytes; this is not a same-user filesystem CAS boundary.

    Absent targets use atomic no-clobber publication. Existing-target identity is checked,
    but a same-user mutator can swap it between that check and rename; the raced directory
    entry may be replaced. Replacement never writes through an existing inode/hardlink.
    Windows binds the source through its held rename handle. POSIX detects source substitution
    after publication and refuses success, but cannot guarantee arbitrary same-user mutation
    was never briefly visible. Cooperative cache writers must use cache_lock.

    A supplied POSIX parent descriptor is borrowed: only its owned duplicate is closed.
    """
    if parent_fd is not None and os.name != "posix":
        raise OSError("borrowed parent descriptors require POSIX")
    parents = _real_components(root, path)
    if expected_parent_identity is not None and parents[-1][1] != expected_parent_identity:
        raise OSError(f"confined parent identity changed: {path.parent}")
    identity = _file_identity(path) if os.path.lexists(path) else None
    if expected_file_identity is not None and (
        identity is None or identity[:2] != expected_file_identity
    ):
        raise OSError(f"confined file identity changed before rollback: {path}")
    if identity is not None:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OSError(f"symbol cache refuses linked cache: {path}")
        if only_if_missing:
            raise FileExistsError(f"symbol cache metadata already exists: {path}")
    _verify_parents(parents)
    with _pin_windows_parents(parents):
        if os.name == "nt":
            owned_identity = _publish_windows(
                path,
                data,
                identity,
                before_create,
                temp_ready,
                before_publish,
                on_published,
                getattr(expected_file_identity, "_descriptor", None) is not None,
            )
        else:
            temporary = f".{path.name}.{uuid4().hex}.tmp"
            descriptor = None
            parent_fd = (
                os.open(
                    path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
                if parent_fd is None
                else os.dup(parent_fd)
            )
            try:
                _verify_open_directory(parent_fd, parents)
                if expected_parent_identity is not None:
                    opened_parent = os.fstat(parent_fd)
                    if (opened_parent.st_dev, opened_parent.st_ino) != expected_parent_identity:
                        raise OSError(f"confined opened parent identity changed: {path.parent}")
                _verify_parents(parents)
                if before_create is not None:
                    before_create()
                descriptor = os.open(
                    temporary,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                opened = os.fstat(descriptor)
                owned_identity = (opened.st_dev, opened.st_ino)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                if temp_ready is not None:
                    temp_ready(descriptor, path.parent / temporary)
                os.fsync(descriptor)
                _verify_parents(parents)
                if identity is None:
                    if before_publish is not None:
                        before_publish()
                    os.link(
                        temporary,
                        path.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                else:
                    if before_publish is not None:
                        before_publish()
                    if _file_identity(path) != identity:
                        raise OSError("symbol cache changed before publication")
                    os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                if on_published is not None:
                    on_published()
                _verify_published(parent_fd, path.name, descriptor, data)
                os.fsync(parent_fd)
            finally:
                try:
                    if descriptor is not None:
                        _cleanup_owned_temp(parent_fd, temporary, descriptor)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    os.close(parent_fd)
    _verify_parents(parents)
    return owned_identity


def _restore_quarantined_file(parent_fd: int, quarantine: str, name: str) -> None:
    os.link(
        quarantine,
        name,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
        follow_symlinks=False,
    )
    os.unlink(quarantine, dir_fd=parent_fd)


def _unlink_expected_file_at(
    parent_fd: int,
    name: str,
    path: Path,
    expected_identity: tuple[int, int],
    *,
    allow_reparse: bool = False,
) -> None:
    # POSIX has no unlink-by-handle; quarantine still ends in a name-based unlink.
    quarantine = f".{name}.{uuid4().hex}.undo"
    os.rename(name, quarantine, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    descriptor = None
    try:
        current = os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        is_reparse = stat.S_ISLNK(current.st_mode) or bool(
            getattr(current, "st_file_attributes", 0) & 0x400
        )
        if (current.st_dev, current.st_ino) != expected_identity or (
            not stat.S_ISREG(current.st_mode) and not (allow_reparse and is_reparse)
        ):
            raise OSError(f"confined leaf identity changed: {path}")
        if not is_reparse:
            descriptor = os.open(
                quarantine,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != expected_identity
            ):
                raise OSError(f"confined leaf identity changed: {path}")
        os.unlink(quarantine, dir_fd=parent_fd)
    except BaseException as exc:
        try:
            _restore_quarantined_file(parent_fd, quarantine, name)
        except OSError as restore_error:
            raise OSError(
                f"confined rollback left a changed leaf at {path.parent / quarantine}"
            ) from restore_error
        raise exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_confined(
    root: Path,
    path: Path,
    *,
    directory: bool,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
    allow_reparse_leaf: bool = False,
) -> None:
    parents = _real_components(root, path)
    if expected_parent_identity is not None and parents[-1][1] != expected_parent_identity:
        raise OSError(f"confined parent identity changed: {path.parent}")
    if directory and expected_file_identity is not None:
        raise ValueError("directory removal cannot use a regular-file identity")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    kind = "directory" if directory else "regular file"
    with _pin_windows_parents(parents):
        if os.name == "nt":
            if expected_file_identity is not None:
                from tensor_grep.io.windows_publication import unlink_open

                unlink_open(path, expected_file_identity, allow_reparse=allow_reparse_leaf)
            else:
                info = path.lstat()
                if not expected(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise OSError(f"confined removal refuses non-{kind}: {path}")
                path.rmdir() if directory else path.unlink()
        else:
            parent_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                _verify_open_directory(parent_fd, parents)
                _verify_parents(parents)
                info = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                is_reparse = stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0) & 0x400
                )
                if not expected(info.st_mode) and not (
                    not directory and allow_reparse_leaf and is_reparse
                ):
                    raise OSError(f"confined removal refuses non-{kind}: {path}")
                if (
                    expected_file_identity is not None
                    and (
                        info.st_dev,
                        info.st_ino,
                    )
                    != expected_file_identity
                ):
                    raise OSError(f"confined leaf identity changed: {path}")
                if directory:
                    os.rmdir(path.name, dir_fd=parent_fd)
                elif expected_file_identity is not None:
                    _unlink_expected_file_at(
                        parent_fd,
                        path.name,
                        path,
                        expected_file_identity,
                        allow_reparse=allow_reparse_leaf,
                    )
                else:
                    os.unlink(path.name, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
    _verify_parents(parents)


def unlink_confined(
    root: Path,
    path: Path,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
    allow_reparse_leaf: bool = False,
) -> None:
    """Unlink a confined regular file, optionally binding rollback to both identities."""
    _remove_confined(
        root,
        path,
        directory=False,
        expected_parent_identity=expected_parent_identity,
        expected_file_identity=expected_file_identity,
        allow_reparse_leaf=allow_reparse_leaf,
    )


def rmdir_confined(root: Path, path: Path) -> None:
    """Remove an empty real directory beneath root relative to a retained parent."""
    _remove_confined(root, path, directory=True)


def prepare_directory(root: Path, directory: Path) -> None:
    """Create only known cache components, refusing pre-existing links and junctions."""
    relative_parts = _confined_relative_parts(root, directory, allow_root=True)
    for count in range(1, len(relative_parts) + 1):
        current = root.joinpath(*relative_parts[:count])
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
                    _verify_open_directory(parent_fd, parents)
                    _verify_parents(parents)
                    try:
                        os.mkdir(current.name, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                finally:
                    os.close(parent_fd)
        _real_components(root, current / "probe")
