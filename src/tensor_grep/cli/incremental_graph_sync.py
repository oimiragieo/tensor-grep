"""Incremental content-hashed SQLite code graph sync and bounded ego-subgraph extraction."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
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
                CREATE TABLE IF NOT EXISTS file_stats (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL
                )
                """
            )
            stat_cols = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(file_stats)").fetchall()
            }
            if "ctime_ns" not in stat_cols:
                conn.execute(
                    "ALTER TABLE file_stats ADD COLUMN ctime_ns INTEGER NOT NULL DEFAULT 0"
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
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
    *,
    scope_root: Path | None = None,
) -> dict[str, int]:
    """Incrementally sync file hashes and extracted code edges in confined SQLite storage."""
    resolved_root = Path(cache_root).resolve()
    resolved_scope = Path(scope_root).expanduser().resolve() if scope_root is not None else None
    conn, db_path = _open_graph_db(resolved_root, create_dirs=True)
    try:
        existing_hashes = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT path, content_hash FROM files").fetchall()
        }

        skipped = 0
        updated = 0
        deleted_paths = sorted(
            path
            for path in existing_hashes
            if path not in current_files
            and (
                resolved_scope is None
                or (Path(path) if Path(path).is_absolute() else resolved_root / path)
                .resolve()
                .is_relative_to(resolved_scope)
            )
        )
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
        stat_upserts: list[tuple[str, int, int, int]] = []
        stat_deletes: list[str] = list(deleted_paths)
        for raw_path in current_files:
            path_str = str(raw_path)
            fp = Path(path_str)
            target_fp = fp if fp.is_absolute() else resolved_root / fp
            stat_key = _lstat_regular(target_fp)
            if stat_key is not None:
                stat_upserts.append((path_str, stat_key[0], stat_key[1], stat_key[2]))
            else:
                stat_deletes.append(path_str)

        with conn:
            for stale_path in deleted_paths:
                conn.execute("DELETE FROM edges WHERE src = ?", (stale_path,))
                conn.execute("DELETE FROM files WHERE path = ?", (stale_path,))
            for stale_stat_path in stat_deletes:
                conn.execute("DELETE FROM file_stats WHERE path = ?", (stale_stat_path,))

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

            if stat_upserts:
                conn.executemany(
                    """
                    INSERT INTO file_stats (path, mtime_ns, ctime_ns, size)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns = excluded.mtime_ns,
                        ctime_ns = excluded.ctime_ns,
                        size = excluded.size
                    """,
                    stat_upserts,
                )

        if dirty_entries or deleted_paths or stat_upserts or not db_path.exists():
            publish_confined(resolved_root, db_path, conn.serialize())
        return {
            "skipped": skipped,
            "updated": updated,
            "deleted": len(deleted_paths),
        }
    finally:
        conn.close()


_EMPTY_DIGEST = _blake2b_hex(b"")


def _lstat_regular(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400):
        return None
    return (int(st.st_mtime_ns), int(st.st_ctime_ns), int(st.st_size))


