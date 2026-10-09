"""Machine-private authentication for untrusted checkout-provided AST cache entries."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path


def _machine_key_path() -> Path:
    """Use OS account locations; checkout/environment overrides cannot select the key."""
    if sys.platform == "win32":
        import ctypes
        import uuid
        from ctypes import wintypes

        identifier = (ctypes.c_ubyte * 16).from_buffer_copy(
            uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
        )
        shell = ctypes.WinDLL("shell32")
        get_folder = shell.SHGetKnownFolderPath
        get_folder.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.c_void_p]
        get_folder.restype = ctypes.c_long
        allocated = ctypes.c_void_p()
        if get_folder(ctypes.byref(identifier), 0, None, ctypes.byref(allocated)) != 0:
            raise OSError("machine key account directory unavailable")
        try:
            root = Path(ctypes.wstring_at(allocated))
        finally:
            free = ctypes.WinDLL("ole32").CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(allocated)
    else:
        import pwd

        root = Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".local" / "state"
    return root / "tensor-grep-symbol-cache" / "signing-key.json"


def load_machine_key(checkout: Path) -> bytes | None:
    """Create exclusively with existing handle-verified private-secret primitives.

    No key is read from or copied into the selected checkout. An unavailable,
    linked, foreign-owned, or broadly accessible key disables persistent reuse.
    """
    from tensor_grep.cli import session_daemon_trust as trust
    from tensor_grep.cli import session_daemon_winsec as winsec

    pinned = None
    try:
        path = _machine_key_path()
        if path.is_relative_to(checkout) or path.resolve(strict=False).is_relative_to(checkout):
            return None
        existing = trust._read_user_secret(path)
        if existing is not None:
            return existing
        if os.path.lexists(path) or not trust._ensure_secret_dir(path.parent):
            return None
        if sys.platform == "win32":
            pinned = winsec.open_no_follow(str(path.parent), directory=True, share=0x3)
            if pinned is None:
                return None
        if not trust._parent_trusted(path.parent) or not trust._ancestors_trusted(path.parent):
            return None
        try:
            payload = {"secret": secrets.token_hex(32)}
            if sys.platform == "win32":
                trust._write_secret_windows(path, payload)
            else:
                trust._write_secret_posix(path, payload)
        except FileExistsError:
            pass  # a concurrent first use won exclusive publication; verify its actual handle
        return trust._read_user_secret(path)
    except (OSError, ValueError, RuntimeError):
        return None
    finally:
        if pinned is not None:
            winsec.close_handle(pinned)
