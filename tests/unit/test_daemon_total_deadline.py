from __future__ import annotations

import io
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_store


def _serve(server: sd._ThreadedSessionDaemon) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _request(server: sd._ThreadedSessionDaemon, request: dict[str, Any]) -> dict[str, Any]:
    with socket.create_connection(server.server_address, timeout=2) as client:
        client.settimeout(2)
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        with client.makefile("rb") as response:
            return json.loads(response.readline())


def test_health_payload_deadline_option_preserves_default_shape(tmp_path: Path) -> None:
    payload = {
        "root": str(tmp_path),
        "repo_map": {"path": str(tmp_path), "files": [], "symbols": []},
        "snapshot": [],
    }
    default = session_store._session_health_payload("session-x", payload)
    explicit_none = session_store._session_health_payload(
        "session-x", payload, deadline_monotonic=None
    )
    assert default == explicit_none


def test_bounded_frame_reader_handles_eof_oversize_and_valid_lines() -> None:
    read = sd._read_bounded_request_line
    assert read(io.BytesIO(b""), max_bytes=16) is None
    assert read(io.BytesIO(b'{"ok":true}'), max_bytes=16) == '{"ok":true}'
    assert read(io.BytesIO(b'{"ok":true}\n'), max_bytes=16) == '{"ok":true}'
    assert read(io.BytesIO(b"12345\n"), max_bytes=5) is None


