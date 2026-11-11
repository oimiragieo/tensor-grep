"""Windows temporary publication and cleanup bound to the originally opened object."""

from __future__ import annotations

import ctypes
import errno
import os
import re
import stat
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any


def _kernel() -> Any:
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]


def open_temporary(path: Path, *, restrict_to_sid: str | None = None) -> int:
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
    security_descriptor = ctypes.c_void_p()
    attributes_pointer = None
    try:
        if restrict_to_sid is not None:
            if (
                type(restrict_to_sid) is not str
                or len(restrict_to_sid) > 184
                or re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+){1,15}", restrict_to_sid) is None
            ):
                raise ValueError("invalid Windows SID")
            components = restrict_to_sid.split("-")[2:]
            limits = (2**48, *([2**32] * (len(components) - 1)))
            if any(
                str(int(part)) != part or int(part) >= limit
                for part, limit in zip(components, limits, strict=True)
            ):
                raise ValueError("invalid Windows SID")

            class SecurityAttributes(ctypes.Structure):
                _fields_ = [
                    ("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL),
                ]

            adv = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
            convert = adv.ConvertStringSecurityDescriptorToSecurityDescriptorW
            convert.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
            ]
            convert.restype = wintypes.BOOL
            if not convert(
                f"D:P(A;;FA;;;{restrict_to_sid})", 1, ctypes.byref(security_descriptor), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
            attributes = SecurityAttributes(
                ctypes.sizeof(SecurityAttributes), security_descriptor, False
            )
            attributes_pointer = ctypes.byref(attributes)
        # GENERIC_READ | GENERIC_WRITE | DELETE, FILE_SHARE_READ, CREATE_NEW, OPEN_REPARSE_POINT.
        handle = create(str(path), 0xC0010000, 1, attributes_pointer, 1, 0x00200080, None)
        create_error = ctypes.get_last_error()  # type: ignore[attr-defined]
    finally:
        if security_descriptor.value:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree(security_descriptor)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(create_error)  # type: ignore[attr-defined]
    try:
        descriptor: int = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
            handle, os.O_RDWR | getattr(os, "O_BINARY", 0)
        )
        return descriptor
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
        code = ctypes.get_last_error()  # type: ignore[attr-defined]
        if code in (80, 183):
            raise FileExistsError(errno.EEXIST, "confined destination appeared before publication")
        raise ctypes.WinError(code)  # type: ignore[attr-defined]


def _set_information(descriptor: int, kind: int, buffer: Any, size: int) -> None:
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    import msvcrt

    _set_handle_information(msvcrt.get_osfhandle(descriptor), kind, buffer, size)


def _rename_open(descriptor: int, path: Path, *, flags: int, kind: int) -> None:
    """Rename THIS HANDLE, without resolving any source filename after bytes were written."""

    class RenameInformation(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
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
    information.Flags = flags
    information.RootDirectory = None
    information.FileNameLength = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
    _set_information(descriptor, kind, buffer, size)


def discard_open(descriptor: int) -> None:
    """Mark ONLY the held temporary object for deletion when its last handle closes."""
    delete = wintypes.BOOLEAN(1)
    _set_information(
        descriptor, 4, ctypes.byref(delete), ctypes.sizeof(delete)
    )  # FileDispositionInfo


def _discard_handle(handle: int) -> None:
    delete = wintypes.BOOLEAN(1)
    _set_handle_information(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete))


def rename_open(descriptor: int, path: Path, *, replace: bool) -> None:
    """Preserve legacy rename semantics for existing confined-publication callers."""
    _rename_open(descriptor, path, flags=int(replace), kind=3)  # FileRenameInfo


def replace_open(descriptor: int, path: Path) -> None:
    """Replace by held source handle, retaining old readers; caller confines destination."""
    if sys.platform != "win32":
        raise OSError("Windows handle publication is unavailable on this platform")
    # FileRenameInfoEx: REPLACE_IF_EXISTS | POSIX_SEMANTICS, with no legacy fallback.
    _rename_open(descriptor, path, flags=3, kind=22)


def unlink_open(
    path: Path, expected_identity: tuple[int, int], *, allow_reparse: bool = False
) -> None:
    """Delete only the verified regular file or reparse point held by this handle."""
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
    handle = create(str(path), 0x00010080, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
    try:
        descriptor: int = msvcrt.open_osfhandle(  # type: ignore[attr-defined]
            handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    try:
        info = os.fstat(descriptor)
        is_reparse = stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & 0x400
        )
        if (
            (not stat.S_ISREG(info.st_mode) and not (allow_reparse and is_reparse))
            or (is_reparse and not allow_reparse)
            or (info.st_dev, info.st_ino) != expected_identity
        ):
            raise OSError(f"confined leaf identity changed: {path}")
        discard_open(descriptor)
    finally:
        os.close(descriptor)
