"""Receipt-aware fixed-path input helpers used by repository evidence builders."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tensor_grep.cli.symbols_cache import (
    authenticated_content_root,
    read_content_bytes,
    record_content_absence,
    record_content_presence,
    record_content_receipt_digest,
)


def read_text_with_receipt(path: Path, *, encoding: str = "utf-8") -> str:
    """Match Path.read_text's newline behavior while receipting the exact input bytes."""
    return read_content_bytes(path).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def is_file_with_receipt(path: Path) -> bool:
    """Receipt file-presence decisions without turning optional probes into content reads."""
    try:
        present = path.is_file()
    except OSError:
        record_content_receipt_digest(path, "")
        return False
    if present:
        record_content_presence(path)
    else:
        record_content_absence(path)
    return present


def validation_repo_root(repo_root: str | Path) -> Path:
    """Find runner scope without allowing live markers above an authenticated scan root."""
    root = Path(repo_root).expanduser().resolve()
    if root.is_file():
        root = root.parent
    markers = (
        "package.json",
        "package-lock.json",
        "pnpm-workspace.yaml",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lockb",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Cargo.toml",
        "tsconfig.json",
    )
    boundary_markers = ("README.md", ".gitignore", "LICENSE", "AGENTS.md")
    boundary_candidate: Path | None = None
    authenticated_root = authenticated_content_root()
    current = root
    temp_root = Path(tempfile.gettempdir()).resolve()
    while True:
        if current == temp_root and root != temp_root:
            break
        if any(is_file_with_receipt(current / marker) for marker in markers):
            return current
        matched_boundaries = sum(
            1 for marker in boundary_markers if is_file_with_receipt(current / marker)
        )
        if authenticated_root is not None and current == authenticated_root:
            return current
        if (authenticated_root is None and (current / ".git").exists()) or matched_boundaries >= 2:
            boundary_candidate = current
        if current.parent == current:
            break
        next_current = current.parent
        if boundary_candidate is not None and next_current == boundary_candidate.parent:
            break
        current = next_current
    return boundary_candidate or root
