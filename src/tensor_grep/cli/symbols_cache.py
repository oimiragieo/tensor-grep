"""Content-addressed whole-file AST products with transactional generation metadata.

SQLite runs in memory; bounded bytes are published atomically beneath the selected
checkout. No SQLite journal can follow a caller-controlled filename. Filesystem
events currently have no sequence/freshness receipt, so every build reconciles
bounded source contents, including non-Git and untracked files.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from tensor_grep.cli import lang_registry
from tensor_grep.cli.repo_map_cache import promote_verified_snapshot
from tensor_grep.cli.symbols_cache_auth import load_machine_key
from tensor_grep.cli.symbols_cache_io import (
    cache_lock,
    prepare_directory,
    publish_confined,
    read_confined,
)

_SCHEMA = 1
_DATABASE_LIMIT = 32 * 1024 * 1024
_ENTRY_LIMIT = 2 * 1024 * 1024
_MAX_ENTRIES = 8192
Product = tuple[list[str], list[dict[str, Any]]]
_CONTENT_RECEIPTS: ContextVar[dict[str, str] | None] = ContextVar(
    "tg_content_receipts", default=None
)


@contextmanager
def collect_content_receipts() -> Iterator[dict[str, str]]:
    """Let session capture bind metadata to the exact contents used by a map build."""
    receipts: dict[str, str] = {}
    token = _CONTENT_RECEIPTS.set(receipts)
    try:
        yield receipts
    finally:
        _CONTENT_RECEIPTS.reset(token)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _database(
    root: Path, path: Path, *, rebuild: bool = False, deadline: float | None = None
) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _ENTRY_LIMIT + 65536)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
    connection.setlimit(sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 0)
    connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
    end = time.monotonic() + 0.25
    if deadline is not None:
        end = min(end, deadline)
    connection.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
    try:
        if not rebuild and path.exists():
            connection.deserialize(read_confined(root, path, _DATABASE_LIMIT))
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise sqlite3.DatabaseError("symbol cache integrity check failed")
            if connection.execute("PRAGMA user_version").fetchone() != (_SCHEMA,):
                raise sqlite3.DatabaseError("symbol cache schema mismatch")
            objects = connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if sorted(objects) != [
                ("table", "entries"),
                ("table", "files"),
                ("table", "generations"),
            ]:
                raise sqlite3.DatabaseError("symbol cache contains unexpected schema objects")
            expected = {
                "entries": ["key", "payload", "receipt", "generation"],
                "files": ["scope", "path", "content", "entry_key", "generation"],
                "generations": ["id", "scope", "merkle", "complete", "created"],
            }
            for table, columns in expected.items():
                if [row[1] for row in connection.execute(f"PRAGMA table_info({table})")] != columns:
                    raise sqlite3.DatabaseError("symbol cache table contract mismatch")
        else:
            connection.executescript("""
                CREATE TABLE entries (key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    receipt TEXT NOT NULL, generation INTEGER NOT NULL);
                CREATE TABLE generations (id INTEGER PRIMARY KEY, scope TEXT NOT NULL,
                    merkle TEXT NOT NULL, complete INTEGER NOT NULL, created REAL NOT NULL);
                CREATE TABLE files (scope TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
                    entry_key TEXT NOT NULL, generation INTEGER NOT NULL,
                    PRIMARY KEY(scope,path));
                PRAGMA user_version=1;
            """)
        connection.set_progress_handler(None, 0)
        return connection
    except BaseException:
        connection.close()
        raise


def _receipt(secret: bytes, key: str, payload: str) -> str:
    return hmac.new(
        secret, b"tg-symbols-v1\0" + key.encode() + b"\0" + payload.encode(), hashlib.sha256
    ).hexdigest()


def _decode_product(payload: str, receipt: str, path: Path, secret: bytes, key: str) -> Product:
    if len(payload) > _ENTRY_LIMIT or not hmac.compare_digest(
        _receipt(secret, key, payload), receipt
    ):
        raise ValueError("symbol cache entry authentication mismatch")
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {"imports", "symbols"}:
        raise ValueError("invalid symbol cache entry")
    imports, symbols = data["imports"], data["symbols"]
    if not isinstance(imports, list) or not all(isinstance(item, str) for item in imports):
        raise ValueError("invalid symbol cache imports")
    if not isinstance(symbols, list):
        raise ValueError("invalid symbol cache symbols")
    restored = []
    for symbol in symbols:
        if (
            not isinstance(symbol, dict)
            or not all(isinstance(symbol.get(field), str) for field in ("name", "kind"))
            or not all(
                isinstance(symbol.get(field), int) and symbol[field] > 0
                for field in ("line", "start_line", "end_line")
            )
        ):
            raise ValueError("invalid symbol cache symbol record")
        restored.append({**symbol, "file": str(path)})
    return imports, restored


class SymbolGeneration:
    """One bounded reconciliation; generations never authorize reuse by mtime alone."""

    def __init__(
        self,
        root: Path,
        *,
        scope: Path,
        max_bytes: int,
        ignore_policy: list[str],
        deadline: float | None,
    ) -> None:
        self.root = root
        self.path = root / ".tg_cache" / "symbols_v1" / "metadata.sqlite3"
        self.scope = str(scope)
        self.max_bytes = max_bytes
        self.ignore_policy = ignore_policy
        self.deadline = deadline
        self.connection: sqlite3.Connection | None = None
        self.entries: dict[str, tuple[str, str]] = {}
        self.files: dict[str, tuple[str, str]] = {}
        self.failures: list[dict[str, str]] = []
        self.hits = 0
        self.misses = 0
        self.bytes_read = 0
        self.unverified_final_files = 0
        self.cache_status = "ready"
        self.signing_key: bytes | None = None
        self.versions: dict[str, str] = {"python": sys.version.split()[0]}
        if deadline is not None and time.monotonic() >= deadline:
            self.cache_status = "not-persisted: deadline exceeded"
            return
        self.signing_key = load_machine_key(root)
        if self.signing_key is None:
            self.cache_status = "not-persisted: machine signing key unavailable"
            return
        try:
            self.connection = _database(root, self.path, deadline=deadline)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.cache_status = f"rebuild-required: {exc}"
        for spec in lang_registry.LANGUAGE_REGISTRY.values():
            for module in spec.grammar_modules:
                try:
                    self.versions[module] = importlib.metadata.version(module.replace("_", "-"))
                except importlib.metadata.PackageNotFoundError:
                    self.versions[module] = "unavailable"

    def _options(self, path: Path) -> dict[str, Any]:
        ignores = []
        directory = path.parent
        while True:
            ignore_file = directory / ".gitignore"
            if ignore_file.exists():
                ignores.append((
                    str(ignore_file.relative_to(self.root)),
                    _digest(read_confined(self.root, ignore_file, 128 * 1024)),
                ))
            if directory == self.root:
                break
            directory = directory.parent
        spec = lang_registry.spec_for_path(path)
        available = (
            spec.parser_for_path(path) is not None
            if spec is not None and spec.parser_for_path is not None
            else True
        )
        return {
            "schema": _SCHEMA,
            "parser_versions": self.versions,
            "scope": self.scope,
            "suffix": path.suffix.lower(),
            "max_parse_bytes": self.max_bytes,
            "ignore_policy": self.ignore_policy,
            "ignore_configuration": ignores,
            "parser_available": available,
        }

    def product(self, path: Path, producer: Callable[[], Product]) -> Product:
        try:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise OSError("reconciliation deadline exceeded")
            content = read_confined(self.root, path, self.max_bytes)
            self.bytes_read += len(content)
            digest = _digest(content)
            key = _digest(_json({"content": digest, **self._options(path)}).encode())
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise OSError("reconciliation deadline exceeded")
            cached = None
            if self.connection is not None:
                try:
                    cached = self.connection.execute(
                        "SELECT payload,receipt FROM entries WHERE key=?", (key,)
                    ).fetchone()
                except sqlite3.Error as exc:
                    self.cache_status = f"corrupt-cache-rebuilt: {exc}"
                    self.connection.close()
                    self.connection = None
            if cached is not None:
                try:
                    assert self.signing_key is not None
                    result = _decode_product(cached[0], cached[1], path, self.signing_key, key)
                except (ValueError, TypeError, RecursionError):
                    self.cache_status = "corrupt-entry-rebuilt"
                else:
                    self.hits += 1
                    self.files[str(path)] = (digest, key)
                    return result
            self.misses += 1
            with lang_registry.source_snapshot(path, content, digest):
                imports, symbols = producer()
            # Validate identity and contents again; a racing write is omitted, never cached.
            after = read_confined(self.root, path, self.max_bytes)
            self.bytes_read += len(after)
            if _digest(after) != digest:
                raise OSError("source changed during symbol extraction")
            payload = _json({
                "imports": imports,
                "symbols": [
                    {key: value for key, value in symbol.items() if key != "file"}
                    for symbol in symbols
                ],
            })
            self.files[str(path)] = (digest, key)
            if self.signing_key is None:
                return imports, symbols
            if len(payload) > _ENTRY_LIMIT:
                self.cache_status = "entry-too-large-not-persisted"
                return imports, symbols
            receipt = _receipt(self.signing_key, key, payload)
            result = _decode_product(payload, receipt, path, self.signing_key, key)
            self.entries[key] = (payload, receipt)
            return result
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.failures.append({"path": str(path), "reason": str(exc)})
            return [], []

    def finish(self, payload: dict[str, Any], *, complete: bool) -> None:
        """Recheck observed bytes before declaring a complete generation; publish atomically."""
        try:
            observed_files = list(self.files.items())
            for position, (path, (digest, _)) in enumerate(observed_files):
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    # Every retained product already has a verified per-file read. Keep
                    # this completed work; disclose the uncompleted final recheck separately.
                    self.unverified_final_files = len(observed_files) - position
                    payload["partial"] = True
                    payload.setdefault(
                        "deadline_limit",
                        {
                            "deadline_exceeded": True,
                            "files_scanned": self.hits + self.misses,
                            "files_total": len(observed_files),
                        },
                    )
                    complete = False
                    break
                try:
                    content = read_confined(self.root, Path(path), self.max_bytes)
                    self.bytes_read += len(content)
                    if _digest(content) != digest:
                        raise OSError("source changed before generation completion")
                    promote_verified_snapshot(path, digest)
                except OSError as exc:
                    self.files.pop(path)
                    self.failures.append({"path": path, "reason": str(exc)})
            rejected = {entry["path"] for entry in self.failures}
            if rejected:
                payload["symbols"] = [s for s in payload["symbols"] if s["file"] not in rejected]
                payload["imports"] = [s for s in payload["imports"] if s["file"] not in rejected]
                payload["partial"] = True
                payload["symbol_cache_coverage"] = {
                    "omitted_files": len(rejected),
                    "sample": self.failures[:20],
                    "parse_cap_only": all(
                        f["reason"]
                        == f"symbol cache file exceeds {self.max_bytes} bytes: {f['path']}"
                        for f in self.failures
                    ),
                    "budget_remediable": all(
                        "deadline" in f["reason"]
                        or f["reason"]
                        == f"symbol cache file exceeds {self.max_bytes} bytes: {f['path']}"
                        for f in self.failures
                    ),
                }
            complete = complete and not self.failures
            collector = _CONTENT_RECEIPTS.get()
            if collector is not None:
                collector.update({path: digest for path, (digest, _) in self.files.items()})
            merkle = _digest(_json(sorted(self.files.items())).encode())
            if self.deadline is not None and time.monotonic() >= self.deadline:
                self.cache_status = "not-persisted: deadline exceeded"
            elif self.signing_key is not None:
                self._persist(merkle, complete)
            payload["symbol_cache"] = {
                "schema_version": _SCHEMA,
                "path": str(self.path),
                "freshness_route": "content-reconciliation",
                "trusted_notifications": False,
                "entry_authentication": "machine-hmac-sha256"
                if self.signing_key is not None
                else "unavailable",
                "snapshot_consistency": "verified-per-file; no filesystem snapshot",
                "status": self.cache_status,
                "hits": self.hits,
                "misses": self.misses,
                "bytes_reconciled": self.bytes_read,
                "merkle_root": merkle,
                "complete": complete,
                "unverified_final_files": self.unverified_final_files,
            }
        finally:
            if self.connection is not None:
                self.connection.close()

    def _persist(self, merkle: str, complete: bool) -> None:
        try:
            prepare_directory(self.root, self.path.parent)
            ignore_path = self.path.parent / ".gitignore"
            try:
                publish_confined(self.root, ignore_path, b"*\n", only_if_missing=True)
            except FileExistsError:
                read_confined(self.root, ignore_path, 65536)
            with cache_lock(self.root, self.path, self.deadline):
                try:
                    connection = _database(self.root, self.path, deadline=self.deadline)
                except (sqlite3.Error, ValueError):
                    connection = _database(
                        self.root, self.path, rebuild=True, deadline=self.deadline
                    )
                try:
                    end = time.monotonic() + 0.25
                    if self.deadline is not None:
                        end = min(end, self.deadline)
                    connection.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
                    with connection:
                        generation = connection.execute(
                            "INSERT INTO generations(scope,merkle,complete,created) VALUES(?,?,?,?)",
                            (self.scope, merkle, int(complete), time.time()),
                        ).lastrowid
                        for key, (data, receipt) in self.entries.items():
                            connection.execute(
                                "INSERT OR REPLACE INTO entries VALUES(?,?,?,?)",
                                (key, data, receipt, generation),
                            )
                        connection.execute("DELETE FROM files WHERE scope=?", (self.scope,))
                        connection.executemany(
                            "INSERT INTO files VALUES(?,?,?,?,?)",
                            [
                                (self.scope, path, digest, key, generation)
                                for path, (digest, key) in self.files.items()
                            ],
                        )
                        connection.execute(
                            "DELETE FROM generations WHERE id < ?", (int(generation or 0) - 8,)
                        )
                        connection.execute(
                            "DELETE FROM entries WHERE key NOT IN "
                            "(SELECT key FROM entries ORDER BY generation DESC LIMIT ?)",
                            (_MAX_ENTRIES,),
                        )
                    serialized = connection.serialize()
                    if len(serialized) > _DATABASE_LIMIT:
                        raise OSError("symbol cache exceeds bounded database size")
                    publish_confined(self.root, self.path, serialized)
                finally:
                    connection.close()
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.cache_status = f"not-persisted: {exc}"
