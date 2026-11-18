"""Receipt-aware fixed-path input helpers used by repository evidence builders."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Collection
from pathlib import Path

from tensor_grep.cli.symbols_cache import (
    authenticated_content_root,
    authenticated_content_size,
    authenticated_content_size_paths,
    authenticated_content_sizes_active,
    authenticated_scan_paths,
    read_content_bytes,
    record_content_absence,
    record_content_presence,
    record_content_receipt_digest,
)


def authenticated_scan_paths_under(
    root: Path, exclude_files: Collection[Path] = ()
) -> list[Path] | None:
    """Return the signed scan universe in a lexical subtree without touching the FS."""
    scan_paths = authenticated_scan_paths()
    if scan_paths is None:
        return None
    root_key = Path(os.path.normcase(os.path.abspath(root)))
    excluded = {os.path.normcase(os.path.abspath(path)) for path in exclude_files}
    selected: list[Path] = []
    for scan_path in scan_paths:
        scan_key = os.path.normcase(os.path.abspath(scan_path))
        try:
            Path(scan_key).relative_to(root_key)
        except ValueError:
            continue
        if scan_key not in excluded:
            selected.append(Path(scan_path))
    return selected


def glob_dirs_with_receipt(root: Path, pattern: str) -> list[Path]:
    """Expand directory globs from signed scan-path parents during authenticated prepare."""
    scan_paths = authenticated_scan_paths()
    if scan_paths is None:
        return [candidate for candidate in root.glob(pattern) if candidate.is_dir()]
    root_key = Path(os.path.normcase(os.path.abspath(root)))
    matched: dict[str, Path] = {}
    for scan_path in scan_paths:
        current = Path(scan_path).parent
        while True:
            try:
                relative = Path(os.path.normcase(os.path.abspath(current))).relative_to(root_key)
            except ValueError:
                break
            if relative.match(pattern):
                matched.setdefault(os.path.normcase(os.path.abspath(current)), current)
            if not relative.parts:
                break
            current = current.parent
    return list(matched.values())


def is_dir_with_receipt(path: Path) -> bool:
    """Derive directory existence from signed descendants during authenticated prepare."""
    signed_paths = authenticated_content_size_paths()
    if signed_paths is None:
        return path.is_dir()
    key = os.path.normcase(os.path.abspath(path))
    authenticated_root = authenticated_content_root()
    if authenticated_root is not None:
        if key == os.path.normcase(os.path.abspath(authenticated_root)):
            return True
    prefix = key.rstrip("\\/") + os.sep
    return any(current.startswith(prefix) for current in signed_paths)


def path_exists_with_receipt(path: Path) -> bool:
    """Derive file/directory existence from the signed prepare snapshot when available."""
    signed_paths = authenticated_content_size_paths()
    if signed_paths is None:
        return path.exists()
    key = os.path.normcase(os.path.abspath(path))
    return key in signed_paths or is_dir_with_receipt(path)


def resolved_input_path(path: str | Path) -> Path:
    """Normalize signed inputs lexically so transient topology cannot redirect prepare reads."""
    expanded = Path(path).expanduser()
    if authenticated_content_sizes_active() or authenticated_scan_paths() is not None:
        return Path(os.path.abspath(expanded))
    return expanded.resolve()


def file_size_with_receipt(path: Path) -> int:
    """Use a signed snapshot size during prepare without reading an over-cap file."""
    if authenticated_content_sizes_active():
        signed_size = authenticated_content_size(path)
        if signed_size is not None:
            return signed_size
        record_content_receipt_digest(path, "")
    return path.stat().st_size


def read_text_with_receipt(path: Path, *, encoding: str = "utf-8") -> str:
    """Match Path.read_text's newline behavior while receipting the exact input bytes."""
    return read_content_bytes(path).decode(encoding).replace("\r\n", "\n").replace("\r", "\n")


def is_file_with_receipt(path: Path, *, raise_on_error: bool = False) -> bool:
    """Receipt file-presence decisions without turning optional probes into content reads."""
    try:
        present = path.is_file()
    except OSError:
        record_content_receipt_digest(path, "")
        if raise_on_error:
            raise
        return False
    if present:
        record_content_presence(path)
    else:
        record_content_absence(path)
    return present


def validation_repo_root(repo_root: str | Path) -> Path:
    """Find runner scope without allowing live markers above an authenticated scan root."""
    root = resolved_input_path(repo_root)
    if not is_dir_with_receipt(root):
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
    temp_root = resolved_input_path(tempfile.gettempdir())
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
