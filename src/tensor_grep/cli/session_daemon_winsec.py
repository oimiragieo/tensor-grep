"""Windows handle-based file trust primitives for the session-daemon secret (ctypes, no deps).

Every function is win32-only and FAILS CLOSED: any API error returns ``None`` / ``False`` so the
caller treats the object as untrusted. Nothing here follows links: the file or directory is opened
with ``FILE_FLAG_OPEN_REPARSE_POINT`` and refused if the OPENED handle is itself a reparse point
(symlink / junction), and ownership + DACL are read from that same handle (``GetSecurityInfo``),
so there is no path-spelling TOCTOU between the check and the read.
"""

from __future__ import annotations

import sys
from typing import Any

_GENERIC_READ = 0x80000000
_READ_CONTROL = 0x00020000
_FILE_SHARE_READ = 0x1  # deny write/delete while open: the object cannot be swapped under us
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SE_FILE_OBJECT = 1
_OWNER_AND_DACL = 0x1 | 0x4
_ACCESS_ALLOWED_ACE_TYPE = 0
_DENY_ACE_TYPES = frozenset({1, 6, 10, 12})  # a deny ACE only restricts; it never grants
_INHERIT_ONLY_ACE = 0x08
_MAX_READ_BYTES = 8192


def open_no_follow(path: str, *, directory: bool = False) -> Any | None:
    """Open ``path`` for reading WITHOUT following a reparse point; ``None`` if refused/failed."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _FileInfo(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(_FileInfo)]
    k32.GetFileInformationByHandle.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    flags = _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = k32.CreateFileW(
        path,
        _GENERIC_READ | _READ_CONTROL,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None
    info = _FileInfo()
    if not k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        k32.CloseHandle(handle)
        return None
    attrs = info.dwFileAttributes
    is_dir = bool(attrs & _FILE_ATTRIBUTE_DIRECTORY)
    if attrs & _FILE_ATTRIBUTE_REPARSE_POINT or is_dir != directory:
        k32.CloseHandle(handle)
        return None
    return handle


def close_handle(handle: Any) -> None:
    if sys.platform != "win32":
        return
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle(handle)


def read_all(handle: Any) -> bytes | None:
    """Read the whole (small) file through the SAME handle; ``None`` on error or oversize."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.ReadFile.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    k32.ReadFile.restype = wintypes.BOOL
    out = b""
    while len(out) <= _MAX_READ_BYTES:
        buf = ctypes.create_string_buffer(4096)
        got = wintypes.DWORD(0)
        if not k32.ReadFile(handle, buf, 4096, ctypes.byref(got), None):
            return None
        if got.value == 0:
            return out
        out += buf.raw[: got.value]
    return None


def _sid_string(sid_ptr: Any) -> str | None:
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertSidToStringSidW.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    out = wintypes.LPWSTR()
    if not adv.ConvertSidToStringSidW(sid_ptr, ctypes.byref(out)):
        return None
    try:
        return str(out.value)
    finally:
        k32.LocalFree(ctypes.cast(out, ctypes.c_void_p))


def current_user_sid() -> str | None:
    """The SID string of the current PROCESS TOKEN's user, or ``None`` on any failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    adv.OpenProcessToken.restype = wintypes.BOOL
    adv.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    adv.GetTokenInformation.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    token = ctypes.c_void_p()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x8, ctypes.byref(token)):  # TOKEN_QUERY
        return None
    try:
        needed = wintypes.DWORD(0)
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))  # 1 = TokenUser
        if needed.value == 0:
            return None
        buf = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buf, needed.value, ctypes.byref(needed)):
            return None
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_string(sid_ptr)
    finally:
        k32.CloseHandle(token)


def owner_and_dacl_sids(handle: Any) -> tuple[str, list[str]] | None:
    """``(owner SID, SIDs granted access by the DACL)`` read from the OPENED handle.

    ``None`` when anything cannot be established. A NULL DACL (everyone full access) or any
    allow-type ACE this code does not parse is reported as a sentinel entry so the caller's
    allow-list rejects it.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _AclSizeInfo(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pp = ctypes.POINTER(ctypes.c_void_p)
    adv.GetSecurityInfo.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        pp,
        pp,
        pp,
        pp,
        pp,
    ]
    adv.GetSecurityInfo.restype = wintypes.DWORD
    adv.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    ]
    adv.GetAclInformation.restype = wintypes.BOOL
    adv.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, pp]
    adv.GetAce.restype = wintypes.BOOL
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    rc = adv.GetSecurityInfo(
        handle,
        _SE_FILE_OBJECT,
        _OWNER_AND_DACL,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(sd),
    )
    if rc != 0:
        return None
    try:
        if not owner.value:
            return None
        owner_sid = _sid_string(owner)
        if owner_sid is None:
            return None
        if not dacl.value:
            return owner_sid, ["NULL-DACL"]
        info = _AclSizeInfo()
        if not adv.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), 2):
            return None
        granted: list[str] = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, index, ctypes.byref(ace)) or not ace.value:
                return None
            header = ctypes.string_at(ace.value, 8)
            ace_type, ace_flags = header[0], header[1]
            if ace_type in _DENY_ACE_TYPES or ace_flags & _INHERIT_ONLY_ACE:
                continue
            if ace_type != _ACCESS_ALLOWED_ACE_TYPE:
                granted.append(f"UNPARSED-ACE-TYPE-{ace_type}")
                continue
            sid = _sid_string(ace.value + 8)
            if sid is None:
                return None
            granted.append(sid)
        return owner_sid, granted
    finally:
        k32.LocalFree(sd)
