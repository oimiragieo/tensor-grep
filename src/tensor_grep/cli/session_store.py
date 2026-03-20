from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tensor_grep.cli.repo_map import build_context_pack_from_map, build_repo_map

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


def open_session(path: str = ".") -> SessionOpenResult:
    root = _resolve_root(Path(path))
    repo_map = build_repo_map(root)
    created_at = datetime.now(UTC).isoformat()
    session_id = f"session-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{root.name}"
    payload = {
        "version": _SESSION_VERSION,
        "session_id": session_id,
        "root": str(root),
        "created_at": created_at,
        "repo_map": repo_map,
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
    context = build_context_pack_from_map(payload["repo_map"], query)
    context["session_id"] = session_id
    context["routing_reason"] = "session-context"
    return context
