"""Atomic no-clobber directory publication; unsupported primitives fail closed."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path
from typing import Any


def _posix_rename(platform: str) -> Any:
    library = ctypes.CDLL(None, use_errno=True)
    name = "renameat2" if platform == "linux" else "renamex_np"
    function = getattr(library, name, None)
    if function is None:
        raise OSError(errno.ENOTSUP, f"atomic no-replace directory publication unavailable: {name}")
    function.argtypes = (
        [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        if platform == "linux"
        else [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    )
    function.restype = ctypes.c_int
    return function


def publish_directory_no_replace(source: Path, destination: Path) -> None:
    """Never replace a destination entry, including an empty directory or a dangling link."""
    source_name, destination_name = os.fspath(source), os.fspath(destination)
    source_bytes, destination_bytes = os.fsencode(source_name), os.fsencode(destination_name)
    if b"\x00" in source_bytes or b"\x00" in destination_bytes:
        raise ValueError("directory publication refuses an embedded NUL in either path")
    if sys.platform == "win32":
        # Windows rename fails when any destination entry already exists.
        os.rename(source_name, destination_name)
        return
    if sys.platform not in {"linux", "darwin"}:
        raise OSError(errno.ENOTSUP, "atomic no-replace directory publication unsupported")
    rename = _posix_rename(sys.platform)
    ctypes.set_errno(0)
    if sys.platform == "linux":
        result = rename(-100, source_bytes, -100, destination_bytes, 1)  # RENAME_NOREPLACE
    else:
        result = rename(source_bytes, destination_bytes, 4)  # RENAME_EXCL
    if result != 0:
        code = ctypes.get_errno() or errno.EIO
        raise OSError(code, os.strerror(code), destination_name)
