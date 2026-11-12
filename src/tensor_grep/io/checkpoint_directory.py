"""Pinned directory ownership for checkpoint creation."""

from __future__ import annotations

import ctypes
import errno
import json
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
_CREATE_NEW = 1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_DIRECTORY_FLAGS_DISALLOW_PATH_REDIRECTS = 0x00000001
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _kernel() -> Any:
    if os.name != "nt":
        raise OSError(errno.ENOTSUP, "Windows checkpoint directory handles are unavailable")
    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def _create_directory2(kernel: Any | None = None) -> Any | None:
    return getattr(_kernel() if kernel is None else kernel, "CreateDirectory2W", None)


def require_supported() -> None:
    if os.name == "posix":
        required = (
            (os.open, os.supports_dir_fd),
            (os.mkdir, os.supports_dir_fd),
            (os.stat, os.supports_dir_fd),
            (os.link, os.supports_dir_fd),
            # os.replace has dir_fd parameters but CPython reports renameat support under os.rename.
            (os.rename, os.supports_dir_fd),
            (os.unlink, os.supports_dir_fd),
            (os.readlink, os.supports_dir_fd),
            (os.symlink, os.supports_dir_fd),
            (os.utime, os.supports_dir_fd),
            (os.utime, os.supports_follow_symlinks),
        )
        if (
            not all(function in supported for function, supported in required)
            or not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW"))
            or not hasattr(os, "fchmod")
        ):
            raise OSError(errno.ENOTSUP, "Python checkpoint creation requires POSIX *at operations")
        return
    if os.name != "nt":
        raise OSError(errno.ENOTSUP, "Python checkpoint creation is unsupported on this platform")
    if _create_directory2() is None:
        raise OSError(
            errno.ENOTSUP,
            "Python checkpoint creation requires Windows CreateDirectory2W path-redirection protection",
        )


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        unsupported = {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
        if exc.errno not in unsupported:
            raise


class _SnapshotCopyLimitExceededError(OSError):
    """A source grew beyond the configured snapshot copy byte budget."""

    def __init__(self, message: str, relative_path: str) -> None:
        super().__init__(errno.EFBIG, message, relative_path)


class CheckpointDirectoryOwner:
    """Pin checkpoint paths with POSIX directory descriptors or Windows handles."""

    def __init__(self, root: Path) -> None:
        require_supported()
        self._handles: dict[str, tuple[int, tuple[int, int]]] = {}
        self._order: list[str] = []
        self._created_paths: list[Path] = []
        self._snapshot_max_file_bytes: int | None = None
        self._snapshot_remaining_total_bytes: int | None = None
        if os.name == "posix":
            try:
                self._open_posix_chain(root)
            except BaseException as original_error:
                try:
                    self.close()
                except OSError as close_error:
                    if hasattr(original_error, "add_note"):
                        original_error.add_note(
                            f"also failed to close a checkpoint directory handle: {close_error}"
                        )
                raise
            return
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
        try:
            for directory in self._ancestor_chain(root):
                self.open_existing(directory)
        except BaseException as original_error:
            try:
                self.close()
            except OSError as close_error:
                add_note = getattr(original_error, "add_note", None)
                if add_note is not None:
                    add_note(f"also failed to close a checkpoint directory handle: {close_error}")
            raise

    @staticmethod
    def _key(path: Path) -> str:
        value = str(path.absolute())
        if os.name == "nt":
            folded = value.casefold()
            if folded.startswith("\\\\?\\unc\\"):
                value = "\\\\" + value[8:]
            elif folded.startswith("\\\\?\\"):
                value = value[4:]
        return os.path.normcase(os.path.normpath(value))

    @staticmethod
    def _posix_flags() -> int:
        return (
            os.O_RDONLY
            | int(getattr(os, "O_DIRECTORY", 0))
            | int(getattr(os, "O_NOFOLLOW", 0))
            | int(getattr(os, "O_CLOEXEC", 0))
        )

    def _open_posix_chain(self, root: Path) -> None:
        absolute = Path(os.path.abspath(root))
        if not absolute.is_absolute() or not absolute.anchor:
            raise OSError(errno.EINVAL, "checkpoint root must be absolute")
        current = Path(absolute.anchor)
        descriptor = os.open(current, self._posix_flags())
        self._remember_posix(current, descriptor)
        for part in absolute.relative_to(current).parts:
            parent_fd = self._handles[self._key(current)][0]
            descriptor = os.open(part, self._posix_flags(), dir_fd=parent_fd)
            current = current / part
            self._remember_posix(current, descriptor)

    def _remember_posix(self, path: Path, descriptor: int) -> tuple[int, int]:
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise OSError(f"checkpoint parent is not a real directory: {path}")
            identity = (info.st_dev, info.st_ino)
            key = self._key(path)
            self._handles[key] = (descriptor, identity)
            self._order.append(key)
            return identity
        except BaseException:
            os.close(descriptor)
            raise

    def _parent_descriptor(self, path: Path) -> int:
        try:
            return self._handles[self._key(path.parent)][0]
        except KeyError as exc:
            raise OSError(f"checkpoint parent is not pinned: {path.parent}") from exc

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
        key = self._key(path)
        if key in self._handles:
            return
        if os.name == "nt":
            try:
                chain = self._ancestor_chain(path)
                aliases: list[tuple[str, tuple[int, tuple[int, int]]]] = []
                for directory, pinned_key in zip(chain, self._order, strict=False):
                    info = directory.lstat()
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
                        or (info.st_dev, info.st_ino) != self._handles[pinned_key][1]
                    ):
                        break
                    aliases.append((self._key(directory), self._handles[pinned_key]))
                else:
                    self._handles.update(aliases)
                    if key in self._handles:
                        return
            except OSError:
                pass
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

    def _descriptor(self, handle: int, flags: int | None = None) -> int:
        import msvcrt

        try:
            descriptor_flags = os.O_RDONLY if flags is None else flags
            descriptor: int = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
                handle, descriptor_flags | getattr(os, "O_BINARY", 0)
            )
            return descriptor
        except BaseException as original_error:
            if not self._close_handle(handle):
                raise ctypes.WinError(  # type: ignore[attr-defined]
                    ctypes.get_last_error()  # type: ignore[attr-defined]
                ) from original_error
            raise

    def open_existing(self, path: Path) -> tuple[int, int]:
        key = self._key(path)
        if key in self._handles:
            return self._handles[key][1]
        if os.name == "posix":
            parent_fd = self._parent_descriptor(path)
            before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise OSError(f"checkpoint parent is not a real directory: {path}")
            descriptor = os.open(path.name, self._posix_flags(), dir_fd=parent_fd)
            identity = self._remember_posix(path, descriptor)
            if identity != (before.st_dev, before.st_ino):
                raise OSError(f"checkpoint parent identity changed while opening: {path}")
            return identity
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
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
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
        if os.name == "posix":
            parent_fd = self._parent_descriptor(path)
            os.mkdir(path.name, dir_fd=parent_fd)
            self._created_paths.append(path)
            _fsync_directory(parent_fd)
            descriptor = os.open(path.name, self._posix_flags(), dir_fd=parent_fd)
            identity = self._remember_posix(path, descriptor)
            self.verify_created_directory(path, identity)
            return identity
        handle = self._create2(
            str(path.absolute()),
            _DIRECTORY_ACCESS,
            _FILE_SHARE_READ,
            _DIRECTORY_FLAGS_DISALLOW_PATH_REDIRECTS,
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
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
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
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

    def set_snapshot_byte_limits(self, max_file_bytes: int, max_total_bytes: int) -> None:
        if max_file_bytes <= 0 or max_total_bytes <= 0:
            raise ValueError("checkpoint snapshot byte limits must be positive")
        self._snapshot_max_file_bytes = max_file_bytes
        self._snapshot_remaining_total_bytes = max_total_bytes

    def _copy_regular_file_contents(
        self, source_fd: int, destination_fd: int, relative_path: str
    ) -> None:
        max_file_bytes = self._snapshot_max_file_bytes
        remaining_total_bytes = self._snapshot_remaining_total_bytes
        if max_file_bytes is None or remaining_total_bytes is None:
            raise RuntimeError("checkpoint snapshot byte limits were not configured")
        limit = min(max_file_bytes, remaining_total_bytes)
        limit_kind = (
            "per-file" if max_file_bytes <= remaining_total_bytes else "per-checkpoint total"
        )
        copied = 0
        while True:
            remaining = limit - copied
            block = os.read(source_fd, min(1024 * 1024, remaining + 1))
            if not block:
                break
            if len(block) > remaining:
                raise _SnapshotCopyLimitExceededError(
                    f"source grew beyond the {limit_kind} snapshot byte limit of {limit} bytes",
                    relative_path,
                )
            view = memoryview(block)
            while view:
                written = os.write(destination_fd, view)
                if written == 0:
                    raise OSError(
                        errno.EIO, "checkpoint snapshot write made no progress", relative_path
                    )
                view = view[written:]
            copied += len(block)
        self._snapshot_remaining_total_bytes = remaining_total_bytes - copied

    def copy_snapshot_file(self, root: Path, snapshot: Path, relative_path: str) -> None:
        """Copy a file or link using retained source and destination parent descriptors."""
        from pathlib import PurePosixPath

        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in ("", ".", "..") for part in relative.parts)
        ):
            raise ValueError(f"invalid checkpoint snapshot path: {relative_path!r}")
        self.pin_source_parent(root, relative_path)
        self.create_snapshot_parents(snapshot, relative_path)
        source_parent = root.joinpath(*relative.parts[:-1])
        destination_parent = snapshot.joinpath(*relative.parts[:-1])
        source_fd = self._handles[self._key(source_parent)][0]
        destination_fd = self._handles[self._key(destination_parent)][0]
        leaf = relative.parts[-1]
        if os.name == "nt":
            source_path = source_parent / leaf
            initial = source_path.lstat()
            if stat.S_ISLNK(initial.st_mode):
                self._copy_windows_symlink(
                    source_path, destination_parent / leaf, initial, relative_path
                )
            elif stat.S_ISREG(initial.st_mode):
                self._copy_windows_regular_file(
                    source_path, destination_parent / leaf, initial, relative_path
                )
            else:
                raise OSError(
                    f"checkpoint source is not a regular file or symlink: {relative_path}"
                )
            return
        initial = os.stat(leaf, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISLNK(initial.st_mode):
            target = os.readlink(leaf, dir_fd=source_fd)
            current = os.stat(leaf, dir_fd=source_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
                raise OSError(f"checkpoint source changed while copying: {relative_path}")
            os.symlink(target, leaf, dir_fd=destination_fd)
            _fsync_directory(destination_fd)
            return
        if not stat.S_ISREG(initial.st_mode):
            raise OSError(f"checkpoint source is not a regular file or symlink: {relative_path}")
        source_fd_file = os.open(
            leaf,
            os.O_RDONLY
            | int(getattr(os, "O_NOFOLLOW", 0))
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_fd,
        )
        destination_fd_file: int | None = None
        try:
            opened = os.fstat(source_fd_file)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                initial.st_dev,
                initial.st_ino,
            ):
                raise OSError(f"checkpoint source changed before copying: {relative_path}")
            destination_fd_file = os.open(
                leaf,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | int(getattr(os, "O_NOFOLLOW", 0))
                | getattr(os, "O_CLOEXEC", 0),
                stat.S_IMODE(opened.st_mode),
                dir_fd=destination_fd,
            )
            fchmod = getattr(os, "fchmod", None)
            if fchmod is None:
                raise OSError(errno.ENOTSUP, "checkpoint snapshots require fchmod")
            fchmod(destination_fd_file, stat.S_IMODE(opened.st_mode))
            self._copy_regular_file_contents(source_fd_file, destination_fd_file, relative_path)
            after = os.fstat(source_fd_file)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ):
                raise OSError(f"checkpoint source changed during copy: {relative_path}")
            os.utime(
                leaf,
                ns=(opened.st_atime_ns, opened.st_mtime_ns),
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            os.fsync(destination_fd_file)
            _fsync_directory(destination_fd)
        finally:
            if destination_fd_file is not None:
                os.close(destination_fd_file)
            os.close(source_fd_file)

    def _open_windows_source(
        self, source: Path, initial: os.stat_result, relative_path: str, *, symlink: bool
    ) -> int:
        handle = self._create(
            str(source.absolute()),
            _FILE_READ_ATTRIBUTES if symlink else _GENERIC_READ | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ if symlink else _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if symlink else 0),
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        descriptor = self._descriptor(handle)
        try:
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino)
            if identity != (initial.st_dev, initial.st_ino):
                raise OSError(f"checkpoint source changed before copying: {relative_path}")
            is_reparse = bool(
                getattr(opened, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
            )
            if symlink:
                if not is_reparse or not stat.S_ISLNK(initial.st_mode):
                    raise OSError(f"checkpoint source changed before copying: {relative_path}")
            elif not stat.S_ISREG(opened.st_mode) or is_reparse:
                raise OSError(f"checkpoint source changed before copying: {relative_path}")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _copy_windows_symlink(
        self,
        source: Path,
        destination: Path,
        initial: os.stat_result,
        relative_path: str,
    ) -> None:
        source_descriptor = self._open_windows_source(source, initial, relative_path, symlink=True)
        try:
            target = os.readlink(source)
            current = os.fstat(source_descriptor)
            if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
                raise OSError(f"checkpoint source changed while copying: {relative_path}")
            os.symlink(target, destination)
        finally:
            os.close(source_descriptor)

    def _copy_windows_regular_file(
        self,
        source: Path,
        destination: Path,
        initial: os.stat_result,
        relative_path: str,
    ) -> None:
        source_descriptor = self._open_windows_source(source, initial, relative_path, symlink=False)
        destination_handle = self._create(
            str(destination.absolute()),
            _GENERIC_WRITE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            None,
            _CREATE_NEW,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if destination_handle in (None, _INVALID_HANDLE_VALUE):
            os.close(source_descriptor)
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        try:
            destination_descriptor = self._descriptor(destination_handle, os.O_WRONLY)
        except BaseException:
            os.close(source_descriptor)
            raise
        try:
            opened = os.fstat(source_descriptor)
            self._copy_regular_file_contents(
                source_descriptor, destination_descriptor, relative_path
            )
            after = os.fstat(source_descriptor)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ):
                raise OSError(f"checkpoint source changed during copy: {relative_path}")
            os.chmod(destination, stat.S_IMODE(opened.st_mode))
            os.utime(
                destination,
                ns=(opened.st_atime_ns, opened.st_mtime_ns),
            )
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
            os.close(source_descriptor)

    def write_json(
        self,
        root: Path,
        path: Path,
        payload: Any,
        *,
        atomic_write_json: Any,
        atomic_write_bytes_confined: Any,
    ) -> None:
        """Atomically write checkpoint metadata through its retained parent descriptor."""
        if os.name != "posix":
            atomic_write_json(path, payload)
            return

        try:
            parent_fd, identity = self._handles[self._key(path.parent)]
        except KeyError as exc:
            raise OSError(f"checkpoint output parent is not pinned: {path.parent}") from exc
        atomic_write_bytes_confined(
            root,
            path,
            json.dumps(payload, indent=2).encode("utf-8"),
            parent_fd=parent_fd,
            expected_parent_identity=identity,
        )

    def create_snapshot_parents(self, snapshot: Path, relative_path: str) -> None:
        parent = snapshot
        for part in Path(relative_path).parts[:-1]:
            if part in ("", ".", "..") or ":" in part:
                raise OSError(f"invalid checkpoint snapshot path: {relative_path!r}")
            parent = parent / part
            if self._key(parent) not in self._handles:
                self.create_directory(parent)

    def verify_created_directory(self, path: Path, expected: tuple[int, int]) -> None:
        if os.name == "posix":
            current = os.stat(
                path.name, dir_fd=self._parent_descriptor(path), follow_symlinks=False
            )
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != expected:
                raise OSError(f"checkpoint directory identity changed: {path}")
            return
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
