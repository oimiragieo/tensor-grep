from __future__ import annotations

import json
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, TextIO, cast

from tensor_grep.cli.repo_map import (
    _iter_repo_files,
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


class _SessionServeCache:
    def __init__(self, max_entries: int = _SESSION_SERVE_CACHE_MAX_ENTRIES) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[tuple[str, str], _SessionServeCacheEntry] = OrderedDict()
        self._size_bytes = 0

    def _key(self, session_id: str, path: str) -> tuple[str, str]:
        return (str(_resolve_root(Path(path))), session_id)

    def get(self, session_id: str, path: str) -> dict[str, Any] | None:
        key = self._key(session_id, path)
        entry = self._entries.pop(key, None)
        if entry is None:
            return None
        self._entries[key] = entry
        return entry.payload

    def put(self, session_id: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._key(session_id, path)
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size_bytes -= previous.size_bytes

        entry = _SessionServeCacheEntry(
            payload=payload,
            size_bytes=len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ),
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

    @property
    def session_count(self) -> int:
        return len(self._entries)

    @property
    def size_bytes(self) -> int:
        return self._size_bytes


def _resolve_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    return resolved if resolved.is_dir() else resolved.parent


def _sessions_dir(root: Path) -> Path:
    return root / _TG_DIRNAME / _SESSIONS_SUBDIR


def _index_path(root: Path) -> Path:
    return _sessions_dir(root) / _INDEX_FILE


def _session_payload_path(root: Path, session_id: str) -> Path:
    return _sessions_dir(root) / f"{session_id}.json"


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
        snapshot.append(
            {
                "path": str(path),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    snapshot.sort(key=lambda item: str(item["path"]))
    return snapshot


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


def _stale_changeset(payload: dict[str, Any]) -> dict[str, list[str]] | None:
    snapshot = cast(list[dict[str, Any]], payload.get("snapshot") or [])
    if not snapshot:
        return None

    root = _resolve_root(Path(str(payload.get("root", payload.get("path", ".")))))
    snapshot_by_path = {
        str(Path(str(entry["path"])).expanduser().resolve()): entry for entry in snapshot
    }
    current_files = _iter_repo_files(root)
    current_paths = {str(current): current for current in current_files}

    added = sorted(path for path in current_paths if path not in snapshot_by_path)
    removed = sorted(path for path in snapshot_by_path if path not in current_paths)
    modified: list[str] = []
    for current_path, path_obj in current_paths.items():
        snapshot_entry = snapshot_by_path.get(current_path)
        if snapshot_entry is None:
            continue
        try:
            stat = path_obj.stat()
        except OSError:
            modified.append(current_path)
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


def _ensure_session_not_stale(payload: dict[str, Any]) -> None:
    changeset = _stale_changeset(payload)
    if _changeset_has_entries(changeset):
        raise SessionStaleError(_changeset_message(cast(dict[str, list[str]], changeset)))


def _resolve_request_session_target(
    request: dict[str, Any], session_id: str, path: str
) -> tuple[str, str]:
    requested_session_id = str(request.get("session_id", session_id)).strip() or session_id
    requested_path = request.get("path", request.get("root", path))
    resolved_path = str(requested_path).strip() if requested_path is not None else path
    return requested_session_id, resolved_path or path


def open_session(path: str = ".") -> SessionOpenResult:
    root = _resolve_root(Path(path))
    repo_map = build_repo_map(root)
    created_at = datetime.now(UTC).isoformat()
    session_id = f"session-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{root.name}"
    changeset = _empty_changeset()
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "repo_map": repo_map,
        "snapshot": _capture_snapshot(repo_map["related_paths"]),
        "refresh_type": "full",
        "changeset": changeset,
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
    records = _load_index(root)
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
    )


def refresh_session(
    session_id: str,
    path: str = ".",
    *,
    payload_cache: _SessionServeCache | None = None,
) -> SessionRefreshResult:
    root = _resolve_root(Path(path))
    existing = get_session(session_id, path)
    changeset = _stale_changeset(existing)
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


def get_session(session_id: str, path: str = ".") -> dict[str, Any]:
    root = _resolve_root(Path(path))
    session_path = _session_payload_path(root, session_id)
    if not session_path.exists():
        raise FileNotFoundError(f"Session not found: {session_id}")
    return cast(dict[str, Any], json.loads(session_path.read_text(encoding="utf-8")))


def session_context(session_id: str, query: str, path: str = ".") -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
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
    max_symbols: int = 5,
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
    context = build_context_edit_plan_from_map(
        payload["repo_map"],
        query,
        max_files=max_files,
        max_symbols=max_symbols,
    )
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-edit-plan"
    return context


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
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
    context = build_context_render_from_map(
        payload["repo_map"],
        query,
        max_files=max_files,
        max_sources=max_sources,
        max_symbols_per_file=max_symbols_per_file,
        max_render_chars=max_render_chars,
        max_tokens=max_tokens,
        model=model,
        optimize_context=optimize_context,
        render_profile=render_profile,
    )
    context["session_id"] = session_id
    context["routing_reason"] = "session-context-render"
    return context


def session_blast_radius(
    session_id: str,
    symbol: str,
    path: str = ".",
    *,
    max_depth: int = 3,
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
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
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
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
) -> dict[str, Any]:
    payload = get_session(session_id, path)
    _ensure_session_not_stale(payload)
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

    _ensure_session_not_stale(payload)

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
        response = build_context_render_from_map(
            repo_map,
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
            model=(
                None if request.get("model") in (None, "") else str(request["model"])
            ),
            optimize_context=bool(request.get("optimize_context", False)),
            render_profile=str(request.get("render_profile", "full")),
        )
        response["session_id"] = session_id
        response["routing_reason"] = "session-context-render"
        return response

    if command == "context_edit_plan":
        query = str(request.get("query", "")).strip()
        if not query:
            raise ValueError("context_edit_plan requests require a non-empty query")
        response = build_context_edit_plan_from_map(
            repo_map,
            query,
            max_files=int(request.get("max_files", 3)),
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
    session_payload = payload if payload is not None else get_session(resolved_session_id, resolved_path)
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
            request_session_id, request_path = _resolve_request_session_target(request, session_id, path)
            command = str(request.get("command", "")).strip().lower()
            if command == "stats":
                response = {
                    "version": _SESSION_VERSION,
                    "ok": True,
                    "session_count": payload_cache.session_count,
                    "cache_size_bytes": payload_cache.size_bytes,
                    "uptime_seconds": max(0.0, monotonic() - started_at),
                    "request_count": request_count,
                }
            else:
                payload = payload_cache.load(request_session_id, request_path)
                response = serve_session_request(
                    request_session_id,
                    request,
                    request_path,
                    payload=payload,
                )
        except SessionStaleError as exc:
            if refresh_on_stale:
                refresh_session(request_session_id, request_path, payload_cache=payload_cache)
                payload = payload_cache.load(request_session_id, request_path)
                response = serve_session_request(
                    request_session_id,
                    request,
                    request_path,
                    payload=payload,
                )
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
