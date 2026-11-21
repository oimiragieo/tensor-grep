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
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
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
_CONTENT_AUTHENTICATED_ROOT: ContextVar[str | None] = ContextVar(
    "tg_content_authenticated_root", default=None
)
_CONTENT_AUTHENTICATED_SIZES: ContextVar[dict[str, int] | None] = ContextVar(
    "tg_content_authenticated_sizes", default=None
)
_CONTENT_AUTHENTICATED_SCAN_PATHS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "tg_content_authenticated_scan_paths", default=None
)
CONTENT_ABSENT_RECEIPT = "<absent>"
CONTENT_PRESENT_RECEIPT = "<present>"


@contextmanager
def collect_content_receipts(
    *,
    authenticated_root: str | Path | None = None,
    authenticated_sizes: Mapping[str | Path, int] | None = None,
    authenticated_scan_paths: Iterable[str | Path] | None = None,
) -> Iterator[dict[str, str]]:
    """Let session capture bind metadata to the exact contents used by a build."""
    receipts: dict[str, str] = {}
    token = _CONTENT_RECEIPTS.set(receipts)
    root_token = _CONTENT_AUTHENTICATED_ROOT.set(
        content_receipt_key(authenticated_root) if authenticated_root is not None else None
    )
    normalized_sizes = None
    if authenticated_sizes is not None:
        normalized_sizes = {
            content_receipt_key(path): size
            for path, size in authenticated_sizes.items()
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0
        }
    sizes_token = _CONTENT_AUTHENTICATED_SIZES.set(normalized_sizes)
    normalized_scan_paths = None
    if authenticated_scan_paths is not None:
        normalized_scan_paths = tuple(
            {
                content_receipt_key(path): os.path.abspath(os.fspath(path))
                for path in authenticated_scan_paths
            }.values()
        )
    scan_paths_token = _CONTENT_AUTHENTICATED_SCAN_PATHS.set(normalized_scan_paths)
    try:
        yield receipts
    finally:
        _CONTENT_AUTHENTICATED_SCAN_PATHS.reset(scan_paths_token)
        _CONTENT_AUTHENTICATED_SIZES.reset(sizes_token)
        _CONTENT_AUTHENTICATED_ROOT.reset(root_token)
        _CONTENT_RECEIPTS.reset(token)


def content_receipts_active() -> bool:
    """Whether exact build-input observations must bypass process-global caches."""
    return _CONTENT_RECEIPTS.get() is not None


def authenticated_content_root() -> Path | None:
    """Return the immutable lexical root selected before receipt collection began."""
    value = _CONTENT_AUTHENTICATED_ROOT.get()
    return Path(value) if value is not None else None


def authenticated_content_size(path: str | Path) -> int | None:
    """Return a signed session-snapshot size, when prepare supplied one for this path."""
    sizes = _CONTENT_AUTHENTICATED_SIZES.get()
    return sizes.get(content_receipt_key(path)) if sizes is not None else None


def authenticated_content_sizes_active() -> bool:
    """Whether file-size decisions are bound to an authenticated session snapshot."""
    return _CONTENT_AUTHENTICATED_SIZES.get() is not None


def authenticated_content_size_paths() -> tuple[str, ...] | None:
    """Return normalized paths whose sizes came from the signed session snapshot."""
    sizes = _CONTENT_AUTHENTICATED_SIZES.get()
    return tuple(sizes) if sizes is not None else None


