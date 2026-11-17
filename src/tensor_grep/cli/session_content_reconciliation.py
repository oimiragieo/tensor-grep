"""Bounded content receipts for metadata-backed sessions that lack trusted watcher events."""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from time import monotonic
from typing import Any

from tensor_grep.cli.symbols_cache_io import read_confined


def snapshot_input_paths(
    root: Path,
    related_paths: list[str],
    receipts: dict[str, str],
    max_extra_paths: int,
    unreadable_hit: Any = None,
) -> list[str]:
    """Include fixed-path inputs observed outside the repo-map file universe."""
    from tensor_grep.cli.symbols_cache import content_receipt_key

    paths = [str(path) for path in related_paths]
    seen = {content_receipt_key(path) for path in paths}
    root_key = Path(content_receipt_key(root))
    added = 0
    omitted = 0
    for path in receipts:
        key = content_receipt_key(path)
        if key not in seen:
            if added >= max(0, max_extra_paths):
                omitted += 1
                continue
            try:
                observed_path = root / Path(key).relative_to(root_key)
            except ValueError:
                observed_path = Path(path)
            paths.append(str(observed_path))
            seen.add(key)
            added += 1
    if omitted and unreadable_hit is not None:
        unreadable_hit.record(OSError("session snapshot receipt path budget exceeded"))
        unreadable_hit.count += omitted - 1
    return paths


def content_digest(root: Path, path: Path, limit: int, deadline: float | None = None) -> str:
    return content_digest_and_size(root, path, limit, deadline)[0]


def content_digest_and_size(
    root: Path,
    path: Path,
    limit: int,
    deadline: float | None = None,
) -> tuple[str, int]:
    """Hash bounded bytes and return their size from the same confined read."""
    if deadline is not None and monotonic() >= deadline:
        raise TimeoutError("session content reconciliation deadline exceeded")
    try:
        content = read_confined(root, path, limit)
    except ValueError as exc:
        raise OSError(f"session source outside captured root: {path}") from exc
    return hashlib.sha256(content).hexdigest(), len(content)


def capture_snapshot(
    paths: list[str],
    *,
    limit: int,
    root: Path | None = None,
    deadline: float | None = None,
    expected: dict[str, str] | None = None,
    unreadable_hit: Any = None,
) -> list[dict[str, Any]]:
    from tensor_grep.cli.symbols_cache import (
        CONTENT_ABSENT_RECEIPT,
        CONTENT_PRESENT_RECEIPT,
        content_receipt_key,
    )

    normalized_expected = (
        {content_receipt_key(path): digest for path, digest in expected.items()}
        if expected is not None
        else None
    )
    snapshots = []
    for position, raw_path in enumerate(paths):
        if deadline is not None and monotonic() >= deadline:
            if unreadable_hit is not None:
                unreadable_hit.record(TimeoutError("session snapshot capture deadline exceeded"))
                unreadable_hit.count += len(paths) - position - 1
            break
        path = Path(raw_path)
        try:
            path_key = content_receipt_key(path)
            expected_digest = (
                normalized_expected.get(path_key) if normalized_expected is not None else None
            )
            if expected_digest == CONTENT_ABSENT_RECEIPT:
                if root is not None:
                    try:
                        Path(path_key).relative_to(Path(content_receipt_key(root)))
                    except ValueError as exc:
                        raise OSError(f"session source outside captured root: {path}") from exc
                try:
                    absent_info = path.stat()
                except (FileNotFoundError, NotADirectoryError):
                    continue
                if stat.S_ISREG(absent_info.st_mode):
                    raise OSError(f"session source appeared since map generation: {path}")
                continue
            info = path.stat()
            digest, content_size = content_digest_and_size(
                root or path.parent,
                path,
                limit,
                deadline,
            )
            if normalized_expected is not None:
                if expected_digest not in (CONTENT_PRESENT_RECEIPT, digest):
                    raise OSError(f"session source changed since map generation: {path}")
        except OSError as exc:
            if unreadable_hit is not None:
                unreadable_hit.record(exc)
            continue
        snapshots.append({
            "path": str(path),
            "size": content_size,
            "mtime_ns": int(info.st_mtime_ns),
            "content_sha256": digest,
        })
    snapshots.sort(key=lambda item: str(item["path"]))
    return snapshots
