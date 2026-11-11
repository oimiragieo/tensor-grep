from __future__ import annotations

import json
import threading
import time
from io import BytesIO
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock, session_daemon, session_daemon_deadlines, session_store


def _hold_session_index(root: Path) -> tuple[threading.Event, threading.Thread]:
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with _index_lock.index_lock(session_store._index_path(root)):
            acquired.set()
            release.wait(1.5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(2.0)
    return release, thread


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def test_open_session_index_lock_uses_daemon_deadline(tmp_path: Path) -> None:
    root = _project(tmp_path)
    release, holder = _hold_session_index(root)
    deadline = time.monotonic() + 0.5
    started = time.monotonic()
    try:
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            session_store.open_session(str(root), deadline_monotonic=deadline)
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join(timeout=2.0)
    assert not holder.is_alive()


def test_refresh_session_index_lock_uses_daemon_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    opened = session_store.open_session(str(root))
    existing = session_store.get_session(opened.session_id, str(root))
    monkeypatch.setattr(session_store, "_clear_all_source_caches", lambda: None)
    monkeypatch.setattr(session_store, "get_session", lambda *_args, **_kwargs: existing)
    monkeypatch.setattr(session_store, "_stale_changeset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        session_store, "build_repo_map", lambda *_args, **_kwargs: existing["repo_map"]
    )
    monkeypatch.setattr(
        session_store, "_capture_snapshot", lambda *_args, **_kwargs: existing["snapshot"]
    )
    release, holder = _hold_session_index(root)
    deadline = time.monotonic() + 0.5
    started = time.monotonic()
    try:
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            session_store.refresh_session(opened.session_id, str(root), deadline_monotonic=deadline)
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join(timeout=2.0)
    assert not holder.is_alive()


class _Connection:
    def __init__(self) -> None:
        self.timeouts: list[float | None] = []
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        self.timeouts.append(timeout)

    def close(self) -> None:
        self.closed = True


def test_request_line_reads_each_chunk_with_remaining_total_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(session_daemon_deadlines, "monotonic", lambda: clock["now"])

    class _TrickleReader:
        def read1(self, _size: int) -> bytes:
            clock["now"] += 10.0
            return b"x"

        def readline(self, _size: int = -1) -> bytes:
            pytest.fail("request-line reader must use bounded chunks")

    connection = _Connection()
    line = session_daemon_deadlines._read_bounded_request_line(
        _TrickleReader(), max_bytes=16, deadline=130.0, connection=connection
    )

    assert line is None
    assert connection.timeouts == [30.0, 20.0, 10.0]


def test_handler_frame_and_work_deadlines_use_admission_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    server = session_daemon._ThreadedSessionDaemon(project, ("127.0.0.1", 0), token="tok")
    connection = _Connection()
    admitted_at = 100.0
    server._handler_admissions[connection] = admitted_at
    server._handler_deadlines[connection] = admitted_at + 30.0
    clock = {"now": 125.0}
    monkeypatch.setattr(session_daemon, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_daemon_deadlines, "monotonic", lambda: clock["now"])
    handler = session_daemon._SessionDaemonHandler.__new__(session_daemon._SessionDaemonHandler)
    handler.server = server
    handler.request = connection
    handler.connection = connection
    handler.rfile = BytesIO(b'{"command":"stats","token":"tok"}\n')
    handler.wfile = BytesIO()

    try:
        session_daemon._SessionDaemonHandler.handle(handler)
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert response["ok"] is True
        assert connection.timeouts == [5.0, 35.0]
    finally:
        server.server_close()


def test_warm_work_keeps_sixty_second_budget_after_thirty_second_frame_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    server = session_daemon._ThreadedSessionDaemon(project, ("127.0.0.1", 0), token="tok")
    connection = _Connection()
    admitted_at = 100.0
    server._handler_admissions[connection] = admitted_at
    server._handler_deadlines[connection] = admitted_at + 30.0
    clock = {"now": admitted_at}
    monkeypatch.setattr(session_daemon, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_daemon_deadlines, "monotonic", lambda: clock["now"])

    class _LateFrameReader:
        def read1(self, _size: int) -> bytes:
            clock["now"] = admitted_at + 29.0
            return b'{"command":"context_render","session_id":"session-id","token":"tok"}\n'

        def readline(self, _size: int = -1) -> bytes:
            pytest.fail("request-line reader must use bounded chunks")

    deadlines: list[float | None] = []

    def load_payload(
        _cache: object, _session_id: str, _path: str, *, deadline_monotonic: float | None = None
    ) -> tuple[dict[str, str], str]:
        deadlines.append(deadline_monotonic)
        return {"created_at": "now"}, "warm"

    def serve_request(**kwargs: object) -> tuple[dict[str, str], str]:
        deadline = kwargs["deadline_monotonic"]
        assert isinstance(deadline, float)
        deadlines.append(deadline)
        clock["now"] = admitted_at + 31.0
        return {"session_id": "session-id"}, "bypass"

    monkeypatch.setattr(session_daemon, "_load_payload_with_status_retry", load_payload)
    monkeypatch.setattr(session_daemon, "_serve_daemon_response_with_cache", serve_request)
    server.demand_metrics.record = lambda **_kwargs: None
    handler = session_daemon._SessionDaemonHandler.__new__(session_daemon._SessionDaemonHandler)
    handler.server = server
    handler.request = connection
    handler.connection = connection
    handler.rfile = _LateFrameReader()
    handler.wfile = BytesIO()

    try:
        session_daemon._SessionDaemonHandler.handle(handler)
        assert not connection.closed
        response = json.loads(handler.wfile.getvalue().decode("utf-8"))
        assert response["session_id"] == "session-id"
        assert deadlines == [admitted_at + 60.0, admitted_at + 60.0]
        assert connection.timeouts == [30.0, 29.0]
    finally:
        server.server_close()


def test_handler_closes_connection_when_work_finishes_after_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    server = session_daemon._ThreadedSessionDaemon(project, ("127.0.0.1", 0), token="tok")
    connection = _Connection()
    server._handler_admissions[connection] = 100.0
    server._handler_deadlines[connection] = 130.0
    clock = {"now": 129.0}
    monkeypatch.setattr(session_daemon, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(session_daemon_deadlines, "monotonic", lambda: clock["now"])

    def _expire_during_ping(*_args: object) -> dict[str, str]:
        clock["now"] = 160.0
        return {"proof": "ok"}

    monkeypatch.setattr(session_daemon, "_ping_proof_fields", _expire_during_ping)
    handler = session_daemon._SessionDaemonHandler.__new__(session_daemon._SessionDaemonHandler)
    handler.server = server
    handler.request = connection
    handler.connection = connection
    handler.rfile = BytesIO(b'{"command":"ping","token":"tok"}\n')
    handler.wfile = BytesIO()

    try:
        session_daemon._SessionDaemonHandler.handle(handler)
        assert connection.closed
        assert handler.wfile.getvalue() == b""
        assert server.inflight_requests == 0
    finally:
        server.server_close()
