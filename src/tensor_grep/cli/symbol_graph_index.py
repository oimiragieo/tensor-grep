"""Bounded SQLite graph edge index with recursive caller traversal."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from tensor_grep.cli.incremental_graph_sync import (
    extract_ego_subgraph,
    sync_graph_edges,
    sync_graph_edges_from_paths,
)
from tensor_grep.cli.repo_map_fast_cache import resolve_canonical_cache_root
from tensor_grep.cli.symbols_cache_io import (
    cache_lock,
    prepare_directory,
    publish_confined,
    read_confined,
)

_SCHEMA = 1
_DATABASE_LIMIT = 32 * 1024 * 1024
_DEFAULT_QUERY_LIMIT = 10000
_QUERY_DEADLINE_SECONDS = 0.25

GRAPH_CACHE_REL_PATH = Path(".tg_cache") / "graph_v1" / "edges.sqlite3"

STDLIB_BUILTIN_NOISE: frozenset[str] = frozenset(
    (
        "abs add all any append bool bytes clear close copy decode dict discard encode endswith "
        "enumerate Exception extend filter float format frozenset get getattr hasattr hash id "
        "insert int isinstance issubclass items iter join KeyError keys len list lower lstrip "
        "map max min next object open OSError pop print range read remove replace repr reversed "
        "round rstrip RuntimeError set setattr setdefault sorted split startswith str strip sum "
        "super tuple type TypeError update upper ValueError values write zip"
    ).split()
)

_EXPECTED_OBJECTS = [
    ("index", "idx_edges_dst"),
    ("index", "idx_edges_src"),
    ("table", "edges"),
    ("table", "metadata"),
]
_EXPECTED_EDGE_COLUMNS = [
    "src_file",
    "src_symbol",
    "dst_file",
    "dst_symbol",
    "edge_kind",
    "line",
    "provenance",
]
_EXPECTED_METADATA_COLUMNS = ["key", "value"]


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _DATABASE_LIMIT)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
    connection.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 0)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)


def _edge_row(
    edge: Mapping[str, Any] | tuple[str, str, str, str, str, int, str],
) -> tuple[str, str, str, str, str, int, str]:
    if isinstance(edge, tuple):
        if len(edge) != 7:
            raise ValueError("edge tuple must have 7 elements")
        src_file, src_symbol, dst_file, dst_symbol, edge_kind, line, provenance = edge
    else:
        src_file = edge["src_file"]
        src_symbol = edge["src_symbol"]
        dst_file = edge.get("dst_file", "")
        dst_symbol = edge["dst_symbol"]
        edge_kind = edge.get("edge_kind", "call")
        line = edge.get("line", 0)
        provenance = edge.get("provenance", "")
    if isinstance(line, bool) or not isinstance(line, int):
        raise ValueError(f"edge line must be an int, got {line!r}")
    return (
        str(src_file),
        str(src_symbol),
        str(dst_file),
        str(dst_symbol),
        str(edge_kind),
        line,
        str(provenance),
    )


class SymbolGraphIndex:
    """In-memory SQLite edge graph supporting bounded recursive caller queries and serialization."""

    def __init__(
        self,
        merkle_root: str = "",
        *,
        _connection: sqlite3.Connection | None = None,
    ) -> None:
        self.merkle_root = merkle_root
        if _connection is not None:
            self.connection = _connection
            return
        conn = sqlite3.connect(":memory:")
        _configure_connection(conn)
        try:
            conn.executescript("""
                CREATE TABLE edges (
                    src_file TEXT NOT NULL,
                    src_symbol TEXT NOT NULL,
                    dst_file TEXT NOT NULL,
                    dst_symbol TEXT NOT NULL,
                    edge_kind TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    provenance TEXT NOT NULL
                );
                CREATE INDEX idx_edges_dst ON edges(dst_symbol);
                CREATE INDEX idx_edges_src ON edges(src_file);
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                PRAGMA user_version = 1;
            """)
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES ('merkle_root', ?)",
                    (self.merkle_root,),
                )
        except BaseException:
            conn.close()
            raise
        self.connection = conn

    def __enter__(self) -> SymbolGraphIndex:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying in-memory SQLite connection."""
        self.connection.close()

    def set_merkle_root(self, merkle_root: str) -> None:
        """Update the stored merkle_root metadata inside the graph database."""
        self.merkle_root = merkle_root
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES ('merkle_root', ?)",
                (merkle_root,),
            )

    def insert_edges(
        self,
        edges: Iterable[Mapping[str, Any] | tuple[str, str, str, str, str, int, str]],
    ) -> int:
        """Insert non-noise edges into the graph and return the number of rows inserted."""
        rows: list[tuple[str, str, str, str, str, int, str]] = []
        for edge in edges:
            row = _edge_row(edge)
            if not row[1] or not row[3] or (not row[2] and row[3] in STDLIB_BUILTIN_NOISE):
                continue
            rows.append(row)
        if not rows:
            return 0
        with self.connection:
            self.connection.executemany(
                "INSERT INTO edges(src_file, src_symbol, dst_file, dst_symbol, edge_kind, line, provenance) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def recursive_callers(
        self,
        target_symbol: str,
        max_depth: int = 3,
        *,
        limit: int = _DEFAULT_QUERY_LIMIT,
    ) -> list[dict[str, Any]]:
        """Traverse callers of target_symbol up to max_depth hops with cycle detection."""
        if max_depth < 1 or limit < 1 or not target_symbol:
            return []
        cte_limit = max(limit * 4, 1000)
        end = time.monotonic() + _QUERY_DEADLINE_SECONDS
        self.connection.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
        try:
            rows = self.connection.execute(
                """
                WITH RECURSIVE callers(
                    src_file, src_symbol, dst_file, dst_symbol,
                    edge_kind, line, provenance, depth, visited
                ) AS (
                    SELECT
                        e.src_file,
                        e.src_symbol,
                        e.dst_file,
                        e.dst_symbol,
                        e.edge_kind,
                        e.line,
                        e.provenance,
                        1 AS depth,
                        char(31) || e.dst_file || char(31) || e.dst_symbol || char(31)
                            || e.src_file || char(31) || e.src_symbol || char(31) AS visited
                    FROM edges e
                    WHERE e.dst_symbol = ?
                    UNION ALL
                    SELECT
                        e.src_file,
                        e.src_symbol,
                        e.dst_file,
                        e.dst_symbol,
                        e.edge_kind,
                        e.line,
                        e.provenance,
                        c.depth + 1 AS depth,
                        c.visited || e.src_file || char(31) || e.src_symbol || char(31) AS visited
                    FROM edges e
                    JOIN callers c
                      ON e.dst_symbol = c.src_symbol
                     AND (e.dst_file = '' OR c.src_file = '' OR e.dst_file = c.src_file)
                    WHERE c.depth < ?
                      AND NOT instr(visited, char(31) || e.src_file || char(31) || e.src_symbol || char(31))
                    LIMIT ?
                )
                SELECT
                    src_file, src_symbol, dst_file, dst_symbol,
                    edge_kind, line, provenance, MIN(depth) AS depth
                FROM callers
                GROUP BY src_file, src_symbol, dst_file, dst_symbol, edge_kind, line, provenance
                ORDER BY depth ASC, src_file ASC, line ASC, src_symbol ASC
                LIMIT ?
                """,
                (target_symbol, max_depth, cte_limit, limit),
            ).fetchall()
        finally:
            self.connection.set_progress_handler(None, 0)
        return [
            {
                "src_file": row[0],
                "src_symbol": row[1],
                "dst_file": row[2],
                "dst_symbol": row[3],
                "edge_kind": row[4],
                "line": row[5],
                "provenance": row[6],
                "depth": row[7],
            }
            for row in rows
        ]

    def serialize(self) -> bytes:
        """Serialize the SQLite graph database to bounded bytes."""
        data = self.connection.serialize()
        if len(data) > _DATABASE_LIMIT:
            raise OSError("symbol graph exceeds bounded database size")
        return data

    @classmethod
    def deserialize(
        cls,
        data: bytes,
        *,
        expected_merkle_root: str | None = None,
        deadline: float | None = None,
    ) -> SymbolGraphIndex:
        """Deserialize and validate schema, integrity, and optional merkle_root."""
        if len(data) > _DATABASE_LIMIT:
            raise ValueError("symbol graph exceeds bounded database size")
        conn = sqlite3.connect(":memory:")
        _configure_connection(conn)
        end = time.monotonic() + _QUERY_DEADLINE_SECONDS
        if deadline is not None:
            end = min(end, deadline)
        conn.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
        try:
            conn.deserialize(data)
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise sqlite3.DatabaseError("symbol graph integrity check failed")
            if conn.execute("PRAGMA user_version").fetchone() != (_SCHEMA,):
                raise sqlite3.DatabaseError("symbol graph schema mismatch")
            objects = conn.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if sorted(objects) != _EXPECTED_OBJECTS:
                raise sqlite3.DatabaseError("symbol graph contains unexpected schema objects")
            if [row[1] for row in conn.execute("PRAGMA table_info(edges)")] != (
                _EXPECTED_EDGE_COLUMNS
            ):
                raise sqlite3.DatabaseError("symbol graph edges table contract mismatch")
            if [row[1] for row in conn.execute("PRAGMA table_info(metadata)")] != (
                _EXPECTED_METADATA_COLUMNS
            ):
                raise sqlite3.DatabaseError("symbol graph metadata table contract mismatch")
            row = conn.execute("SELECT value FROM metadata WHERE key = 'merkle_root'").fetchone()
            stored_merkle_root = str(row[0]) if row is not None else ""
            if expected_merkle_root is not None and stored_merkle_root != expected_merkle_root:
                raise ValueError("symbol graph merkle_root mismatch")
            conn.set_progress_handler(None, 0)
            return cls(merkle_root=stored_merkle_root, _connection=conn)
        except BaseException:
            conn.close()
            raise

    def save_to_cache(self, root: Path, *, deadline: float | None = None) -> Path:
        """Persist serialized graph database under .tg_cache/graph_v1/edges.sqlite3."""
        cache_root = resolve_canonical_cache_root(root)
        cache_path = cache_root / GRAPH_CACHE_REL_PATH
        prepare_directory(cache_root, cache_path.parent)
        ignore_path = cache_path.parent / ".gitignore"
        try:
            publish_confined(cache_root, ignore_path, b"*\n", only_if_missing=True)
        except FileExistsError:
            read_confined(cache_root, ignore_path, 65536)
        payload = self.serialize()
        with cache_lock(cache_root, cache_path, deadline):
            publish_confined(cache_root, cache_path, payload)
        return cache_path

    @classmethod
    def load_from_cache(
        cls,
        root: Path,
        *,
        expected_merkle_root: str,
        deadline: float | None = None,
    ) -> SymbolGraphIndex | None:
        """Load graph cache if present, valid, and matching expected_merkle_root."""
        if deadline is not None and time.monotonic() >= deadline:
            return None
        cache_root = resolve_canonical_cache_root(root)
        cache_path = cache_root / GRAPH_CACHE_REL_PATH
        if not cache_path.exists():
            return None
        try:
            data = read_confined(cache_root, cache_path, _DATABASE_LIMIT)
            return cls.deserialize(
                data,
                expected_merkle_root=expected_merkle_root,
                deadline=deadline,
            )
        except (OSError, ValueError, sqlite3.Error):
            return None


