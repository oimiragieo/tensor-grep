"""Keep the downloaded object alive and refuse to remove an unrelated replacement."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4


class NativeDownloadCleanupError(RuntimeError):
    """The downloaded object could not be safely cleaned up or released."""


def read_verified_asset(pin: int, limit: int, expected: str | None) -> bytes:
    os.lseek(pin, 0, os.SEEK_SET)
    chunks = []
    total = 0
    while total <= limit:
        chunk = os.read(pin, min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    data = b"".join(chunks)
    if total > limit or not expected or hashlib.sha256(data).hexdigest() != expected:
        raise NativeDownloadCleanupError("managed native asset changed after verification")
    return data


def publish_fresh_asset(destination: Path, data: bytes, parent_final: str, held: ExitStack) -> None:
    from tensor_grep.cli import native_frontdoor
    from tensor_grep.cli import session_daemon_winsec as winsec
    from tensor_grep.io import windows_publication

    sid = winsec.current_user_sid()
    if sid is None:
        raise NativeDownloadCleanupError("native publication owner identity is unavailable")
    temporary = destination.with_name(f"{destination.name}.{uuid4().hex}.tmp")
    descriptor = windows_publication.open_temporary(temporary, restrict_to_sid=sid)
    completed = False
    guard: int | None = None
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        expected = hashlib.sha256(data).hexdigest()
        native_frontdoor._validate_managed_publication_descriptor(
            descriptor, parent_final, temporary.name
        )
        read_verified_asset(descriptor, len(data), expected)
        windows_publication.rename_open(descriptor, destination, replace=False)
        native_frontdoor._validate_managed_publication_descriptor(
            descriptor, parent_final, destination.name
        )
        read_verified_asset(descriptor, len(data), expected)
        guard = acquire_guard(destination, descriptor)
        completed = True
    finally:
        prior = sys.exception()
        errors = []
        if not completed:
            try:
                windows_publication.discard_open(descriptor)
            except OSError as exc:
                errors.append(f"native publication cleanup failed: {exc}")
        try:
            os.close(descriptor)
        except OSError as exc:
            errors.append(f"native publication close failed: {exc}")
        if errors:
            if guard is not None:
                try:
                    os.close(guard)
                except OSError as exc:
                    errors.append(f"native publication guard close failed: {exc}")
            prefix = f"{prior}; " if prior is not None else ""
            raise NativeDownloadCleanupError(prefix + "; ".join(errors))
    assert guard is not None
    try:
        with ExitStack() as local:
            pin = local.enter_context(hold_private_asset(destination, guard, parent_final))
            read_verified_asset(pin, len(data), expected)
            finished_guard, guard = guard, None
            try:
                os.close(finished_guard)
            except OSError as exc:
                raise NativeDownloadCleanupError(
                    f"native publication guard close failed; candidate or guard may remain: {exc}"
                ) from exc
            transferred = local.pop_all()
            try:
                held.callback(transferred.close)
            except BaseException:
                transferred.close()
                raise
    finally:
        if guard is not None:
            prior = sys.exception()
            dispose_guarded(destination, guard, state=f"publication handoff failed: {prior}")


def _same_file(first: int, second: int) -> bool:
    left, right = os.fstat(first), os.fstat(second)
    return bool(left.st_ino and right.st_ino) and (left.st_dev, left.st_ino) == (
        right.st_dev,
        right.st_ino,
    )


def acquire_guard(path: Path, writer: int) -> int:
    if sys.platform == "win32":
        import msvcrt

        from tensor_grep.cli import session_daemon_winsec as winsec

        handle = winsec.open_no_follow(str(path), share=7)
        if handle is None:
            raise OSError("cannot pin downloaded native asset; temporary may remain")
        try:
            guard = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except BaseException:
            winsec.close_handle(handle)
            raise
    else:
        guard = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(guard).st_mode) or not _same_file(writer, guard):
            raise OSError("downloaded native asset changed during transfer; temporary may remain")
    except BaseException:
        os.close(guard)
        raise
    return guard


@contextmanager
def hold_private_asset(path: Path, guard: int, parent_final: str) -> Iterator[int]:
    """Keep a validated Windows download read-only while it is checked and copied."""
    if sys.platform != "win32":
        raise NativeDownloadCleanupError("private native asset pin requires Windows")
    import msvcrt

    from tensor_grep.cli import native_frontdoor
    from tensor_grep.cli import session_daemon_winsec as winsec

    handle = winsec.open_no_follow(str(path), share=1)
    if handle is None:
        raise NativeDownloadCleanupError("native asset cannot be pinned against mutation")
    try:
        pin = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except BaseException:
        winsec.close_handle(handle)
        raise
    try:
        if not _same_file(guard, pin):
            raise NativeDownloadCleanupError("native asset changed before its read pin")
        native_frontdoor._validate_managed_leaf_handle(handle, parent_final, path.name)
        sid = winsec.current_user_sid()
        details = winsec.owner_and_dacl_entries(handle)
        if (
            sid is None
            or details is None
            or not details[1]
            or any(principal != sid for principal, _mask in details[1])
        ):
            raise NativeDownloadCleanupError("native asset ACL is not private")
        yield pin
    finally:
        prior = sys.exception()
        try:
            os.close(pin)
        except OSError as exc:
            prefix = f"{prior}; " if prior is not None else ""
            raise NativeDownloadCleanupError(
                prefix + f"native asset pin close failed: {exc}"
            ) from exc


def discard_guarded(path: Path, guard: int) -> None:
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        from tensor_grep.io import windows_publication

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
        handle = create(str(path), 0x00010080, 7, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("cannot reopen downloaded native asset for cleanup")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except BaseException:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
            raise
        try:
            if not _same_file(guard, descriptor):
                raise OSError("downloaded native asset changed; cleanup refused")
            windows_publication.discard_open(descriptor)
        finally:
            previous_error = sys.exception()
            try:
                os.close(descriptor)
            except OSError as close_error:
                previous = f"{previous_error}; " if previous_error else ""
                raise OSError(
                    f"{previous}delete handle close failed: {close_error}"
                ) from close_error
        return

    raise OSError("identity-bound native download cleanup is unavailable on this platform")


def dispose_guarded(path: Path, guard: int, *, state: str) -> None:
    errors: list[str] = []
    try:
        try:
            discard_guarded(path, guard)
        except OSError as exc:
            errors.append(str(exc))
    finally:
        try:
            os.close(guard)
        except OSError as exc:
            errors.append(f"guard close failed: {exc}")
    if errors:
        raise NativeDownloadCleanupError(
            f"native download cleanup failed {state}: {'; '.join(errors)}"
        )
