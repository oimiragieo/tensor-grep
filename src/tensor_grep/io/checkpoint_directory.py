"""Windows directory handles for checkpoint creation ownership."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
from ctypes import wintypes
from pathlib import Path
from typing import Any

_FILE_LIST_DIRECTORY = 0x0001
_FILE_READ_ATTRIBUTES = 0x0080
_SYNCHRONIZE = 0x00100000
_DIRECTORY_ACCESS = _FILE_LIST_DIRECTORY | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
_FILE_SHARE_READ = 0x0001
_FILE_SHARE_WRITE = 0x0002
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_DIRECTORY_FLAGS_DISALLOW_PATH_REDIRECTS = 0x00000001
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _kernel() -> Any:
    if os.name != "nt":
        raise OSError(errno.ENOTSUP, "Windows checkpoint directory handles are unavailable")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _create_directory2(kernel: Any | None = None) -> Any | None:
    return getattr(_kernel() if kernel is None else kernel, "CreateDirectory2W", None)


def require_supported() -> None:
    if os.name != "nt":
        raise OSError(
            errno.ENOTSUP,
            "Python checkpoint creation is unsupported on POSIX without atomic directory ownership",
        )
    if _create_directory2() is None:
        raise OSError(
            errno.ENOTSUP,
            "Python checkpoint creation requires Windows CreateDirectory2W path-redirection protection",
        )


class CheckpointDirectoryOwner:
    """Pin every checkpoint directory while path-based snapshot and state writes run."""

    def __init__(self, root: Path) -> None:
        require_supported()
        self._kernel = _kernel()
        create2 = _create_directory2(self._kernel)
        if create2 is None:
            raise OSError(errno.ENOTSUP, "Windows CreateDirectory2W is unavailable")
        self._create2 = create2
        self._create2.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self._create2.restype = wintypes.HANDLE
        self._create = self._kernel.CreateFileW
        self._create.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._create.restype = wintypes.HANDLE
        self._close_handle = self._kernel.CloseHandle
        self._close_handle.argtypes = [wintypes.HANDLE]
        self._close_handle.restype = wintypes.BOOL
        self._handles: dict[str, tuple[int, tuple[int, int]]] = {}
        self._order: list[str] = []
        self._created_paths: list[Path] = []
        try:
            for directory in self._ancestor_chain(root):
                self.open_existing(directory)
        except BaseException as original_error:
            try:
                self.close()
            except BaseException as close_error:
                add_note = getattr(original_error, "add_note", None)
                if add_note is not None:
                    add_note(f"also failed to close a checkpoint directory handle: {close_error}")
            raise

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path.absolute()))

    @staticmethod
    def _ancestor_chain(root: Path) -> list[Path]:
        absolute = root.absolute()
        drive = absolute.drive
        is_drive_letter = len(drive) == 2 and drive[1] == ":" and drive[0].isalpha()
        unc_parts = drive.strip("\\").split("\\")
        is_unc_share = (
            drive.startswith("\\\\")
            and not drive.startswith("\\\\?\\")
            and len(unc_parts) == 2
            and all(unc_parts)
        )
        if not absolute.is_absolute() or not (is_drive_letter or is_unc_share):
            raise OSError(
                errno.ENOTSUP,
                "Python checkpoint creation supports only drive-letter and UNC paths",
            )
        anchor = Path(absolute.anchor)
        chain = [anchor]
        current = anchor
        for part in absolute.relative_to(anchor).parts:
            current = current / part
            chain.append(current)
        return chain

    def identity(self, path: Path) -> tuple[int, int]:
        try:
            return self._handles[self._key(path)][1]
        except KeyError as exc:
            raise OSError(f"checkpoint directory is not pinned: {path}") from exc

    def assert_pinned(self, path: Path) -> None:
        """Require a discovered checkpoint scope to belong to the already-pinned input chain."""
        if self._key(path) not in self._handles:
            raise OSError(f"checkpoint scope root is outside the pinned input path chain: {path}")

    @property
    def created_paths(self) -> tuple[Path, ...]:
        """Directories atomically created and retained by this owner, in creation order."""
        return tuple(self._created_paths)

    def _remember(self, path: Path, handle: int) -> tuple[int, int]:
        descriptor = self._descriptor(handle)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise OSError(f"checkpoint parent is not a real directory: {path}")
            identity = (info.st_dev, info.st_ino)
            key = self._key(path)
            self._handles[key] = (descriptor, identity)
            self._order.append(key)
            return identity
        except BaseException:
            os.close(descriptor)
            raise

    def _descriptor(self, handle: int) -> int:
        import msvcrt

        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except BaseException as original_error:
            if not self._close_handle(handle):
                raise ctypes.WinError(ctypes.get_last_error()) from original_error
            raise

    def open_existing(self, path: Path) -> tuple[int, int]:
        key = self._key(path)
        if key in self._handles:
            return self._handles[key][1]
        before = path.lstat()
        if getattr(before, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError(f"checkpoint parent refuses a reparse point: {path}")
        if not stat.S_ISDIR(before.st_mode):
            raise OSError(f"checkpoint parent is not a real directory: {path}")
        handle = self._create(
            str(path.absolute()),
            _DIRECTORY_ACCESS,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = self._remember(path, handle)
        if identity != (before.st_dev, before.st_ino):
            raise OSError(f"checkpoint parent identity changed while opening: {path}")
        self.verify_created_directory(path, identity)
        return identity

    def create_directory(self, path: Path) -> tuple[int, int]:
        key = self._key(path)
        if key in self._handles:
            raise FileExistsError(
                errno.EEXIST, "checkpoint directory was already created", str(path)
            )
        handle = self._create2(
            str(path.absolute()),
            _DIRECTORY_ACCESS,
            _FILE_SHARE_READ,
            _DIRECTORY_FLAGS_DISALLOW_PATH_REDIRECTS,
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        self._created_paths.append(path)
        identity = self._remember(path, handle)
        first_fd = self._handles[key][0]
        second_handle = self._create(
            str(path.absolute()),
            _DIRECTORY_ACCESS,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if second_handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        second_fd = self._descriptor(second_handle)
        try:
            info = os.fstat(second_fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
                or (info.st_dev, info.st_ino) != identity
            ):
                raise OSError(
                    f"checkpoint directory identity changed during handle transfer: {path}"
                )
            os.close(first_fd)
            self._handles[key] = (second_fd, identity)
            second_fd = -1
        finally:
            if second_fd >= 0:
                os.close(second_fd)
        self.verify_created_directory(path, identity)
        return identity

    def ensure_storage(self, root: Path, storage: Path) -> tuple[tuple[int, int], ...]:
        current = root
        identities = [self.identity(root)]
        for part in storage.relative_to(root).parts:
            current = current / part
            try:
                identity = self.open_existing(current)
            except FileNotFoundError:
                try:
                    identity = self.create_directory(current)
                except FileExistsError:
                    identity = self.open_existing(current)
            identities.append(identity)
        return tuple(identities)

    def pin_existing_storage(self, root: Path, storage: Path) -> None:
        current = root
        for part in storage.relative_to(root).parts:
            current = current / part
            try:
                self.open_existing(current)
            except FileNotFoundError:
                return

    def pin_source_parent(self, root: Path, relative_path: str) -> None:
        candidate = Path(relative_path)
        if (
            candidate.is_absolute()
            or candidate.drive
            or any(part == ".." for part in candidate.parts)
        ):
            raise ValueError(f"Refusing checkpoint entry outside root: {relative_path!r}")
        parent = root
        for part in candidate.parts[:-1]:
            parent = parent / part
            self.open_existing(parent)

    def create_snapshot_parents(self, snapshot: Path, relative_path: str) -> None:
        parent = snapshot
        for part in Path(relative_path).parts[:-1]:
            if part in ("", ".", "..") or ":" in part:
                raise OSError(f"invalid checkpoint snapshot path: {relative_path!r}")
            parent = parent / part
            if self._key(parent) not in self._handles:
                self.create_directory(parent)

    def verify_created_directory(self, path: Path, expected: tuple[int, int]) -> None:
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            or (info.st_dev, info.st_ino) != expected
        ):
            raise OSError(f"checkpoint directory identity changed: {path}")

    def close(self) -> None:
        first_error: OSError | None = None
        original_order = self._order
        for key in reversed(self._order):
            descriptor, _identity = self._handles[key]
            try:
                os.close(descriptor)
            except OSError as exc:
                first_error = first_error or exc
            else:
                self._handles.pop(key)
        self._order = [key for key in original_order if key in self._handles]
        if first_error is not None:
            raise first_error