def test_valid_frame_restores_handler_timeout_before_response_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sd._SessionDaemonHandler, "timeout", 1.0)

    def finish_at_deadline(rfile: Any, *args: Any, **kwargs: Any) -> str:
        del rfile, args
        kwargs["connection"].settimeout(0.000001)
        return '{"command":"ping","token":"secret"}'

    monkeypatch.setattr(sd, "_read_bounded_request_line", finish_at_deadline)
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    try:
        assert _request(server, {"command": "ping", "token": "secret"})["ok"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_daemon_keeps_authentication_before_dispatch(tmp_path: Path) -> None:
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    try:
        denied = _request(server, {"command": "ping"})
        assert denied["error"]["code"] == "unauthorized"
        assert (
            _request(server, {"command": "ping", "token": "wrong"})["error"]["code"]
            == "unauthorized"
        )
        assert _request(server, {"command": "ping", "token": "secret"})["ok"] is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_slow_drip_hits_total_deadline_and_releases_handler_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeout = 0.2
    monkeypatch.setattr(sd, "_DAEMON_HANDLER_TIMEOUT_SECONDS", timeout)
    monkeypatch.setattr(sd._SessionDaemonHandler, "timeout", timeout)
    monkeypatch.setattr(sd._ThreadedSessionDaemon, "_max_concurrent_connections", 1)
    entered_reader = threading.Event()
    original_read = sd._read_bounded_request_line

    def observe_read(rfile: Any, *args: Any, **kwargs: Any) -> str | None:
        entered_reader.set()
        return original_read(rfile, *args, **kwargs)

    monkeypatch.setattr(sd, "_read_bounded_request_line", observe_read)
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    client = socket.create_connection(server.server_address, timeout=1)
    client.settimeout(0.8)
    stop_drip = threading.Event()
    drip_done = threading.Event()
    payload = b" " * 180 + b'{"command":"ping","token":"secret"}\n'

    def drip() -> None:
        try:
            for byte in payload[1:]:
                if stop_drip.is_set():
                    break
                client.sendall(bytes((byte,)))
                time.sleep(0.01)
        except OSError:
            pass
        finally:
            drip_done.set()

    started = time.monotonic()
    try:
        client.sendall(payload[:1])
        assert entered_reader.wait(1)
        sender = threading.Thread(target=drip, daemon=True)
        sender.start()
        assert client.recv(1) == b""
        elapsed = time.monotonic() - started
        assert elapsed <= timeout + 0.35
        stop_drip.set()
        assert drip_done.wait(1)
        sender.join(timeout=1)

        assert server._connection_slots.acquire(timeout=1)
        server._connection_slots.release()
        assert _request(server, {"command": "ping", "token": "secret"})["ok"] is True
    finally:
        stop_drip.set()
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_deadline_starts_before_worker_thread_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timeout = 0.15
    monkeypatch.setattr(sd, "_DAEMON_HANDLER_TIMEOUT_SECONDS", timeout)
    worker_started = threading.Event()
    release_worker = threading.Event()
    original_process_request_thread = sd._ThreadedSessionDaemon.process_request_thread
    gate_first_worker = True
    gate_lock = threading.Lock()

    def delay_first_worker(
        server: sd._ThreadedSessionDaemon, request: Any, client_address: Any
    ) -> None:
        nonlocal gate_first_worker
        with gate_lock:
            gated = gate_first_worker
            gate_first_worker = False
        if gated:
            worker_started.set()
            release_worker.wait(timeout=1)
        original_process_request_thread(server, request, client_address)

    monkeypatch.setattr(sd._ThreadedSessionDaemon, "process_request_thread", delay_first_worker)
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    client = socket.create_connection(server.server_address, timeout=1)
    client.settimeout(1)
    try:
        client.sendall(b'{"command":"ping","token":"secret"}\n')
        assert worker_started.wait(1)
        assert not release_worker.wait(timeout + 0.1)
        release_worker.set()
        assert client.recv(1) == b""
    finally:
        release_worker.set()
        client.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_implicit_session_open_uses_work_deadline_from_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = 0.5
    monkeypatch.setattr(sd, "WARM_DAEMON_DEFAULT_DEADLINE_SECONDS", budget)
    captured: dict[str, float] = {}
    server: sd._ThreadedSessionDaemon

    def open_implicit_session(
        path: str,
        *,
        max_repo_files: int | None = None,
        deadline_monotonic: float | None = None,
    ) -> Any:
        del path, max_repo_files
        with server._handler_deadline_lock:
            admitted_at = next(iter(server._handler_admissions.values()))
        captured["expected"] = admitted_at + budget
        assert deadline_monotonic == captured["expected"]
        return type("OpenedSession", (), {"session_id": "implicit-session"})()

    monkeypatch.setattr(sd, "open_session", open_implicit_session)
    monkeypatch.setattr(
        sd,
        "_load_payload_with_status_retry",
        lambda cache, session_id, path, *, deadline_monotonic: (
            {"root": path, "repo_map": {}},
            "miss",
        ),
    )

    def serve_with_deadline(**kwargs: Any) -> tuple[dict[str, Any], str]:
        assert kwargs["deadline_monotonic"] == captured["expected"]
        return {"ok": True}, "miss"

    monkeypatch.setattr(sd, "_serve_daemon_response_with_cache", serve_with_deadline)
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    try:
        response = _request(server, {"command": "context_render", "token": "secret"})
        assert response["ok"] is True
        assert captured["expected"] > time.monotonic()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_health_freshness_check_uses_work_deadline_from_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = 0.5
    monkeypatch.setattr(sd, "WARM_DAEMON_DEFAULT_DEADLINE_SECONDS", budget)
    captured: dict[str, float] = {}
    observed: list[float | None] = []
    server: sd._ThreadedSessionDaemon

    def load_payload(
        cache: Any,
        session_id: str,
        path: str,
        *,
        deadline_monotonic: float | None,
    ) -> tuple[dict[str, Any], str]:
        del cache, session_id
        with server._handler_deadline_lock:
            admitted_at = next(iter(server._handler_admissions.values()))
        captured["expected"] = admitted_at + budget
        assert deadline_monotonic == captured["expected"]
        return {"root": path, "repo_map": {}, "snapshot": []}, "miss"

    def observe_staleness(
        payload: dict[str, Any], *, detect_added_files: bool, deadline_monotonic: float | None
    ) -> None:
        del payload, detect_added_files
        observed.append(deadline_monotonic)
        return None

    monkeypatch.setattr(sd, "_load_payload_with_status_retry", load_payload)
    monkeypatch.setattr(session_store, "_stale_changeset", observe_staleness)
    server = sd._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0), token="secret")
    thread = _serve(server)
    try:
        response = _request(
            server,
            {"command": "health", "session_id": "session-existing", "token": "secret"},
        )
        assert response["ok"] is True
        assert observed == [captured["expected"]]
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
