"""Keep the downloaded object alive and refuse to remove an unrelated replacement."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


class NativeDownloadCleanupError(RuntimeError):
    """The downloaded object could not be safely cleaned up or released."""


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
