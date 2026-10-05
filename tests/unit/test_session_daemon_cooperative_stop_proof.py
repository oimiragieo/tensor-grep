"""``stop_method "cooperative"`` needs PROOF of shutdown (PR #1197 round-2 audit).

A cooperative stop is reported only if the stop reply was ``ok: true`` AND the endpoint then REFUSES
connections. A failed/unauthorized stop reply, or a failing ping, is not evidence that the daemon
ended: with the listener still open the result must be ``running True / stopped False`` and the
metadata kept (or, if the proven pid can be signalled and then the endpoint refuses, ``pid``).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.5)


def _serve(root: Path) -> tuple[Any, Any]:
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    real_shutdown = server.shutdown
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": int(server.server_address[1]),
            "pid": 1,
            "started_at": "x",
            "token": "tok",
        },
    )
    return server, real_shutdown


def test_unauthorized_stop_reply_with_the_listener_still_open_is_not_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    server, real_shutdown = _serve(root)
    real_request = sd._daemon_request
    pings = {"n": 0}

    def _request(host: str, port: int, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if request.get("command") == "stop":
            return {"ok": False, "error": {"code": "unauthorized"}}
        if request.get("command") == "ping":
            pings["n"] += 1
            if pings["n"] > 1:  # the initial signed probe succeeds, every later ping fails
                raise OSError("ping failed")
        return real_request(host, port, request, **kwargs)

    monkeypatch.setattr(sd, "_daemon_request", _request)
    try:
        result = sd.stop_session_daemon(str(root))
        assert pings["n"] >= 2, "the later ping never happened: the test is vacuous"
        # the listener is genuinely still open
        import socket

        socket.create_connection(("127.0.0.1", int(server.server_address[1])), timeout=2).close()
        assert result["stop_method"] == "none"
        assert result["stopped"] is False
        assert result["running"] is True
        assert sd._read_daemon_metadata(root) is not None  # metadata kept
    finally:
        real_shutdown()
        server.server_close()


def test_ok_stop_reply_but_listener_still_open_is_not_cooperative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    server, real_shutdown = _serve(root)
    monkeypatch.setattr(server, "shutdown", lambda: None)  # acks ok, keeps the socket open
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda m, **k: False)
    try:
        result = sd.stop_session_daemon(str(root))
        assert result["stop_method"] != "cooperative"
        assert result["stopped"] is False
        assert result["running"] is True
        assert sd._read_daemon_metadata(root) is not None
    finally:
        real_shutdown()
        server.server_close()


def test_a_real_daemon_process_stops_cooperatively_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONTROL: a REAL daemon process (launched like ``_spawn_daemon_subprocess``) that acks the
    stop and exits is reported ``cooperative`` and its metadata is removed."""
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 20.0)
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    sd._spawn_daemon_subprocess(root)
    live = None
    deadline = time.time() + 60
    while time.time() < deadline and live is None:
        live = sd._probe_daemon(root)
        time.sleep(0.2)
    assert live is not None, "the real daemon never became reachable"
    pid = int(live["pid"])
    try:
        result = sd.stop_session_daemon(str(root))
        assert result["stop_method"] == "cooperative"
        assert result["stopped"] is True
        assert result["running"] is False
        assert sd._read_daemon_metadata(root) is None
    finally:
        try:
            import psutil

            psutil.Process(pid).kill()
        except Exception:
            pass
