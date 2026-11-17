"""Safe static ownership checks for launcher candidates reported by ``tg doctor``."""

from __future__ import annotations

import json
import os
import re
import stat
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


def _bounded_bytes(path: Path, limit: int) -> bytes | None:
    try:
        metadata = path.lstat()
        if getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return None
        with path.open("rb") as stream:
            contents = stream.read(limit + 1)
    except OSError:
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
    return _path_key(left) == _path_key(right)


def _home_directories() -> list[Path]:
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
    except (UnicodeError, ValueError):
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
    try:
        left_size = left.stat().st_size
        right_size = right.stat().st_size
        if left_size != right_size or left_size > _MAX_NATIVE_BYTES:
            return False
        with left.open("rb") as left_stream, right.open("rb") as right_stream:
            while True:
                left_chunk = left_stream.read(1024 * 1024)
                right_chunk = right_stream.read(1024 * 1024)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


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

    if not any(
        _same_path(candidate.parent, compatibility_dir)
        for compatibility_dir in _compatibility_directories()
    ):
        return None
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