def sync_incremental_ego_subgraph(
    cache_root: Path,
    current_files: Mapping[str, bytes | str],
    extract_edges_fn: Callable[[str, str], list[tuple[str, str]]],
    seed_file: str,
    *,
    max_hops: int = 2,
    max_nodes: int = 32,
) -> dict[str, Any]:
    """Incrementally sync file edges and extract a bounded ego subgraph around ``seed_file``."""
    scope_root = Path(cache_root).expanduser().resolve()
    canonical_root = resolve_canonical_cache_root(scope_root)
    sync_stats = sync_graph_edges(
        canonical_root,
        current_files,
        extract_edges_fn,
        scope_root=scope_root,
    )
    subgraph = extract_ego_subgraph(
        canonical_root,
        seed_file,
        max_hops=max_hops,
        max_nodes=max_nodes,
    )
    return {**subgraph, "sync": sync_stats}


def sync_incremental_ego_subgraph_from_paths(
    cache_root: Path,
    file_paths: Iterable[str | Path],
    extract_edges_fn: Callable[[str, str], list[tuple[str, str]]],
    seed_file: str,
    *,
    max_hops: int = 2,
    max_nodes: int = 32,
    deadline_monotonic: float | None = None,
    force_refresh: bool = False,
    context_digest: str | None = None,
) -> dict[str, Any]:
    """Incrementally sync file edges via stat+hash reconciliation and extract an ego subgraph."""
    scope_root = Path(cache_root).expanduser().resolve()
    canonical_root = resolve_canonical_cache_root(scope_root)
    sync_stats = sync_graph_edges_from_paths(
        canonical_root,
        file_paths,
        extract_edges_fn,
        deadline_monotonic=deadline_monotonic,
        force_refresh=force_refresh,
        context_digest=context_digest,
        scope_root=scope_root,
    )
    subgraph = extract_ego_subgraph(
        canonical_root,
        seed_file,
        max_hops=max_hops,
        max_nodes=max_nodes,
    )
    return {**subgraph, "sync": sync_stats}
