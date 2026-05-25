from __future__ import annotations

import copy
import json
import os
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, TextIO, cast
from uuid import uuid4

from tensor_grep.cli.repo_map import (
    _is_repo_context_file,
    _iter_repo_files,
    apply_repo_map_output_limits,
    build_context_edit_plan_from_map,
    build_context_pack_from_map,
    build_context_render_from_map,
    build_repo_map,
    build_repo_map_incremental,
    build_symbol_blast_radius_from_map,
    build_symbol_blast_radius_plan_from_map,
    build_symbol_blast_radius_render_from_map,
    build_symbol_callers_from_map,
    build_symbol_defs_from_map,
    build_symbol_impact_from_map,
    build_symbol_refs_from_map,
)

_SESSION_VERSION = 1
_TG_DIRNAME = ".tensor-grep"
_SESSIONS_SUBDIR = "sessions"
_INDEX_FILE = "index.json"
_SESSION_SERVE_CACHE_MAX_ENTRIES = 32
_SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES = 32
_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV = "TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES"
_DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT = 512
_DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT = 512


@dataclass
class SessionRecord:
    version: int
    session_id: str
    root: str
    created_at: str
    file_count: int
    symbol_count: int


@dataclass
class SessionOpenResult:
    session_id: str
    root: str
    created_at: str
    file_count: int
    symbol_count: int
    refresh_type: str
    changeset: dict[str, list[str]]
    scan_limit: dict[str, Any] | None = None
    build_seconds: float | None = None


@dataclass
class SessionRefreshResult:
    session_id: str
    root: str
    refreshed_at: str
    file_count: int
    symbol_count: int
    refresh_type: str
    changeset: dict[str, list[str]]


class SessionStaleError(RuntimeError):
    pass


@dataclass
class _SessionServeCacheEntry:
    payload: dict[str, Any]
    size_bytes: int


@dataclass
class _SessionServeResponseCacheEntry:
    payload: dict[str, Any]
    size_bytes: int


