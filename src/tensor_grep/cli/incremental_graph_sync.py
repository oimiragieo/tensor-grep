"""Incremental content-hashed SQLite code graph sync and bounded ego-subgraph extraction."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

GRAPH_SYNC_REL_PATH = Path(".tg_cache") / "graph_v1" / "edges.sqlite3"


def _blake2b_hex(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def _open_graph_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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
    return conn


def sync_graph_edges(
    cache_root: Path,
    current_files: Mapping[str, bytes | str],
    extract_edges_fn: Callable[[str, str], list[tuple[str, str]]],
) -> dict[str, int]:
    """Incrementally sync file hashes and extracted code edges in SQLite WAL mode.

    Unchanged files (matching BLAKE2b content hash) are skipped in O(1).
    Dirty files have their edges replaced and deleted files are pruned within
    a single transaction.
    """
    db_path = Path(cache_root) / GRAPH_SYNC_REL_PATH
    conn = _open_graph_db(db_path)
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

    db_path = Path(cache_root) / GRAPH_SYNC_REL_PATH
    if not db_path.exists():
        return {
            "seed": seed_file,
            "nodes": [seed_file],
            "edges": [],
            "truncated": False,
        }

    conn = _open_graph_db(db_path)
    try:
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
            rows = conn.execute(
                "SELECT src, dst, kind FROM edges WHERE src = ? ORDER BY dst ASC, kind ASC",
                (current,),
            ).fetchall()
            for row_src, row_dst, row_kind in rows:
                src = str(row_src)
                dst = str(row_dst)
                kind = str(row_kind)
                if dst not in visited:
                    if len(ordered_nodes) >= max_nodes:
                        truncated = True
                        continue
                    visited.add(dst)
                    ordered_nodes.append(dst)
                    queue.append((dst, depth + 1))
                edge_key = (src, dst, kind)
                if dst in visited and edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    collected_edges.append({"src": src, "dst": dst, "kind": kind})

        return {
            "seed": seed_file,
            "nodes": ordered_nodes,
            "edges": collected_edges,
            "truncated": truncated,
        }
    finally:
        conn.close()
