"""Safe static ownership checks for launcher candidates reported by ``tg doctor``."""

from __future__ import annotations

import re
from pathlib import Path


def powershell_shim_target(candidate: Path) -> Path | None:
    """Return the sole owned tg executable delegated to by a simple PowerShell shim."""
    try:
        with candidate.open("rb") as stream:
            contents = stream.read(4097)
        if len(contents) > 4096:
            return None
        lines = [line.strip() for line in contents.decode("utf-8-sig").splitlines() if line.strip()]
    except (OSError, UnicodeError):
        return None
    if len(lines) not in {1, 2}:
        return None
    if len(lines) == 2 and lines[1].casefold() != "exit $lastexitcode":
        return None
    command = re.fullmatch(
        r"&\s+(?P<target>'[^'\r\n]+'|\"[^\"\r\n]+\"|\$PSScriptRoot[\\/][^\s;|&]+)\s+@args",
        lines[0],
        re.IGNORECASE,
    )
    if command is None:
        return None
    raw_target = command.group("target")
    if raw_target[:1] in {"'", '"'}:
        raw_target = raw_target[1:-1]
    script_root = "$PSScriptRoot"
    if raw_target.casefold().startswith(script_root.casefold()):
        relative = Path(raw_target[len(script_root) :].lstrip("\\/"))
        if not relative.parts or ".." in relative.parts:
            return None
        target = candidate.parent / relative
    else:
        target = Path(raw_target)
        if not target.is_absolute():
            return None
    try:
        target = target.resolve(strict=True)
    except OSError:
        return None
    if not target.is_file() or target.name.casefold() not in {"tg.exe", "tg.com"}:
        return None
    return target