def _configured_positive_int(env_var: str, default: int) -> int:
    raw_value = os.environ.get(env_var)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _json_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class _SessionServeCache:
    def __init__(self, max_entries: int = _SESSION_SERVE_CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[tuple[str, str], _SessionServeCacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0
        self._refreshes = 0

    def _key(self, session_id: str, path: str) -> tuple[str, str]:
        return (str(_session_root_for_payload(session_id, path)), session_id)

    def get(self, session_id: str, path: str) -> dict[str, Any] | None:
        key = self._key(session_id, path)
        entry = self._entries.pop(key, None)
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries[key] = entry
        return entry.payload

    def put(self, session_id: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._key(session_id, path)
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size_bytes -= previous.size_bytes

        entry = _SessionServeCacheEntry(
            payload=payload,
            size_bytes=_json_size_bytes(payload),
        )
        self._entries[key] = entry
        self._size_bytes += entry.size_bytes

        while len(self._entries) > self._max_entries:
            _, evicted = self._entries.popitem(last=False)
            self._size_bytes -= evicted.size_bytes

        return payload

    def load(self, session_id: str, path: str) -> dict[str, Any]:
        cached = self.get(session_id, path)
        if cached is not None:
            return cached
        return self.put(session_id, path, get_session(session_id, path))

    def load_with_status(self, session_id: str, path: str) -> tuple[dict[str, Any], str]:
        cached = self.get(session_id, path)
        if cached is not None:
            return cached, "hit"
        return self.put(session_id, path, get_session(session_id, path)), "miss"

    def record_refresh(self) -> None:
        self._refreshes += 1

    @property
    def session_count(self) -> int:
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def refreshes(self) -> int:
        return self._refreshes

    @property
    def root_count(self) -> int:
        return len({root for root, _ in self._entries})

    @property
    def sessions(self) -> list[dict[str, str]]:
        return [
            {"root": root, "session_id": session_id} for root, session_id in self._entries.keys()
        ]


class _SessionServeResponseCache:
    def __init__(
        self,
        max_entries: int = _SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES,
        max_size_bytes: int | None = None,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._max_size_bytes = (
            _configured_positive_int(
                _SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV,
                _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
            )
            if max_size_bytes is None
            else max(1, int(max_size_bytes))
        )
        self._entries: OrderedDict[tuple[str, ...], _SessionServeResponseCacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._oversized_skips = 0

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        entry = self._entries.pop(key, None)
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries[key] = entry
        return copy.deepcopy(entry.payload)

    def put(self, key: tuple[str, ...], response: dict[str, Any]) -> None:
        self._puts += 1
        size_bytes = _json_size_bytes(response)
        if size_bytes > self._max_size_bytes:
            self._oversized_skips += 1
            return
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size_bytes -= previous.size_bytes
        entry = _SessionServeResponseCacheEntry(
            payload=copy.deepcopy(response),
            size_bytes=size_bytes,
        )
        self._entries[key] = entry
        self._size_bytes += entry.size_bytes
        while len(self._entries) > self._max_entries or self._size_bytes > self._max_size_bytes:
            _, evicted = self._entries.popitem(last=False)
            self._size_bytes -= evicted.size_bytes

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def puts(self) -> int:
        return self._puts

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def oversized_skips(self) -> int:
        return self._oversized_skips


def _resolve_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    return resolved if resolved.is_dir() else resolved.parent


def _sessions_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _SESSIONS_SUBDIR


def _index_path(root: Path) -> Path:
    return _sessions_dir(root) / _INDEX_FILE


def _session_payload_path(root: Path, session_id: str) -> Path:
    return _sessions_dir(root) / f"{session_id}.json"


def _session_root_for_payload(session_id: str, path: str = ".") -> Path:
    root = _resolve_root(Path(path))
    if _session_payload_path(root, session_id).exists():
        return root
    for candidate in _nearby_session_roots(path):
        if candidate == root:
            continue
        if _session_payload_path(candidate, session_id).exists():
            return candidate
    return root


def _nearby_session_roots(path: str = ".") -> list[Path]:
    root = _resolve_root(Path(path))
    candidates: list[Path] = [root]
    candidates.extend(parent for parent in root.parents if parent != root)
    try:
        candidates.extend(child for child in root.iterdir() if child.is_dir())
    except OSError:
        pass

    seen: set[str] = set()
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower() if sys.platform.startswith("win") else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if _index_path(resolved).exists():
            roots.append(resolved)
    return roots


def _load_index(root: Path) -> list[SessionRecord]:
    index_path = _index_path(root)
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [SessionRecord(**entry) for entry in payload]


def _write_index(root: Path, records: list[SessionRecord]) -> None:
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(record) for record in records], indent=2), encoding="utf-8")


def _capture_snapshot(file_paths: list[str]) -> list[dict[str, Any]]:
    snapshot: list[dict[str, Any]] = []
    for current in file_paths:
        path = Path(current)
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot.append({
            "path": str(path),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
    snapshot.sort(key=lambda item: str(item["path"]))
    return snapshot


def _snapshot_path_key(raw_path: object) -> str:
    path = Path(str(raw_path)).expanduser()
    if path.is_absolute():
        return str(path)
    return str(path.resolve())


def _empty_changeset() -> dict[str, list[str]]:
    return {"added": [], "modified": [], "removed": []}


def _changeset_has_entries(changeset: dict[str, list[str]] | None) -> bool:
    return bool(changeset and any(changeset[key] for key in ("added", "modified", "removed")))


def _changeset_message(changeset: dict[str, list[str]]) -> str:
    details: list[str] = []
    for key in ("modified", "added", "removed"):
        paths = changeset.get(key, [])
        if not paths:
            continue
        details.append(f"{key} {len(paths)}: {paths[0]}")
    if not details:
        return "cached session files changed on disk"
    return f"cached session files changed on disk ({'; '.join(details)})"


def _stale_changeset(
    payload: dict[str, Any], *, detect_added_files: bool = True
) -> dict[str, list[str]] | None:
    snapshot = cast(list[dict[str, Any]], payload.get("snapshot") or [])
    if not snapshot:
        return None

    root = _resolve_root(Path(str(payload.get("root", payload.get("path", ".")))))
    snapshot_by_path = {_snapshot_path_key(entry["path"]): entry for entry in snapshot}

    added: list[str] = []
    current_paths: dict[str, Path] = {}
    if detect_added_files:
        context_root = root if root.is_dir() else root.parent
        current_files = [
            current
            for current in _iter_repo_files(root)
            if _is_repo_context_file(current, context_root)
        ]
        current_paths = {str(current): current for current in current_files}
        added = sorted(path for path in current_paths if path not in snapshot_by_path)

    removed: list[str] = []
    modified: list[str] = []
    for current_path, snapshot_entry in snapshot_by_path.items():
        try:
            stat = os.stat(current_paths.get(current_path) or current_path)
        except OSError:
            removed.append(current_path)
            continue
        if int(stat.st_size) != int(snapshot_entry["size"]) or int(stat.st_mtime_ns) != int(
            snapshot_entry["mtime_ns"]
        ):
            modified.append(current_path)

    return {
        "added": added,
        "modified": sorted(dict.fromkeys(modified)),
        "removed": removed,
    }


def _ensure_session_not_stale(payload: dict[str, Any], *, detect_added_files: bool = False) -> None:
    changeset = _stale_changeset(payload, detect_added_files=detect_added_files)
    if _changeset_has_entries(changeset):
        raise SessionStaleError(_changeset_message(cast(dict[str, list[str]], changeset)))


def _resolve_request_session_target(
    request: dict[str, Any], session_id: str, path: str
) -> tuple[str, str]:
    requested_session_id = str(request.get("session_id", session_id)).strip() or session_id
    requested_path = request.get("path", request.get("root", path))
    resolved_path = str(requested_path).strip() if requested_path is not None else path
    return requested_session_id, resolved_path or path


def _new_session_id(root: Path) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"session-{timestamp}-{root.name}-{uuid4().hex[:8]}"


def open_session(path: str = ".", *, max_repo_files: int | None = None) -> SessionOpenResult:
    root = _resolve_root(Path(path))
    started_at = monotonic()
    repo_map = build_repo_map(root, max_repo_files=max_repo_files)
    built_at = monotonic()
    created_at = datetime.now(UTC).isoformat()
    session_id = _new_session_id(root)
    changeset = _empty_changeset()
    scan_limit = cast(dict[str, Any] | None, repo_map.get("scan_limit"))
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "repo_map": repo_map,
        "snapshot": _capture_snapshot(repo_map["related_paths"]),
        "refresh_type": "full",
        "changeset": changeset,
        "scan_limit": scan_limit,
        "build_seconds": max(0.0, built_at - started_at),
    }
    session_path = _session_payload_path(root, session_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    record = SessionRecord(
        version=_SESSION_VERSION,
        session_id=session_id,
        root=str(root),
        created_at=created_at,
        file_count=len(repo_map["files"]),
        symbol_count=len(repo_map["symbols"]),
    )
    records = [existing for existing in _load_index(root) if existing.session_id != session_id]
    records.insert(0, record)
    _write_index(root, records)
    return SessionOpenResult(
        session_id=session_id,
        root=str(root),
        created_at=created_at,
        file_count=record.file_count,
        symbol_count=record.symbol_count,
        refresh_type="full",
        changeset=changeset,
        scan_limit=scan_limit,
        build_seconds=max(0.0, built_at - started_at),
    )


def refresh_session(
    session_id: str,
    path: str = ".",
    *,
    payload_cache: _SessionServeCache | None = None,
) -> SessionRefreshResult:
    root = _resolve_root(Path(path))
    existing = get_session(session_id, path)
    changeset = _stale_changeset(existing, detect_added_files=True)
    refresh_type = "full"
    if changeset is not None:
        try:
            repo_map = build_repo_map_incremental(
                cast(dict[str, Any], existing["repo_map"]),
                changeset,
            )
            refresh_type = "incremental"
        except Exception:
            repo_map = build_repo_map(root)
            refresh_type = "full"
    else:
        repo_map = build_repo_map(root)
        changeset = _empty_changeset()
    refreshed_at = datetime.now(UTC).isoformat()
    created_at = str(existing.get("created_at", refreshed_at))
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "refreshed_at": refreshed_at,
        "repo_map": repo_map,
        "snapshot": _capture_snapshot(repo_map["related_paths"]),
        "refresh_type": refresh_type,
        "changeset": changeset,
    }
    session_path = _session_payload_path(root, session_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if payload_cache is not None:
        payload_cache.put(session_id, str(root), payload)

    records = _load_index(root)
    for index, record in enumerate(records):
        if record.session_id == session_id:
            records[index] = SessionRecord(
                version=_SESSION_VERSION,
                session_id=session_id,
                root=str(root),
                created_at=created_at,
                file_count=len(repo_map["files"]),
                symbol_count=len(repo_map["symbols"]),
            )
            break
    else:
        records.insert(
            0,
            SessionRecord(
                version=_SESSION_VERSION,
                session_id=session_id,
                root=str(root),
                created_at=created_at,
                file_count=len(repo_map["files"]),
                symbol_count=len(repo_map["symbols"]),
            ),
        )
    _write_index(root, records)

    return SessionRefreshResult(
        session_id=session_id,
        root=str(root),
        refreshed_at=refreshed_at,
        file_count=len(repo_map["files"]),
        symbol_count=len(repo_map["symbols"]),
        refresh_type=refresh_type,
        changeset=changeset,
    )


def list_sessions(path: str = ".") -> list[SessionRecord]:
    root = _resolve_root(Path(path))
    return _load_index(root)


def list_sessions_with_discovery(path: str = ".") -> tuple[list[SessionRecord], str, bool]:
    root = _resolve_root(Path(path))
    direct = _load_index(root)
    if direct:
        return direct, str(root), False

    records: list[SessionRecord] = []
    discovered_roots = [candidate for candidate in _nearby_session_roots(path) if candidate != root]
    for discovered_root in discovered_roots:
        records.extend(_load_index(discovered_root))

    records.sort(key=lambda record: record.created_at, reverse=True)
    return (
        records,
        str(discovered_roots[0]) if len(discovered_roots) == 1 else str(root),
        bool(records),
    )


def get_session(session_id: str, path: str = ".") -> dict[str, Any]:
    root = _session_root_for_payload(session_id, path)
    session_path = _session_payload_path(root, session_id)
    if not session_path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    return cast(dict[str, Any], json.loads(session_path.read_text(encoding="utf-8")))


def _load_session_payload(
    session_id: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
    payload_cache: _SessionServeCache | None = None,
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    try:
        _ensure_session_not_stale(payload, detect_added_files=refresh_on_stale)
    except SessionStaleError:
        if not refresh_on_stale:
            raise
        refresh_session(session_id, path, payload_cache=payload_cache)
        payload = get_session(session_id, path)
        _ensure_session_not_stale(payload, detect_added_files=True)
    return payload


def _session_health_payload(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    changeset = _stale_changeset(payload, detect_added_files=False) or _empty_changeset()
    stale = _changeset_has_entries(changeset)
    return {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(payload.get("root", repo_map.get("path", "."))),
        "created_at": str(payload.get("created_at", "")),
        "refreshed_at": str(payload.get("refreshed_at", payload.get("created_at", ""))),
        "refresh_type": str(payload.get("refresh_type", "full")),
        "file_count": len(cast(list[Any], repo_map.get("files", []))),
        "symbol_count": len(cast(list[Any], repo_map.get("symbols", []))),
        "ok": not stale,
        "stale": stale,
        "changeset": changeset,
    }


def session_context(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    context = build_context_pack_from_map(payload["repo_map"], query)
    context["session_id"] = session_id
    context["routing_reason"] = "session-context"
    return context


def session_context_edit_plan(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int | None = None,
    max_tokens: int | None = None,
    max_symbols: int = 5,
    max_repo_files: int | None = _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = monotonic()
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    loaded_at = monotonic()
    repo_map = _limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = build_context_edit_plan_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_tokens=max_tokens,
        max_symbols=max_symbols,
    )
    built_at = monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-edit-plan"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_edit_plan_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def _limited_session_repo_map(
    repo_map: dict[str, Any],
    *,
    max_repo_files: int | None,
) -> dict[str, Any]:
    if max_repo_files is None:
        return repo_map
    return apply_repo_map_output_limits(repo_map, max_files=max(1, int(max_repo_files)))


def _serve_request_cache_value(request: dict[str, Any], name: str, default: Any = "") -> str:
    value = request.get(name, default)
    if value in (None, ""):
        return ""
    return str(value)


def _serve_payload_fingerprint(payload: dict[str, Any]) -> tuple[str, ...]:
    repo_map = cast(dict[str, Any], payload.get("repo_map") or {})
    return (
        str(payload.get("root", "")),
        str(payload.get("created_at", "")),
        str(payload.get("refreshed_at", "")),
        str(len(cast(list[Any], repo_map.get("files", [])))),
        str(len(cast(list[Any], repo_map.get("symbols", [])))),
    )


def _serve_response_cache_key(
    *,
    session_id: str,
    path: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, ...]:
    command = str(request.get("command", "")).strip().lower()
    common = (
        str(_session_root_for_payload(session_id, path)),
        session_id,
        *_serve_payload_fingerprint(payload),
        command,
        str(request.get("query", "")).strip(),
        _serve_request_cache_value(request, "max_files", 3),
        _serve_request_cache_value(request, "max_sources"),
        _serve_request_cache_value(request, "max_tokens"),
        _serve_request_cache_value(request, "max_repo_files"),
    )
    if command == "context_render":
        return (
            *common,
            _serve_request_cache_value(request, "max_symbols_per_file", 6),
            _serve_request_cache_value(request, "max_render_chars"),
            _serve_request_cache_value(request, "model"),
            _serve_request_cache_value(request, "optimize_context", False),
            _serve_request_cache_value(request, "render_profile", "full"),
            _serve_request_cache_value(request, "profile", False),
        )
    return (
        *common,
        _serve_request_cache_value(request, "max_symbols", 5),
    )


def session_context_render(
    session_id: str,
    query: str,
    path: str = ".",
    *,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    profile: bool = False,
    max_repo_files: int | None = _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    started_at = monotonic()
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    loaded_at = monotonic()
    repo_map = _limited_session_repo_map(
        cast(dict[str, Any], payload["repo_map"]),
        max_repo_files=max_repo_files,
    )
    context = build_context_render_from_map(
        repo_map,
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
        profile=profile,
    )
    built_at = monotonic()
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-render"
    context["session_timing"] = {
        "cache_status": "disk-load",
        "load_session_seconds": max(0.0, loaded_at - started_at),
        "build_context_render_seconds": max(0.0, built_at - loaded_at),
        "total_seconds": max(0.0, built_at - started_at),
    }
    return context


def session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_symbol_blast_radius_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius"
    return response


def session_blast_radius_plan(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_symbols: int = 5,
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_symbol_blast_radius_plan_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_symbols=max_symbols,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-plan"
    return response


def session_blast_radius_render(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    max_files: int = 3,
    max_sources: int = 5,
    max_symbols_per_file: int = 6,
    max_render_chars: int | None = None,
    optimize_context: bool = False,
    render_profile: str = "full",
    refresh_on_stale: bool = False,
) -> dict[str, Any]:
    payload = _load_session_payload(session_id, path, refresh_on_stale=refresh_on_stale)
    response = build_symbol_blast_radius_render_from_map(
        payload["repo_map"],
        symbol,
        max_depth=max_depth,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )
    response["session_id"] = session_id
    response["routing_reason"] = "session-blast-radius-render"
    return response


def _serve_session_request_from_payload(
    session_id: str,
    request: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    repo_map = cast(dict[str, Any], payload["repo_map"])
    command = str(request.get("command", "")).strip().lower()

    if command == "ping":
        return {"version": _SESSION_VERSION, "session_id": session_id, "ok": True}

    if command == "show":
        response = dict(payload)
        response["session_id"] = session_id
        return response

    _ensure_session_not_stale(
        payload,
        detect_added_files=bool(request.get("refresh_on_stale", False)),
    )

    if command == "repo_map":
        response = dict(repo_map)
        response["session_id"] = session_id
        response["routing_reason"] = "session-repo-map"
        return response

    if command == "context":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context requests require a non-empty query")
        response = build_context_pack_from_map(repo_map, query)
        response["session_id"] = session_id
        response["routing_reason"] = "session-context"
        return response

    if command == "context_render":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_render requests require a non-empty query")
        max_repo_files = request.get(
            "max_repo_files",
            _DEFAULT_SESSION_CONTEXT_RENDER_REPO_MAP_LIMIT,
        )
        response = build_context_render_from_map(
            _limited_session_repo_map(
                repo_map,
                max_repo_files=(
                    None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
                ),
            ),
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            model=(None if request.get("model") in (None, "") else str(request["model"])),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-render"
        return response

    if command == "context_edit_plan":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_edit_plan requests require a non-empty query")
        max_repo_files = request.get("max_repo_files", _DEFAULT_SESSION_EDIT_PLAN_REPO_MAP_LIMIT)
        scoped_repo_map = _limited_session_repo_map(
            repo_map,
            max_repo_files=(
                None if max_repo_files in (None, "") else int(cast(int | str, max_repo_files))
            ),
        )
        response = build_context_edit_plan_from_map(
            scoped_repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
            max_sources=(
                None if request.get("max_sources") in (None, "") else int(request["max_sources"])
            ),
            max_tokens=(
                None if request.get("max_tokens") in (None, "") else int(request["max_tokens"])
            ),
            max_symbols=int(request.get("max_symbols", 5)),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-edit-plan"
        return response

    if command == "defs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("defs requests require a non-empty symbol")
        response = build_symbol_defs_from_map(repo_map, symbol)
        response["session_id"] = session_id
        response["routing_reason"] = "session-defs"
        return response

    if command == "impact":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("impact requests require a non-empty symbol")
        response = build_symbol_impact_from_map(repo_map, symbol)
        response["session_id"] = session_id
        response["routing_reason"] = "session-impact"
        return response

    if command == "refs":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("refs requests require a non-empty symbol")
        response = build_symbol_refs_from_map(repo_map, symbol)
        response["session_id"] = session_id
        response["routing_reason"] = "session-refs"
        return response

    if command == "callers":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("callers requests require a non-empty symbol")
        response = build_symbol_callers_from_map(repo_map, symbol)
        response["session_id"] = session_id
        response["routing_reason"] = "session-callers"
        return response

    if command == "blast_radius":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius requests require a non-empty symbol")
        response = build_symbol_blast_radius_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius"
        return response

    if command == "blast_radius_render":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_render requests require a non-empty symbol")
        response = build_symbol_blast_radius_render_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_sources=int(request.get("max_sources", 5)),
            max_symbols_per_file=int(request.get("max_symbols_per_file", 6)),
            max_render_chars=(
                None
                if request.get("max_render_chars") in (None, "")
                else int(request["max_render_chars"])
            ),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
            profile=bool(request.get("profile", False)),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-render"
        return response

    if command == "blast_radius_plan":
        symbol = str(request.get("symbol", "")).strip()
        if not symbol:
            raise ValueError("blast_radius_plan requests require a non-empty symbol")
        response = build_symbol_blast_radius_plan_from_map(
            repo_map,
            symbol,
            max_depth=int(request.get("max_depth", 3)),
            max_files=int(request.get("max_files", 3)),
            max_symbols=int(request.get("max_symbols", 5)),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-blast-radius-plan"
        return response

    raise ValueError(f"unknown session command: {command or '<empty>'}")


def serve_session_request(
    session_id: str,
    request: dict[str, Any],
    path: str = ".",
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_session_id, resolved_path = _resolve_request_session_target(request, session_id, path)
    session_payload = (
        payload if payload is not None else get_session(resolved_session_id, resolved_path)
    )
    return _serve_session_request_from_payload(resolved_session_id, request, session_payload)


def serve_session_stream(
    session_id: str,
    path: str = ".",
    *,
    refresh_on_stale: bool = False,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> int:
    request_stream = input_stream or sys.stdin
    response_stream = output_stream or sys.stdout
    request_count = 0
    started_at = monotonic()
    payload_cache = _SessionServeCache()
    response_cache = _SessionServeResponseCache()

    for raw_line in request_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_count += 1
        request_session_id = session_id
        request_path = path
        response: dict[str, Any]
        try:
            request = cast(dict[str, Any], json.loads(line))
            request_session_id, request_path = _resolve_request_session_target(
                request, session_id, path
            )
            if refresh_on_stale and not bool(request.get("refresh_on_stale", False)):
                request = dict(request)
                request["refresh_on_stale"] = True
            command = str(request.get("command", "")).strip().lower()
            if command == "stats":
                response = {
                    "version": _SESSION_VERSION,
                    "ok": True,
                    "cache_hits": payload_cache.hits,
                    "cache_misses": payload_cache.misses,
                    "refresh_count": payload_cache.refreshes,
                    "root_count": payload_cache.root_count,
                    "session_count": payload_cache.session_count,
                    "sessions": payload_cache.sessions,
                    "cache_size_bytes": payload_cache.size_bytes,
                    "response_cache_hits": response_cache.hits,
                    "response_cache_misses": response_cache.misses,
                    "response_cache_puts": response_cache.puts,
                    "response_cache_entries": response_cache.entry_count,
                    "response_cache_size_bytes": response_cache.size_bytes,
                    "response_cache_max_size_bytes": response_cache.max_size_bytes,
                    "response_cache_oversized_skips": response_cache.oversized_skips,
                    "uptime_seconds": max(0.0, monotonic() - started_at),
                    "request_count": request_count,
                }
            elif command == "health":
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = _session_health_payload(request_session_id, payload)
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response_cache_status = "bypass"
                cacheable_response_command = command in {
                    "context_edit_plan",
                    "context_render",
                } and not bool(request.get("refresh_on_stale", False))
                if cacheable_response_command:
                    _ensure_session_not_stale(payload, detect_added_files=False)
                    response_cache_key = _serve_response_cache_key(
                        session_id=request_session_id,
                        path=request_path,
                        request=request,
                        payload=payload,
                    )
                    cached_response = response_cache.get(response_cache_key)
                    if cached_response is not None:
                        response = cached_response
                        response_cache_status = "hit"
                    else:
                        response = serve_session_request(
                            request_session_id,
                            request,
                            request_path,
                            payload=payload,
                        )
                        response_cache.put(response_cache_key, response)
                        response_cache_status = "miss"
                else:
                    response = serve_session_request(
                        request_session_id,
                        request,
                        request_path,
                        payload=payload,
                    )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
                if cacheable_response_command:
                    response["serve_response_cache"] = {
                        "status": response_cache_status,
                        "entries": response_cache.entry_count,
                        "hits": response_cache.hits,
                        "misses": response_cache.misses,
                        "size_bytes": response_cache.size_bytes,
                        "max_size_bytes": response_cache.max_size_bytes,
                        "oversized_skips": response_cache.oversized_skips,
                    }
        except SessionStaleError as exc:
            if refresh_on_stale:
                refresh_session(request_session_id, request_path, payload_cache=payload_cache)
                payload_cache.record_refresh()
                payload, cache_status = payload_cache.load_with_status(
                    request_session_id, request_path
                )
                response = serve_session_request(
                    request_session_id,
                    request,
                    request_path,
                    payload=payload,
                )
                response["serve_cache"] = {
                    "status": cache_status,
                    "session_count": payload_cache.session_count,
                    "root_count": payload_cache.root_count,
                }
            else:
                response = {
                    "version": _SESSION_VERSION,
                    "session_id": request_session_id,
                    "error": {"code": "stale_session", "message": str(exc)},
                }
        except Exception as exc:
            response = {
                "version": _SESSION_VERSION,
                "session_id": request_session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        response_stream.write(json.dumps(response) + "\n")
        response_stream.flush()

    return request_count