def sync_graph_edges_from_paths(
    cache_root: Path,
    file_paths: Iterable[str | Path],
    extract_edges_fn: Callable[[str, str], list[tuple[str, str]]],
    *,
    deadline_monotonic: float | None = None,
    force_refresh: bool = False,
    context_digest: str | None = None,
    scope_root: Path | None = None,
) -> dict[str, int]:
    """Incrementally sync file edges using ``(mtime_ns, ctime_ns, size)`` stat-first reconciliation.

    Stat-first freshness contract:
    A file is skipped without reading or hashing when its ``(mtime_ns, ctime_ns, size)`` tuple
    matches ``file_stats`` and it already has a ``files.content_hash`` entry. Including
    ``ctime_ns`` detects NTFS/POSIX content writes where ``mtime_ns`` was restored to its
    previous value. Whenever any stat field differs, confined bounded content is read and
    hashed; unchanged hashes update ``file_stats`` only, while changed hashes re-extract edges.
    """
    from tensor_grep.cli.repo_map import _max_parse_bytes

    resolved_root = Path(cache_root).resolve()
    max_bytes = _max_parse_bytes()
    conn, db_path = _open_graph_db(resolved_root, create_dirs=True)
    try:
        existing_hashes = {
            str(row[0]): str(row[1])
            for row in conn.execute("SELECT path, content_hash FROM files").fetchall()
        }
        existing_stats = {
            str(row[0]): (int(row[1]), int(row[2]), int(row[3]))
            for row in conn.execute(
                "SELECT path, mtime_ns, ctime_ns, size FROM file_stats"
            ).fetchall()
        }
        context_digest_changed = False
        if context_digest is not None:
            row = conn.execute("SELECT value FROM metadata WHERE key = 'context_digest'").fetchone()
            stored_context_digest = str(row[0]) if row is not None else None
            if stored_context_digest != context_digest:
                force_refresh = True
                context_digest_changed = True

        current_path_strs = [str(p) for p in file_paths]
        current_path_set = set(current_path_strs)
        resolved_scope = Path(scope_root).expanduser().resolve() if scope_root is not None else None
        deleted_paths = sorted(
            p
            for p in existing_hashes
            if p not in current_path_set
            and (
                resolved_scope is None
                or (Path(p) if Path(p).is_absolute() else resolved_root / p)
                .resolve()
                .is_relative_to(resolved_scope)
            )
        )

        skipped = 0
        updated = 0
        incomplete_oversized = 0
        incomplete_unreadable = 0
        deadline_hit = False
        oversized_stale_paths: list[str] = []
        unreadable_stale_paths: list[str] = []
        dirty_entries: list[tuple[str, str, list[tuple[str, str]]]] = []
        stat_updates: list[tuple[str, int, int, int]] = []

        for path_str in current_path_strs:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                deadline_hit = True
                break
            fp = Path(path_str)
            target_fp = fp if fp.is_absolute() else resolved_root / fp
            stat_key = _lstat_regular(target_fp)
            if stat_key is not None:
                if (
                    not force_refresh
                    and existing_stats.get(path_str) == stat_key
                    and path_str in existing_hashes
                ):
                    skipped += 1
                    continue
                if stat_key[2] > max_bytes:
                    incomplete_oversized += 1
                    if path_str in existing_hashes or path_str in existing_stats:
                        oversized_stale_paths.append(path_str)
                    continue
                try:
                    content_bytes = read_confined(resolved_root, target_fp, max_bytes)
                except OSError:
                    incomplete_unreadable += 1
                    if path_str in existing_hashes or path_str in existing_stats:
                        unreadable_stale_paths.append(path_str)
                    continue
            else:
                if (
                    not force_refresh
                    and existing_stats.get(path_str) == (-1, -1, -1)
                    and existing_hashes.get(path_str) == _EMPTY_DIGEST
                ):
                    skipped += 1
                    continue
                content_bytes = b""
                stat_key = (-1, -1, -1)

            digest = _blake2b_hex(content_bytes)
            if not force_refresh and existing_hashes.get(path_str) == digest:
                stat_updates.append((path_str, stat_key[0], stat_key[1], stat_key[2]))
                skipped += 1
                continue

            content_text = content_bytes.decode("utf-8", errors="replace")
            extracted = extract_edges_fn(path_str, content_text)
            dirty_entries.append((path_str, digest, list(extracted)))
            stat_updates.append((path_str, stat_key[0], stat_key[1], stat_key[2]))
            updated += 1

        commit_context_digest = (
            context_digest_changed
            and context_digest is not None
            and not deadline_hit
            and incomplete_unreadable == 0
        )
        now = time.time()
        with conn:
            for stale_path in (
                *deleted_paths,
                *oversized_stale_paths,
                *unreadable_stale_paths,
            ):
                conn.execute("DELETE FROM edges WHERE src = ?", (stale_path,))
                conn.execute("DELETE FROM files WHERE path = ?", (stale_path,))
                conn.execute("DELETE FROM file_stats WHERE path = ?", (stale_path,))

            for path_str, digest, extracted in dirty_entries:
                conn.execute("DELETE FROM edges WHERE src = ?", (path_str,))
                if extracted:
                    conn.executemany(
                        "INSERT OR IGNORE INTO edges (src, dst, kind) VALUES (?, ?, ?)",
                        [(path_str, str(dst), str(kind)) for dst, kind in extracted],
                    )
                conn.execute(
                    """
                    INSERT INTO files (path, content_hash, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at
                    """,
                    (path_str, digest, now),
                )

            if stat_updates:
                conn.executemany(
                    """
                    INSERT INTO file_stats (path, mtime_ns, ctime_ns, size)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        mtime_ns = excluded.mtime_ns,
                        ctime_ns = excluded.ctime_ns,
                        size = excluded.size
                    """,
                    stat_updates,
                )

            if commit_context_digest:
                conn.execute(
                    """
                    INSERT INTO metadata (key, value)
                    VALUES ('context_digest', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (context_digest,),
                )

        if (
            dirty_entries
            or deleted_paths
            or oversized_stale_paths
            or unreadable_stale_paths
            or stat_updates
            or commit_context_digest
            or not db_path.exists()
        ):
            publish_confined(resolved_root, db_path, conn.serialize())
        complete = (
            0 if (deadline_hit or incomplete_oversized > 0 or incomplete_unreadable > 0) else 1
        )
        return {
            "skipped": skipped,
            "updated": updated,
            "deleted": len(deleted_paths),
            "incomplete_oversized": incomplete_oversized,
            "incomplete_unreadable": incomplete_unreadable,
            "complete": complete,
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
