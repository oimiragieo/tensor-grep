"""The pid used to terminate a daemon must be the one PROVEN by the HMAC ping reply (PR #1197).

``_probe_daemon`` used to verify the signed reply (nonce|pid|root|port) and then return the
ORIGINAL metadata, whose ``pid`` was never verified. Root A's daemon.json could carry A's valid
endpoint and token but name root B's daemon pid; A's stop would then escalate to
``_terminate_daemon_by_pid(metadata)`` and kill B.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.3)  # keep escalation tests fast


@contextmanager
def _daemon(root: Path, *, metadata_pid: int, package_version: str | None = None) -> Iterator[Any]:
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    real_shutdown = server.shutdown

    def _serve_then_close() -> None:
        # A real daemon is a PROCESS: when serve_forever returns the process exits and its listening
        # socket disappears (connections are REFUSED). In this in-process stand-in the socket would
        # otherwise stay bound, and on Linux a listening-but-not-accepting socket still completes
        # connects via the kernel backlog (probed on Linux), so a cooperative stop could never be
        # confirmed there. Close the listener when the loop ends, as process exit does.
        try:
            server.serve_forever()
        finally:
            server.server_close()

    thread = threading.Thread(target=_serve_then_close, daemon=True)
    thread.start()
    server._test_real_shutdown = real_shutdown  # type: ignore[attr-defined]
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": package_version or _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": int(server.server_address[1]),
            "pid": metadata_pid,
            "started_at": "x",
            "token": "tok",
        },
    )
    try:
        yield server
    finally:
        real_shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_probe_returns_the_proven_pid_not_the_planted_one(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=999_999):
        proven = sd._probe_daemon(root)
    assert proven is not None
    assert proven["pid"] == os.getpid()  # the in-process daemon's own pid, signed in the reply


def test_stop_never_signals_a_planted_pid_and_reports_an_unconfirmed_stop_honestly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with _daemon(root, metadata_pid=victim.pid) as server:
            # The daemon acks "stop" but keeps serving (a wedged / lying shutdown).
            monkeypatch.setattr(server, "shutdown", lambda: None)
            calls: list[Any] = []

            def _record(metadata: Any, **_k: Any) -> bool:
                calls.append(dict(metadata) if metadata else metadata)
                return True  # "a terminate signal was delivered" -- but it never took effect

            monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _record)
            result = sd.stop_session_daemon(str(root))
            assert calls, "the escalation was never attempted: the test is vacuous"
            assert all(c["pid"] != victim.pid for c in calls), calls
            assert all(c["pid"] == os.getpid() for c in calls), calls
            assert victim.poll() is None  # the unrelated process is untouched
            assert result["running"] is True
            assert result["stopped"] is False
            assert result["stop_method"] == "none"
            assert sd._read_daemon_metadata(root) is not None  # daemon.json is KEPT
    finally:
        victim.kill()
        victim.wait(timeout=5)


def _close_listener(server: Any) -> None:
    server._test_real_shutdown()
    server.server_close()


def test_stale_branch_terminate_true_while_the_listener_still_serves_is_not_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A package_version mismatch makes the probe return None (stale branch) although the
    # endpoint is alive. A DELIVERED signal is not a stopped daemon: only a refused connection is.
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid(), package_version="0.0.0-stale"):
        assert sd._probe_daemon(root) is None
        calls: list[Any] = []
        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda m, **_k: calls.append(m) or True)
        result = sd.stop_session_daemon(str(root))
        assert calls, "no pid escalation happened: the test is vacuous"
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert sd._read_daemon_metadata(root) is not None  # daemon.json kept


def test_stale_branch_control_terminate_true_then_listener_closed_is_a_pid_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid(), package_version="0.0.0-stale") as server:

        def _terminate_and_die(_m: Any, **_k: Any) -> bool:
            _close_listener(server)
            return True

        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _terminate_and_die)
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None  # identity-guarded removal


def test_probe_branch_control_terminate_true_then_listener_closed_is_a_pid_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    with _daemon(root, metadata_pid=os.getpid()) as server:
        monkeypatch.setattr(server, "shutdown", lambda: None)  # ack "stop", keep serving

        def _terminate_and_die(_m: Any, **_k: Any) -> bool:
            _close_listener(server)
            return True

        monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _terminate_and_die)
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None


def test_cooperative_stop_control_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # POSITIVE CONTROL: a normal stop (daemon really shuts down) is confirmed, not escalated.
    root = tmp_path.resolve()
    calls: list[Any] = []
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda m, **_k: calls.append(m) or False)
    with _daemon(root, metadata_pid=os.getpid()):
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "cooperative"
        assert calls == []
        assert sd._read_daemon_metadata(root) is None
