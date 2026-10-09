"""Bounded content receipts for metadata-backed sessions that lack trusted watcher events."""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic
from typing import Any

from tensor_grep.cli.symbols_cache_io import read_confined


def content_digest(root: Path, path: Path, limit: int, deadline: float | None = None) -> str:
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("session content reconciliation deadline exceeded")
    try:
        content = read_confined(root, path, limit)
    except ValueError as exc:
        raise OSError(f"session source outside captured root: {path}") from exc
    return hashlib.sha256(content).hexdigest()


def capture_snapshot(
    paths: list[str],
    *,
    limit: int,
    root: Path | None = None,
    deadline: float | None = None,
    expected: dict[str, str] | None = None,
    unreadable_hit: Any = None,
) -> list[dict[str, Any]]:
    snapshots = []
    for position, raw_path in enumerate(paths):
        if deadline is not None and monotonic() >= deadline:
            if unreadable_hit is not None:
                unreadable_hit.record(TimeoutError("session snapshot capture deadline exceeded"))
                unreadable_hit.count += len(paths) - position - 1
            break
        path = Path(raw_path)
        try:
            info = path.stat()
            digest = content_digest(root or path.parent, path, limit, deadline)
            if expected is not None and expected.get(str(path)) != digest:
                raise OSError(f"session source changed since map generation: {path}")
        except OSError as exc:
            if unreadable_hit is not None:
                unreadable_hit.record(exc)
            continue
        snapshots.append({
            "path": str(path),
            "size": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
            "content_sha256": digest,
        })
    snapshots.sort(key=lambda item: str(item["path"]))
    return snapshots
