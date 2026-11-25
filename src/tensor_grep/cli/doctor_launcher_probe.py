"""Safe static ownership checks for launcher candidates reported by ``tg doctor``."""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

_MAX_SHIM_BYTES = 4096
_MAX_MARKER_BYTES = 16 * 1024
_MAX_NATIVE_BYTES = 512 * 1024 * 1024
_MANAGED_METADATA_FILENAME = "tg-native-metadata.json"
_MANAGED_METADATA_ARTIFACT = "tensor_grep_native_frontdoor_metadata"
_WINDOWS_EXE_BRIDGE_MARKER = "tg.exe.tensor-grep-bridge"
_WINDOWS_EXE_BRIDGE_MARKER_CONTENT = "tensor-grep managed tg.exe bridge\n"
_SIMPLE_DELEGATE_RE = re.compile(
    r"&\s+(?P<target>'[^'\r\n]+'|\"[^\"\r\n]+\"|\$PSScriptRoot[\\/]tg\.exe)\s+@args",
    re.IGNORECASE,
)


def strip_windows_extended_prefix(path_str: str) -> str:
    """Strip Windows extended-length drive/UNC prefixes while preserving device namespaces."""
    lower = path_str.lower()
    if lower.startswith("\\\\?\\unc\\"):
        return "\\\\" + path_str[8:]
    if (
        path_str.startswith("\\\\?\\")
        and len(path_str) >= 7
        and path_str[4].isalpha()
        and path_str[5:7] in {":\\", ":/"}
    ):
        return path_str[4:]
    return path_str


def format_stale_daemon_cleanup_hint(root_path: str) -> str:
    """Format a shell-quoted cleanup hint for stale session_daemon metadata."""
    import os
    import shlex
    import subprocess

    clean_root = strip_windows_extended_prefix(root_path)
    quoted = subprocess.list2cmdline([clean_root]) if os.name == "nt" else shlex.quote(clean_root)
    return f"Run `tg session daemon stop {quoted}` to remove stale daemon metadata."


_PROBE_ENV_ALLOWLIST = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "PATHEXT",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "TG_FRONTDOOR_HOPS",
)


def sanitized_probe_env(raw_env: dict[str, str] | None) -> dict[str, str] | None:
    """Return a minimal allowlisted environment for doctor version probes."""
    if raw_env is None:
        return None
    return {key: raw_env[key] for key in _PROBE_ENV_ALLOWLIST if key in raw_env}


