"""Windows temporary publication and cleanup bound to the originally opened object."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any


def _kernel() -> Any:
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def open_temporary(path: Path) -> int:
    """Claim a new leaf and deny other handles write/delete access through publication."""
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    import msvcrt

    kernel = _kernel()
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
    # GENERIC_READ | GENERIC_WRITE | DELETE, FILE_SHARE_READ, CREATE_NEW, OPEN_REPARSE_POINT.
    handle = create(str(path), 0xC0010000, 1, None, 1, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except BaseException:
        try:
            _discard_handle(handle)
        finally:
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)
        raise


def _set_handle_information(handle: int, kind: int, buffer: Any, size: int) -> None:
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    kernel = _kernel()
    set_information = kernel.SetFileInformationByHandle
    set_information.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_information.restype = wintypes.BOOL
    if not set_information(handle, kind, buffer, size):
        code = ctypes.get_last_error()
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, "confined destination appeared before publication")
        raise ctypes.WinError(code)


def _set_information(descriptor: int, kind: int, buffer: Any, size: int) -> None:
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    import msvcrt

    _set_handle_information(msvcrt.get_osfhandle(descriptor), kind, buffer, size)


def rename_open(descriptor: int, path: Path, *, replace: bool) -> None:
    """Rename THIS HANDLE, without resolving any source filename after bytes were written."""

    class RenameInformation(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOL),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    encoded = str(path.absolute()).encode("utf-16-le")
    offset = RenameInformation.FileName.offset
    # SetFileInformationByHandle's DOS-path conversion also consumes a terminating WCHAR.
    size = offset + len(encoded) + ctypes.sizeof(wintypes.WCHAR)
    buffer = ctypes.create_string_buffer(max(size, ctypes.sizeof(RenameInformation)))
    information = RenameInformation.from_buffer(buffer)
    information.ReplaceIfExists = int(replace)
    information.RootDirectory = None
    information.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
    _set_information(descriptor, 3, buffer, size)  # FileRenameInfo


def discard_open(descriptor: int) -> None:
    """Mark ONLY the held temporary object for deletion when its last handle closes."""
    delete = wintypes.BOOLEAN(1)
    _set_information(
        descriptor, 4, ctypes.byref(delete), ctypes.sizeof(delete)
    )  # FileDispositionInfo


def _discard_handle(handle: int) -> None:
    delete = wintypes.BOOLEAN(1)
    _set_handle_information(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete))
