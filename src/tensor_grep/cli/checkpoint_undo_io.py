"""Identity checks and best-effort confined checkpoint rollback."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from functools import partial
from pathlib import Path

from tensor_grep.cli._index_lock import (
    _is_link_or_junction,
    atomic_write_bytes_confined,
    dir_identity,
)
from tensor_grep.io.confined import (
    read_confined,
    unlink_confined,
    validate_confined_parent_prefix,
)


class _PinnedFileIdentity(tuple[int, int]):
    _descriptor: int | None
    _temporary: Path | None

    def __new__(cls, identity: tuple[int, int], descriptor: int) -> _PinnedFileIdentity:
        pinned = super().__new__(cls, identity)
        pinned._descriptor = descriptor
        return pinned

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __del__(self) -> None:
        self.close()


def close_published_identity(identity: tuple[int, int] | None) -> None:
    if isinstance(identity, _PinnedFileIdentity):
        identity.close()


def _pin_published_file(path: Path, source_descriptor: int) -> int:
    if os.name != "nt":
        descriptor = os.dup(source_descriptor)
    else:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
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
        handle = create(
            str(path),
            0x80000000,
            0x7,
            None,
            3,
            0x00200000,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        try:
            descriptor = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
                handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
        except BaseException:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            raise

    try:
        pinned = os.fstat(descriptor)
        published = os.fstat(source_descriptor)
        if (pinned.st_dev, pinned.st_ino) != (published.st_dev, published.st_ino):
            raise OSError(
                "checkpoint restore target changed before its published handle was retained"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _check_pinned_leaf(
    root: Path,
    path: Path,
    parent_identity: tuple[int, int] | None,
    file_identity: tuple[int, int] | None,
) -> None:
    if not isinstance(file_identity, _PinnedFileIdentity):
        return
    descriptor = file_identity._descriptor
    if descriptor is None:
        raise OSError("checkpoint restore target's pinned handle is closed")
    temporary = getattr(file_identity, "_temporary", None)
    if temporary is not None:
        try:
            unlink_confined(
                root,
                temporary,
                expected_parent_identity=parent_identity,
                expected_file_identity=file_identity,
            )
        except FileNotFoundError:
            pass
        file_identity._temporary = None
    validate_confined_parent_prefix(root, path)
    info = path.lstat()
    pinned = os.fstat(descriptor)
    if (
        (parent_identity is not None and dir_identity(path.parent) != parent_identity)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or _is_link_or_junction(path)
        or (info.st_dev, info.st_ino) != (pinned.st_dev, pinned.st_ino)
    ):
        raise OSError("checkpoint restore target no longer names its pinned published file")


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


def unlink_with_pinned_parent(
    root: Path,
    path: Path,
    captured_parent: tuple[int, int] | None,
    captured_file: tuple[int, int] | None,
) -> None:
    """Remove only the undo-created leaf under the parent recorded in its journal."""
    if captured_parent is None or captured_file is None:
        raise OSError("Refusing to remove an undo target without captured file identities")
    _check_pinned_leaf(root, path, captured_parent, captured_file)
    if dir_identity(path.parent) != captured_parent:
        raise OSError("Refusing to remove an undo target whose parent directory changed")
    unlink_confined(
        root,
        path,
        expected_parent_identity=captured_parent,
        expected_file_identity=captured_file,
    )


def publish_restored_file(
    root: Path,
    path: Path,
    data: bytes,
    metadata: os.stat_result,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    expected_file_identity: tuple[int, int] | None = None,
    expect_absent: bool = False,
    on_new_file_identity: Callable[[tuple[int, int]], None] | None = None,
    on_publication: Callable[[], None] | None = None,
) -> None:
    """Restore bytes atomically under the expected parent without writing through hardlinks."""
    _check_pinned_leaf(root, path, expected_parent_identity, expected_file_identity)
    times = (metadata.st_atime_ns, metadata.st_mtime_ns)
    mode = stat.S_IMODE(metadata.st_mode)

    def preserve_metadata(descriptor: int, temporary: Path) -> None:
        if os.name == "nt":
            os.utime(temporary, ns=times)
            os.chmod(temporary, mode)
        else:
            os.utime(descriptor, ns=times)
            fchmod = getattr(os, "fchmod", None)
            if fchmod is None:
                raise OSError("checkpoint restore cannot preserve file mode on this platform")
            fchmod(descriptor, mode)
        if on_new_file_identity is not None:
            info = os.fstat(descriptor)
            pinned = _PinnedFileIdentity(
                (info.st_dev, info.st_ino), _pin_published_file(temporary, descriptor)
            )
            pinned._temporary = temporary
            try:
                on_new_file_identity(pinned)
            except BaseException:
                pinned.close()
                raise

    def record_published_identity() -> None:
        if on_publication is not None:
            on_publication()

    atomic_write_bytes_confined(
        root,
        path,
        data,
        replace=not expect_absent,
        temp_ready=preserve_metadata,
        expected_parent_identity=expected_parent_identity,
        expected_file_identity=expected_file_identity,
        after_publish=record_published_identity,
    )


def rollback_overwrites(
    root: Path,
    entries: list[
        tuple[
            Path,
            bytes | None,
            os.stat_result | None,
            tuple[int, int],
            list[tuple[int, int] | None],
            list[bool],
        ]
    ],
    *,
    interruptions: list[BaseException] | None = None,
) -> list[Path]:
    failures: list[Path] = []
    for entry in reversed(entries):
        path = entry[0]
        _attempt_rollback(
            path,
            partial(_rollback_overwrite, root, entry),
            failures,
            interruptions,
        )
    return failures


def _rollback_overwrite(
    root: Path,
    entry: tuple[
        Path,
        bytes | None,
        os.stat_result | None,
        tuple[int, int],
        list[tuple[int, int] | None],
        list[bool],
    ],
) -> None:
    path, prior_bytes, prior_info, parent_identity, published_identity, publication_state = entry
    expected_file_identity = published_identity[0]
    if expected_file_identity is None:
        if publication_state[0]:
            raise OSError("Refusing rollback without a pinned published identity")
        # Publication did not happen, so verify that the original target is still in
        # place before treating this entry as untouched.
        if prior_info is not None:
            check_restore_target(
                root, path, parent_identity, (prior_info.st_dev, prior_info.st_ino)
            )
        return
    validate_confined_parent_prefix(root, path)
    if dir_identity(path.parent) != parent_identity:
        raise OSError("checkpoint restore target parent changed")
    if prior_bytes is None:
        descriptor = getattr(expected_file_identity, "_descriptor", None)
        if descriptor is None or os.fstat(descriptor).st_nlink == 0:
            close_published_identity(expected_file_identity)
            if not os.path.lexists(path):
                return
            raise OSError("checkpoint restore target was replaced after rollback unlink")
    restored = published_identity[1]
    if restored is not None:
        try:
            _check_pinned_leaf(root, path, parent_identity, restored)
            return
        except OSError:
            pass
    try:
        _check_pinned_leaf(root, path, parent_identity, expected_file_identity)
    except OSError:
        if not publication_state[0] and prior_info is not None:
            check_restore_target(
                root, path, parent_identity, (prior_info.st_dev, prior_info.st_ino)
            )
            return
        raise
    if restored is not None:
        close_published_identity(restored)
        published_identity[1] = None
    if prior_bytes is None:
        unlink_with_pinned_parent(root, path, parent_identity, expected_file_identity)
        return
    if prior_info is None:
        raise OSError("Refusing to restore an undo target without captured identities")
    publish_restored_file(
        root,
        path,
        prior_bytes,
        prior_info,
        expected_parent_identity=parent_identity,
        expected_file_identity=expected_file_identity,
        on_new_file_identity=partial(published_identity.__setitem__, 1),
    )


def _attempt_rollback(
    path: Path,
    operation: Callable[[], None],
    failures: list[Path],
    interruptions: list[BaseException] | None,
) -> None:
    pending_interruption: BaseException | None = None
    failed = False
    while True:
        try:
            operation()
            break
        except (KeyboardInterrupt, SystemExit) as interruption:
            if interruptions is None:
                raise
            if pending_interruption is None:
                pending_interruption = interruption
            continue
        except OSError:
            failed = True
            break
    while True:
        try:
            if failed:
                failures.append(path)
            if pending_interruption is not None and interruptions is not None and not interruptions:
                interruptions.append(pending_interruption)
            return
        except (KeyboardInterrupt, SystemExit) as interruption:
            if interruptions is None:
                raise
            if pending_interruption is None:
                pending_interruption = interruption


def _check_removed_parent(path: Path, identity: tuple[int, int]) -> None:
    if dir_identity(path.parent) != identity:
        raise OSError("checkpoint removed parent identity changed")


def rollback_removes(
    root: Path,
    entries: list[tuple[Path, bytes, os.stat_result, tuple[int, int]]],
    restored_identities: dict[Path, list[tuple[int, int] | None]],
    *,
    interruptions: list[BaseException] | None = None,
) -> list[Path]:
    failures: list[Path] = []
    for entry in reversed(entries):
        path = entry[0]
        restored_identity = restored_identities.setdefault(path, [None])
        _attempt_rollback(
            path,
            partial(_rollback_remove, root, entry, restored_identity),
            failures,
            interruptions,
        )
    return failures


def _rollback_remove(
    root: Path,
    entry: tuple[Path, bytes, os.stat_result, tuple[int, int]],
    restored_identity: list[tuple[int, int] | None],
) -> None:
    path, prior_bytes, prior_info, parent_identity = entry
    check = partial(_check_removed_parent, path, parent_identity)
    check()
    restored = restored_identity[0]
    if restored is not None:
        try:
            _check_pinned_leaf(root, path, parent_identity, restored)
            return
        except OSError:
            if os.path.lexists(path):
                raise
            close_published_identity(restored)
            restored_identity[0] = None
    if os.path.lexists(path):
        current = path.lstat()
        identity = (prior_info.st_dev, prior_info.st_ino)
        if (current.st_dev, current.st_ino) != identity:
            raise OSError("checkpoint removed target was replaced before rollback")
        check_restore_target(root, path, parent_identity, identity)
        if read_confined(root, path, prior_info.st_size) != prior_bytes:
            raise OSError("checkpoint removed target content changed before rollback")
        check_restore_target(root, path, parent_identity, identity)
        return
    publish_restored_file(
        root,
        path,
        prior_bytes,
        prior_info,
        expected_parent_identity=parent_identity,
        expect_absent=True,
        on_new_file_identity=partial(restored_identity.__setitem__, 0),
    )
