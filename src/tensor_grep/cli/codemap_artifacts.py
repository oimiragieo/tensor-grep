"""Bounded verification of the generated files recorded by a codemap stamp."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from tensor_grep.cli.codemap_pinned import PinnedDirectory
from tensor_grep.cli.diff_impact_git import BlobOverCap, PathChanged, UnreadablePath

MAX_STAMP_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PAGES = 50_000
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PAGE_NAME = re.compile(r"[A-Za-z0-9_.-]+\.md\Z")


class CodemapOutputError(ValueError):
    """Expected codemap destination or verification-limit refusal."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def add_size(text: str, total: int) -> int:
    size = len(text.encode("utf-8"))
    if size > MAX_ARTIFACT_BYTES or total + size > MAX_TOTAL_BYTES:
        raise CodemapOutputError("codemap output exceeds verification byte limits")
    return total + size


def read_stamp(out_dir: PinnedDirectory, name: str) -> bytes | None:
    try:
        return out_dir.read(name, MAX_STAMP_BYTES)
    except (OSError, PathChanged, UnreadablePath, BlobOverCap, ValueError):
        return None


def verify(
    coverage: dict[str, Any],
    out_dir: Path,
    index_path: Path,
    output: PinnedDirectory,
    index_parent: PinnedDirectory,
) -> bool:
    manifest = coverage.get("artifact_manifest")
    if not isinstance(manifest, dict) or set(manifest) != {"version", "index", "pages"}:
        return False
    if type(manifest["version"]) is not int or manifest["version"] != 1:
        return False
    index = manifest["index"]
    pages = manifest["pages"]
    if not isinstance(index, dict) or set(index) != {"path", "sha256"}:
        return False
    if index["path"] != str(index_path) or not isinstance(index["sha256"], str):
        return False
    if not _DIGEST.fullmatch(index["sha256"]):
        return False
    count = coverage.get("folders_total")
    if (
        not isinstance(pages, dict)
        or type(count) is not int
        or count != len(pages)
        or len(pages) > MAX_PAGES
    ):
        return False
    names: set[str] = set()
    for name, sha in pages.items():
        if not isinstance(name, str) or not _PAGE_NAME.fullmatch(name) or name in {".", ".."}:
            return False
        if not isinstance(sha, str) or not _DIGEST.fullmatch(sha) or name.casefold() in names:
            return False
        names.add(name.casefold())
    if index_path.parent == out_dir and index_path.name.casefold() in names:
        return False
    remaining = MAX_TOTAL_BYTES
    try:
        for root, name, sha in [
            (index_parent, index_path.name, index["sha256"]),
            *[(output, name, sha) for name, sha in pages.items()],
        ]:
            data = root.read(name, min(MAX_ARTIFACT_BYTES, remaining))
            remaining -= len(data)
            if hashlib.sha256(data).hexdigest() != sha:
                return False
    except (OSError, PathChanged, UnreadablePath, BlobOverCap, ValueError):
        return False
    return True