def _strip_win32_device_prefix(path_str: str) -> str:
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def _open_regular_nontraversing(path: Path, *, share_read_only: bool = False) -> int | None:
    """Open ``path`` without following leaf reparse points/symlinks and verify final identity."""
    if sys.platform == "win32" and os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class _ByHandleFileInfo(ctypes.Structure):
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

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInfo)]
        get_info.restype = wintypes.BOOL
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final_path.restype = wintypes.DWORD
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        invalid_handle = ctypes.c_void_p(-1).value
        generic_read = 0x80000000
        file_share_read = 0x00000001
        file_share_all = 0x00000007
        open_existing = 3
        file_flag_open_reparse_point = 0x00200000
        handle = create_file(
            os.path.abspath(path),
            generic_read,
            file_share_read if share_read_only else file_share_all,
            None,
            open_existing,
            file_flag_open_reparse_point,
            None,
        )
        if handle == invalid_handle or handle is None:
            return None
        try:
            info = _ByHandleFileInfo()
            if not get_info(handle, ctypes.byref(info)):
                return None
            if info.dwFileAttributes & (
                stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY
            ):
                return None
            buf = ctypes.create_unicode_buffer(1024)
            length = get_final_path(handle, buf, len(buf), 0)
            if length == 0 or length >= len(buf):
                return None
            final_lexical = _strip_win32_device_prefix(buf.value).replace("/", "\\").casefold()
            if final_lexical != _lexical_path_key(path):
                return None
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
            handle = None
            return fd
        finally:
            if handle is not None:
                close_handle(handle)

    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(str(path), flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None
        resolved = path.resolve(strict=True)
        if _lexical_path_key(path) != _lexical_path_key(resolved):
            os.close(fd)
            return None
        return fd
    except OSError:
        os.close(fd)
        return None


def _bounded_bytes(path: Path, limit: int) -> bytes | None:
    fd = _open_regular_nontraversing(path)
    if fd is None:
        return None
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            contents = stream.read(limit + 1)
    except OSError:
        if fd >= 0:
            os.close(fd)
        return None
    return contents if len(contents) <= limit else None


def _shim_lines(candidate: Path) -> list[str] | None:
    contents = _bounded_bytes(candidate, _MAX_SHIM_BYTES)
    if contents is None:
        return None
    try:
        return [line.strip() for line in contents.decode("utf-8-sig").splitlines() if line.strip()]
    except UnicodeError:
        return None


def _path_key(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    return str(resolved).replace("/", "\\").casefold()


def _lexical_path_key(path: Path) -> str:
    return os.path.abspath(path).replace("/", "\\").casefold()


def _same_path(left: Path, right: Path) -> bool:
    return _lexical_path_key(left) == _lexical_path_key(right)


def _home_directories() -> list[Path]:
    if os.name == "nt":
        try:
            from tensor_grep.cli.managed_runtime_candidate import _windows_profile_directory

            home = _windows_profile_directory()
        except (OSError, RuntimeError):
            return []
        return [home] if home.is_absolute() else []

    homes: list[Path] = []
    seen: set[str] = set()
    for env_name in ("USERPROFILE", "HOME"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        home = Path(raw).expanduser()
        if not home.is_absolute():
            continue
        key = _path_key(home)
        if key not in seen:
            seen.add(key)
            homes.append(home)
    if homes:
        return homes
    try:
        home = Path.home()
    except RuntimeError:
        return []
    return [home] if home.is_absolute() else []


def _managed_bin_directories() -> list[Path]:
    return [home / ".tensor-grep" / "bin" for home in _home_directories()]


def _compatibility_directories() -> list[Path]:
    return [
        directory
        for home in _home_directories()
        for directory in (home / "bin", home / ".local" / "bin")
    ]


def _resolve_file(path: Path) -> Path | None:
    try:
        metadata = path.lstat()
        if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return None
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if _lexical_path_key(path) != _lexical_path_key(resolved):
        return None
    try:
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def _managed_target(target: Path) -> Path | None:
    resolved = _resolve_file(target)
    if resolved is None or resolved.name.casefold() != "tg.exe":
        return None
    metadata = _bounded_bytes(resolved.with_name(_MANAGED_METADATA_FILENAME), _MAX_MARKER_BYTES)
    if metadata is None:
        return None
    try:
        payload = json.loads(metadata.decode("utf-8-sig"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    asset_name = payload.get("asset_name")
    if (
        payload.get("artifact") != _MANAGED_METADATA_ARTIFACT
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(asset_name, str)
        or not asset_name.casefold().endswith(".exe")
    ):
        return None
    return resolved


def _marked_bridge_target(target: Path) -> Path | None:
    resolved = _resolve_file(target)
    if resolved is None or resolved.name.casefold() != "tg.exe":
        return None
    marker = _bounded_bytes(resolved.with_name(_WINDOWS_EXE_BRIDGE_MARKER), 128)
    if marker not in {
        _WINDOWS_EXE_BRIDGE_MARKER_CONTENT.encode("ascii"),
        _WINDOWS_EXE_BRIDGE_MARKER_CONTENT.replace("\n", "\r\n").encode("ascii"),
    }:
        return None
    for managed_bin in _managed_bin_directories():
        managed = _managed_target(managed_bin / "tg.exe")
        if managed is not None and _same_file_content(resolved, managed):
            return resolved
    return None


def _same_file_content(left: Path, right: Path) -> bool:
    left_fd = _open_regular_nontraversing(left)
    if left_fd is None:
        return False
    right_fd = _open_regular_nontraversing(right)
    if right_fd is None:
        os.close(left_fd)
        return False
    try:
        with (
            os.fdopen(left_fd, "rb") as left_stream,
            os.fdopen(right_fd, "rb") as right_stream,
        ):
            left_fd = right_fd = -1
            left_size = os.fstat(left_stream.fileno()).st_size
            right_size = os.fstat(right_stream.fileno()).st_size
            if left_size != right_size or left_size > _MAX_NATIVE_BYTES:
                return False
            while True:
                left_chunk = left_stream.read(1024 * 1024)
                right_chunk = right_stream.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False
    finally:
        for fd in (left_fd, right_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _simple_delegate_target(lines: list[str], candidate: Path) -> Path | None:
    if len(lines) not in {1, 2}:
        return None
    if len(lines) == 2 and lines[1].casefold() != "exit $lastexitcode":
        return None
    command = _SIMPLE_DELEGATE_RE.fullmatch(lines[0])
    if command is None:
        return None
    raw_target = command.group("target")
    if raw_target.casefold().startswith("$psscriptroot"):
        return candidate.parent / "tg.exe"
    quote = raw_target[0]
    raw_target = raw_target[1:-1]
    if quote == '"' and ("$" in raw_target or "`" in raw_target):
        return None
    target = Path(raw_target)
    return target if target.is_absolute() else None


def _managed_template_target(lines: list[str], managed_bin: Path) -> Path | None:
    target = managed_bin / "tg.exe"
    sidecar = managed_bin.parent / ".venv" / "Scripts" / "python.exe"
    unsafe_literal_chars = {'"', "$", "`", "\r", "\n"}
    if any(char in str(target) or char in str(sidecar) for char in unsafe_literal_chars):
        return None
    requested = re.fullmatch(
        r'\$env:TG_NATIVE_FRONTDOOR_REQUESTED_FLAVOR = "(?:cpu|nvidia)"',
        lines[3] if len(lines) > 3 else "",
        re.IGNORECASE,
    )
    installed = re.fullmatch(
        r'\$env:TG_NATIVE_FRONTDOOR_FLAVOR = "(?:cpu|nvidia)"',
        lines[4] if len(lines) > 4 else "",
        re.IGNORECASE,
    )
    expected = [
        '$env:PYTHONUTF8 = "1"',
        '$env:PYTHONIOENCODING = "utf-8"',
        f'$env:TG_SIDECAR_PYTHON = "{sidecar}"',
        lines[3] if requested else "",
        lines[4] if installed else "",
        f'if (Test-Path -LiteralPath "{target}") {{',
        f'$env:TG_NATIVE_TG_BINARY = "{target}"',
        f'& "{target}" @args',
        "} else {",
        f'& "{sidecar}" -X utf8 -m tensor_grep @args',
        "}",
        "exit $LASTEXITCODE",
    ]
    if lines != expected:
        return None
    return _managed_target(target)


def _managed_shim_target(candidate: Path, lines: list[str], managed_bin: Path) -> Path | None:
    target = _managed_template_target(lines, managed_bin)
    if target is not None:
        return target
    simple_target = _simple_delegate_target(lines, candidate)
    if simple_target is None or not _same_path(simple_target, managed_bin / "tg.exe"):
        return None
    return _managed_target(simple_target)


def powershell_shim_target(candidate: Path) -> Path | None:
    """Resolve only an installer-owned PowerShell shim to its marked native target."""
    if candidate.name.casefold() != "tg.ps1":
        return None
    is_managed = any(
        _same_path(candidate, managed_bin / "tg.ps1") for managed_bin in _managed_bin_directories()
    )
    is_compat = any(
        _same_path(candidate.parent, compatibility_dir)
        for compatibility_dir in _compatibility_directories()
    )
    if not (is_managed or is_compat):
        return None
    resolved_candidate = _resolve_file(candidate)
    if resolved_candidate is None or resolved_candidate.name.casefold() != "tg.ps1":
        return None
    candidate = resolved_candidate
    lines = _shim_lines(candidate)
    if lines is None:
        return None

    for managed_bin in _managed_bin_directories():
        if _same_path(candidate, managed_bin / "tg.ps1"):
            return _managed_shim_target(candidate, lines, managed_bin)

    target = _simple_delegate_target(lines, candidate)
    if target is None:
        return None
    if _same_path(target, candidate.parent / "tg.exe"):
        return _marked_bridge_target(target)
    for managed_bin in _managed_bin_directories():
        if _same_path(target, managed_bin / "tg.exe"):
            return _managed_target(target)
        if _same_path(target, managed_bin / "tg.ps1"):
            managed_shim = _resolve_file(target)
            if managed_shim is None:
                return None
            managed_lines = _shim_lines(managed_shim)
            if managed_lines is None:
                return None
            return _managed_shim_target(managed_shim, managed_lines, managed_bin)
    return None


def _file_identity(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


@contextlib.contextmanager
def verified_probe_target(
    target: Path,
) -> Iterator[tuple[str, tuple[int, ...], Callable[[], bool]] | None]:
    """Hold a read-only handle on ``target`` across spawn and verify identity did not change.

    On Windows, holding a ``FILE_SHARE_READ``-only handle locks both ``target`` and its
    ancestor directories against modification, rename, or deletion while ``capture_probe`` spawns.
    On Linux, binds execution to ``/proc/self/fd/<fd>`` via ``pass_fds``.
    """
    resolved = _resolve_file(target)
    if resolved is None:
        yield None
        return

    fd = _open_regular_nontraversing(resolved, share_read_only=True)
    if fd is None:
        yield None
        return
    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            yield None
            return
        expected_id = _file_identity(opened_stat)
        if sys.platform == "win32":
            launch_arg = str(resolved)
            pass_fds: tuple[int, ...] = ()
        else:
            fd_path = f"/proc/self/fd/{fd}" if sys.platform.startswith("linux") else f"/dev/fd/{fd}"
            if not os.path.exists(fd_path):
                yield None
                return
            launch_arg = fd_path
            pass_fds = (fd,)

        def _unchanged() -> bool:
            try:
                current_lstat = resolved.lstat()
                if (
                    getattr(current_lstat, "st_file_attributes", 0) or 0
                ) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    return False
                if _file_identity(current_lstat) != expected_id:
                    return False
                current_resolved = resolved.resolve(strict=True)
                return _lexical_path_key(current_resolved) == _lexical_path_key(resolved)
            except OSError:
                return False

        yield (launch_arg, pass_fds, _unchanged)
    except OSError:
        yield None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _lexical_shim_target(candidate: Path) -> Path | None:
    """Derive the candidate target path lexically without trusting authority yet."""
    if candidate.name.casefold() != "tg.ps1":
        return None
    is_managed = any(
        _same_path(candidate, managed_bin / "tg.ps1") for managed_bin in _managed_bin_directories()
    )
    is_compat = any(
        _same_path(candidate.parent, compatibility_dir)
        for compatibility_dir in _compatibility_directories()
    )
    if not (is_managed or is_compat):
        return None
    resolved_candidate = _resolve_file(candidate)
    if resolved_candidate is None or resolved_candidate.name.casefold() != "tg.ps1":
        return None
    lines = _shim_lines(resolved_candidate)
    if lines is None:
        return None
    for managed_bin in _managed_bin_directories():
        if _same_path(resolved_candidate, managed_bin / "tg.ps1"):
            return managed_bin / "tg.exe"
    target = _simple_delegate_target(lines, resolved_candidate)
    if target is None:
        return None
    for managed_bin in _managed_bin_directories():
        if _same_path(target, managed_bin / "tg.ps1"):
            return managed_bin / "tg.exe"
    return target


@contextlib.contextmanager
def verified_powershell_shim_probe(
    candidate: Path,
) -> Iterator[tuple[Path, str, tuple[int, ...], Callable[[], bool]] | None]:
    """Acquire the target handle first, then verify shim authority under that held lease."""
    lexical_target = _lexical_shim_target(candidate)
    if lexical_target is None:
        yield None
        return
    with verified_probe_target(lexical_target) as leased:
        if leased is None:
            yield None
            return
        launch_arg, pass_fds, verify_unchanged = leased
        authorized_target = powershell_shim_target(candidate)
        if authorized_target is None or not _same_path(authorized_target, lexical_target):
            yield None
            return
        if not verify_unchanged():
            yield None
            return
        yield (authorized_target, launch_arg, pass_fds, verify_unchanged)
