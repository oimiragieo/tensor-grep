"""Incremental content-hashed SQLite code graph sync and bounded ego-subgraph extraction."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tensor_grep.io.confined import (
    prepare_directory,
    publish_confined,
    read_confined,
    validate_confined_parent_prefix,
)

GRAPH_SYNC_REL_PATH = Path(".tg_cache") / "graph_v1" / "incremental_edges.sqlite3"
_GRAPH_DB_LIMIT = 64 * 1024 * 1024


def _blake2b_hex(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def _open_graph_db(cache_root: Path, *, create_dirs: bool) -> tuple[sqlite3.Connection, Path]:
    resolved_root = Path(cache_root).resolve()
    db_path = resolved_root / GRAPH_SYNC_REL_PATH
    if create_dirs:
        prepare_directory(resolved_root, db_path.parent)
        gitignore_path = resolved_root / ".tg_cache" / ".gitignore"
        if not gitignore_path.exists():
            publish_confined(resolved_root, gitignore_path, b"*\n", only_if_missing=True)
    else:
        validate_confined_parent_prefix(resolved_root, db_path)
    conn = sqlite3.connect(":memory:")
    try:
        if db_path.exists():
            conn.deserialize(read_confined(resolved_root, db_path, _GRAPH_DB_LIMIT))
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS edges (
                    src TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (src, dst, kind)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON edges(src)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON edges(dst)")
    except BaseException:
        conn.close()
        raise
    return conn, db_path


def sync_graph_edges(
    cache_root: Path,
    current_files: Mapping[str, bytes | str],
    extract_edges_fn: Callable[[str, str], list[tuple[str, str]]],
) -> dict[str, int]:
    """Incrementally sync file hashes and extracted code edges in confined SQLite storage."""
    resolved_root = Path(cache_root).resolve()
    conn, db_path = _open_graph_db(resolved_root, create_dirs=True)
    try:
        existing_hashes = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT path, content_hash FROM files").fetchall()
        }

        skipped = 0
        updated = 0
        deleted_paths = sorted(path for path in existing_hashes if path not in current_files)
        dirty_entries: list[tuple[str, str, list[tuple[str, str]]]] = []

        for raw_path, raw_content in current_files.items():
            path = str(raw_path)
            if isinstance(raw_content, bytes):
                content_bytes = raw_content
                content_text = raw_content.decode("utf-8", errors="replace")
            else:
                content_text = str(raw_content)
                content_bytes = content_text.encode("utf-8")

            digest = _blake2b_hex(content_bytes)
            if existing_hashes.get(path) == digest:
                skipped += 1
                continue

            extracted = extract_edges_fn(path, content_text)
            dirty_entries.append((path, digest, list(extracted)))
            updated += 1

        now = time.time()
        with conn:
            for stale_path in deleted_paths:
                conn.execute("DELETE FROM edges WHERE src = ?", (stale_path,))
                conn.execute("DELETE FROM files WHERE path = ?", (stale_path,))

            for path, digest, extracted in dirty_entries:
                conn.execute("DELETE FROM edges WHERE src = ?", (path,))
                if extracted:
                    conn.executemany(
                        "INSERT OR IGNORE INTO edges (src, dst, kind) VALUES (?, ?, ?)",
                        [(path, str(dst), str(kind)) for dst, kind in extracted],
                    )
                conn.execute(
                    """
                    INSERT INTO files (path, content_hash, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at
                    """,
                    (path, digest, now),
                )

        if dirty_entries or deleted_paths or not db_path.exists():
            publish_confined(resolved_root, db_path, conn.serialize())
        return {
            "skipped": skipped,
            "updated": updated,
            "deleted": len(deleted_paths),
        }
    finally:
        conn.close()


def extract_ego_subgraph(
    cache_root: Path,
    seed_file: str,
    *,
    max_hops: int = 2,
    max_nodes: int = 32,
) -> dict[str, Any]:
    """Extract a bounded k-hop ego subgraph around ``seed_file`` via BFS."""
    if max_hops < 0:
        raise ValueError(f"max_hops must be non-negative, got {max_hops}")
    if max_nodes <= 0:
        return {
            "seed": seed_file,
            "nodes": [],
            "edges": [],
            "truncated": True,
        }

    resolved_root = Path(cache_root).resolve()
    conn, db_path = _open_graph_db(resolved_root, create_dirs=False)
    try:
        if not db_path.exists():
            return {
                "seed": seed_file,
                "nodes": [seed_file],
                "edges": [],
                "truncated": False,
            }
        visited: set[str] = {seed_file}
        ordered_nodes: list[str] = [seed_file]
        collected_edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()
        queue: deque[tuple[str, int]] = deque([(seed_file, 0)])
        truncated = False

        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            dst_rows = conn.execute(
                "SELECT DISTINCT dst FROM edges WHERE src = ? ORDER BY dst ASC LIMIT ?",
                (current, max_nodes + 1),
            ).fetchall()
            kept_dsts: list[str] = []
            for (row_dst,) in dst_rows:
                dst = str(row_dst)
                if dst not in visited:
                    if len(ordered_nodes) >= max_nodes:
                        truncated = True
                        continue
                    visited.add(dst)
                    ordered_nodes.append(dst)
                    queue.append((dst, depth + 1))
                kept_dsts.append(dst)
            for dst in kept_dsts:
                kind_rows = conn.execute(
                    "SELECT kind FROM edges WHERE src = ? AND dst = ? ORDER BY kind ASC",
                    (current, dst),
                ).fetchall()
                for (row_kind,) in kind_rows:
                    edge_key = (current, dst, str(row_kind))
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        collected_edges.append({"src": current, "dst": dst, "kind": str(row_kind)})

        return {
            "seed": seed_file,
            "nodes": ordered_nodes,
            "edges": collected_edges,
            "truncated": truncated,
        }
    finally:
        conn.close()
