"""Identify generated codemap output directories for repository scan consumers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_COVERAGE_MARKER = "_coverage.json"
_COVERAGE_SCHEMA_VERSION = 1
_COVERAGE_REQUIRED_KEYS = frozenset({
    "out",
    "tree_manifest_sha256",
    "files_total",
    "folders_total",
    "symbols_total",
})
_MAX_MARKER_BYTES = 64 * 1024


def is_codemap_generated_dir(directory: Path) -> bool:
    """Return whether *directory* contains a valid codemap coverage marker."""
    marker = directory / _COVERAGE_MARKER
    try:
        if marker.is_symlink() or not marker.is_file():
            return False
        with marker.open("rb") as handle:
            raw = handle.read(_MAX_MARKER_BYTES + 1)
        if len(raw) > _MAX_MARKER_BYTES:
            return False
        payload: Any = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("schema_version") == _COVERAGE_SCHEMA_VERSION
        and _COVERAGE_REQUIRED_KEYS.issubset(payload)
        and isinstance(payload.get("out"), str)
        and isinstance(payload.get("tree_manifest_sha256"), str)
        and len(payload["tree_manifest_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in payload["tree_manifest_sha256"])
        and all(
            type(payload.get(key)) is int
            for key in ("files_total", "folders_total", "symbols_total")
        )
    )