def authenticated_scan_paths() -> tuple[str, ...] | None:
    """Return the signed repo-map file universe with persisted path spelling."""
    return _CONTENT_AUTHENTICATED_SCAN_PATHS.get()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_receipt_key(path: str | Path) -> str:
    """Normalize receipt keys lexically without following a mutable filesystem target."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def record_content_receipt_digest(path: str | Path, digest: str) -> None:
    """Record one observed input; conflicting reads remain permanently unauthenticated."""
    collector = _CONTENT_RECEIPTS.get()
    if collector is None:
        return
    key = content_receipt_key(path)
    previous = collector.get(key)
    if previous is None or previous == digest:
        collector[key] = digest
    elif previous == CONTENT_PRESENT_RECEIPT and digest not in {CONTENT_ABSENT_RECEIPT, ""}:
        collector[key] = digest
    elif digest == CONTENT_PRESENT_RECEIPT and previous not in {CONTENT_ABSENT_RECEIPT, ""}:
        collector[key] = previous
    else:
        collector[key] = ""


def record_content_receipt(path: str | Path, content: bytes) -> None:
    record_content_receipt_digest(path, _digest(content))


def record_content_absence(path: str | Path) -> None:
    """Record a missing input whose absence affected the build."""
    record_content_receipt_digest(path, CONTENT_ABSENT_RECEIPT)


def record_content_presence(path: str | Path) -> None:
    """Record an existence-only decision for a file input."""
    record_content_receipt_digest(path, CONTENT_PRESENT_RECEIPT)


_MAX_AUTHENTICATED_INPUT_BYTES = 16 * 1024 * 1024


def read_content_bytes(path: str | Path) -> bytes:
    """Read and receipt exact bytes, including a fail-closed missing-file observation."""
    source = Path(path)
    try:
        if content_receipts_active():
            from tensor_grep.io.root_probe import open_regular_nontraversing_fd

            fd = open_regular_nontraversing_fd(source)
            if fd is None:
                source.lstat()  # raises FileNotFoundError/NotADirectoryError if missing
                raise OSError(f"untrusted or non-regular authenticated input: {source}")
            with os.fdopen(fd, "rb") as handle:
                content = handle.read(_MAX_AUTHENTICATED_INPUT_BYTES + 1)
            if len(content) > _MAX_AUTHENTICATED_INPUT_BYTES:
                raise OSError(
                    f"authenticated input exceeded {_MAX_AUTHENTICATED_INPUT_BYTES} bytes: {source}"
                )
        else:
            content = source.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        record_content_absence(source)
        raise
    except OSError:
        # An input that could not be authenticated must not later become publishable merely
        # because another read of the same pathname succeeds.
        record_content_receipt_digest(source, "")
        raise
    record_content_receipt(source, content)
    return content


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


def _parser_versions() -> dict[str, str]:
    versions: dict[str, str] = {"python": sys.version.split()[0]}
    for spec in lang_registry.LANGUAGE_REGISTRY.values():
        for module in spec.grammar_modules:
            try:
                versions[module] = importlib.metadata.version(module.replace("_", "-"))
            except importlib.metadata.PackageNotFoundError:
                versions[module] = "unavailable"
    return versions


def entry_key_for_digest(
    root: Path,
    path: Path,
    content_digest: str,
    *,
    scope: Path,
    max_bytes: int,
    ignore_policy: list[str],
    parser_versions: dict[str, str] | None = None,
) -> str:
    """Recompute the content- and ignore-policy-bound cache key for one file digest."""
    ignores: list[tuple[str, str]] = []
    directory = path.parent
    while True:
        ignore_file = directory / ".gitignore"
        if ignore_file.exists():
            ignores.append((
                str(ignore_file.relative_to(root)),
                _digest(read_confined(root, ignore_file, 128 * 1024)),
            ))
        if directory == root:
            break
        directory = directory.parent
    spec = lang_registry.spec_for_path(path)
    available = (
        spec.parser_for_path(path) is not None
        if spec is not None and spec.parser_for_path is not None
        else True
    )
    options = {
        "schema": _SCHEMA,
        "parser_versions": parser_versions or _parser_versions(),
        "scope": str(scope),
        "suffix": path.suffix.lower(),
        "max_parse_bytes": max_bytes,
        "ignore_policy": ignore_policy,
        "ignore_configuration": ignores,
        "parser_available": available,
    }
    return _digest(_json({"content": content_digest, **options}).encode())


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
        self._dir_ignore_cache: dict[Path, tuple[str, str] | None] = {}
        self._parser_available_cache: dict[str, bool] = {}
        self._options_cache: dict[tuple[Path, str], dict[str, Any]] = {}
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
        suffix = path.suffix.lower()
        cache_key = (path.parent, suffix)
        cached_options = self._options_cache.get(cache_key)
        if cached_options is not None:
            return cached_options
        ignores: list[tuple[str, str]] = []
        directory = path.parent
        while True:
            if directory in self._dir_ignore_cache:
                entry = self._dir_ignore_cache[directory]
            else:
                ignore_file = directory / ".gitignore"
                if ignore_file.exists():
                    entry = (
                        str(ignore_file.relative_to(self.root)),
                        _digest(read_confined(self.root, ignore_file, 128 * 1024)),
                    )
                else:
                    entry = None
                self._dir_ignore_cache[directory] = entry
            if entry is not None:
                ignores.append(entry)
            if directory == self.root or directory == directory.parent:
                break
            directory = directory.parent
        spec = lang_registry.spec_for_path(path)
        parser_key = f"{spec.language_id}:{suffix}" if spec is not None else suffix
        if parser_key in self._parser_available_cache:
            available = self._parser_available_cache[parser_key]
        else:
            available = (
                spec.parser_for_path(path) is not None
                if spec is not None and spec.parser_for_path is not None
                else True
            )
            self._parser_available_cache[parser_key] = available
        options: dict[str, Any] = {
            "schema": _SCHEMA,
            "parser_versions": self.versions,
            "scope": self.scope,
            "suffix": suffix,
            "max_parse_bytes": self.max_bytes,
            "ignore_policy": self.ignore_policy,
            "ignore_configuration": ignores,
            "parser_available": available,
        }
        self._options_cache[cache_key] = options
        return options

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
            for path, (digest, _) in self.files.items():
                record_content_receipt_digest(path, digest)
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
