from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from tensor_grep.cli.repo_map import (
    _iter_repo_files,
    build_context_pack_from_map,
    build_context_render_from_map,
    build_repo_map,
    build_repo_map_incremental,
    build_symbol_blast_radius_from_map,
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


def refresh_session(session_id: str, path: str = ".") -> SessionRefreshResult:
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


def serve_session_request(session_id: str, request: dict[str, Any], path: str = ".") -> dict[str, Any]:
    payload = get_session(session_id, path)
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

    raise ValueError(f"unknown session command: {command or '<empty>'}")


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

    for raw_line in request_stream:
        line = raw_line.strip()
        if not line:
            continue
        request_count += 1
        try:
            request = cast(dict[str, Any], json.loads(line))
            response = serve_session_request(session_id, request, path)
        except SessionStaleError as exc:
            if refresh_on_stale:
                refresh_session(session_id, path)
                request = cast(dict[str, Any], json.loads(line))
                response = serve_session_request(session_id, request, path)
            else:
                response = {
                    "version": _SESSION_VERSION,
                    "session_id": session_id,
                    "error": {"code": "stale_session", "message": str(exc)},
                }
        except Exception as exc:
            response = {
                "version": _SESSION_VERSION,
                "session_id": session_id,
                "error": {"code": "invalid_request", "message": str(exc)},
            }
        response_stream.write(json.dumps(response) + "\n")
        response_stream.flush()

    return request_count
