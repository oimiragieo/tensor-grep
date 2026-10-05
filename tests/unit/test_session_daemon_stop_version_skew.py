"""F-07: `tg session daemon stop` must stop a version-skewed daemon it can authenticate to,
and must not orphan a daemon it could not stop by deleting its daemon.json."""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon


def _publish(root: Path, host: str, port: int, *, package_version: str) -> None:
    session_daemon._write_daemon_metadata(
        root,
        {
            "version": 1,
            "root": str(root),
            "host": host,
            "port": port,
            "pid": 0,  # pid <= 0 is never terminated: the pid fallback is inert in these tests
            "started_at": "test",
            "token": "test-token",
            "package_version": package_version,
        },
    )


def test_stop_cooperatively_stops_a_version_skewed_daemon(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="test-token")

    def _serve() -> None:  # production-faithful: the listener CLOSES when serving ends (:2084 `with`)
        try:
            server.serve_forever(poll_interval=0.05)
        finally:
            server.server_close()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        _publish(root, str(host), int(port), package_version="0.0.0-stale-fixture")
        # premise: the probe rejects this daemon (that is the skew), so the old code never
        # sent it a stop request.
        assert session_daemon._probe_daemon(root) is None

        result = session_daemon.stop_session_daemon(str(root))

        assert result["stopped"] is True, result
        assert result["stop_method"] == "cooperative", result
        thread.join(5.0)
        assert not thread.is_alive(), "the skewed daemon is still serving"
        # The refused ping is the evidence here; stop_session_daemon then also calls the guarded
        # remove (a no-op if the daemon already removed its own file). The fixture's pid is 0, so
        # its identity may not resolve -- assert on the daemon, not the file, for this arm.
    finally:
        if thread.is_alive():
            server.shutdown()


def test_ineligible_remote_host_metadata_is_never_pid_killed_or_deleted(tmp_path: Path, monkeypatch: Any) -> None:
    # council wave-2b r1/r2: a planted non-loopback daemon.json must not reach the pid fallback
    root = tmp_path.resolve()
    calls: list[Any] = []
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: calls.append(md) or True)
    session_daemon._write_daemon_metadata(root, {
        "version": 1, "root": str(root), "host": "10.0.0.1", "port": 9, "pid": os.getpid(),
        "started_at": "test", "token": "t", "package_version": "0.0.0-stale"})
    result = session_daemon.stop_session_daemon(str(root))
    assert result["stop_method"] == "none", result
    assert calls == [], "pid fallback reached for planted remote-host metadata"
    assert session_daemon._read_daemon_metadata(root) is not None


@pytest.mark.parametrize("bad_port", [True, 1.5, 70000, 0, "8080"])
def test_malformed_port_is_ineligible_no_request_no_kill(tmp_path: Path, monkeypatch: Any, bad_port: Any) -> None:
    # council wave-2b r3: strict type/range check BEFORE any network request or pid fallback
    root = tmp_path.resolve()
    kills: list[Any] = []
    requests: list[Any] = []
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: kills.append(md) or True)
    monkeypatch.setattr(session_daemon, "_daemon_request", lambda *a, **k: requests.append(a) or {"ok": True})
    session_daemon._write_daemon_metadata(root, {
        "version": 1, "root": str(root), "host": "127.0.0.1", "port": bad_port, "pid": os.getpid(),
        "started_at": "t", "token": "t", "package_version": "0.0.0-stale"})
    result = session_daemon.stop_session_daemon(str(root))
    assert (result["stopped"], result["stop_method"]) == (False, "none"), result
    assert kills == [] and requests == []
    assert session_daemon._read_daemon_metadata(root) is not None


def test_other_roots_daemon_credentials_planted_here_are_not_stopped(tmp_path: Path, monkeypatch: Any) -> None:
    # council wave-2b r3: identity is PROVEN (wave-1 HMAC ping binds root), not inferred from creds
    root_a, root_b = (tmp_path / "a").resolve(), (tmp_path / "b").resolve()
    root_a.mkdir()
    root_b.mkdir()
    kills: list[Any] = []
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: kills.append(md) or True)
    server = session_daemon._ThreadedSessionDaemon(root_b, ("127.0.0.1", 0), token="tok-b")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        _publish(root_a, str(host), int(port), package_version="0.0.0-stale")  # A's file, B's endpoint
        session_daemon._write_daemon_metadata(root_a, {**session_daemon._read_daemon_metadata(root_a), "token": "tok-b"})
        result = session_daemon.stop_session_daemon(str(root_a))
        assert (result["stopped"], result["stop_method"]) == (False, "none"), result
        assert kills == []
        assert thread.is_alive(), "root B's daemon was stopped through root A's planted metadata"
        assert session_daemon._read_daemon_metadata(root_a) is not None
    finally:
        server.shutdown()
        server.server_close()


def test_no_daemon_reports_not_stopped(tmp_path: Path) -> None:  # stop-honesty contract
    result = session_daemon.stop_session_daemon(str(tmp_path.resolve()))
    assert (result["running"], result["stopped"], result["stop_method"]) == (False, False, "none")


def test_stop_keeps_daemon_json_when_a_listening_daemon_cannot_be_stopped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.6)
    with socket.socket() as silent:  # accepts connections (backlog) but never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(4)
        _publish(root, "127.0.0.1", silent.getsockname()[1], package_version="0.0.0-stale")

        result = session_daemon.stop_session_daemon(str(root))

        assert result["stopped"] is False, result
        assert session_daemon._read_daemon_metadata(root) is not None, (
            "daemon.json deleted although the daemon is still listening (orphaned)"
        )


def test_stop_cleans_metadata_of_a_dead_daemon(tmp_path: Path) -> None:
    # POSITIVE CONTROL: nothing is listening (connection refused) -> the stale file IS removed,
    # so keeping daemon.json above is specific to a daemon that is still reachable.
    root = tmp_path.resolve()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    _publish(root, "127.0.0.1", dead_port, package_version="0.0.0-stale")

    result = session_daemon.stop_session_daemon(str(root))

    assert result["running"] is False
    assert session_daemon._read_daemon_metadata(root) is None


def _skewed_serving_daemon(root: Path, monkeypatch: Any, *, metadata_pid: int) -> Any:
    """A real in-process daemon for `root` whose metadata is version-skewed and whose `stop` is ACKED
    but never takes effect (wedged shutdown). Returns (server, real_shutdown)."""
    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="test-token")
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    real_shutdown = server.shutdown
    monkeypatch.setattr(server, "shutdown", lambda: None)  # ack `stop`, keep serving
    host, port = server.server_address
    session_daemon._write_daemon_metadata(
        root,
        {
            "version": 1,
            "root": str(root),
            "host": str(host),
            "port": int(port),
            "pid": metadata_pid,
            "started_at": "test",
            "token": "test-token",
            "package_version": "0.0.0-stale-fixture",
        },
    )
    return server, real_shutdown


def test_verified_listener_that_acks_stop_but_keeps_serving_is_not_reported_stopped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # council wave-2b r4: a VERIFIED daemon we failed to stop is still running; metadata is kept.
    root = tmp_path.resolve()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.6)
    calls: list[Any] = []
    monkeypatch.setattr(
        session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: calls.append(md) or False
    )
    server, real_shutdown = _skewed_serving_daemon(root, monkeypatch, metadata_pid=0)
    try:
        result = session_daemon.stop_session_daemon(str(root))
        assert calls, "the proven-pid escalation was never attempted: the test is vacuous"
        assert (result["running"], result["stopped"], result["stop_method"]) == (
            True,
            False,
            "none",
        )
        assert session_daemon._read_daemon_metadata(root) is not None
    finally:
        real_shutdown()
        server.server_close()


def test_a_signal_delivered_to_a_verified_daemon_that_keeps_serving_is_not_a_stop(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # council wave-2b r25: signal delivery is not proof of shutdown; only a REFUSED connection is.
    root = tmp_path.resolve()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.6)
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: True)
    server, real_shutdown = _skewed_serving_daemon(root, monkeypatch, metadata_pid=0)
    try:
        result = session_daemon.stop_session_daemon(str(root))
        assert (result["running"], result["stopped"], result["stop_method"]) == (
            True,
            False,
            "none",
        )
        assert session_daemon._read_daemon_metadata(root) is not None
    finally:
        real_shutdown()
        server.server_close()


def test_escalation_for_a_skewed_daemon_targets_the_proven_pid_never_the_planted_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # council wave-2b r3/r24: the pid used for any fallback is the one in the SIGNED reply.
    root = tmp_path.resolve()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.6)
    targets: list[Any] = []
    monkeypatch.setattr(
        session_daemon,
        "_terminate_daemon_by_pid",
        lambda md, **_k: targets.append(md["pid"]) or False,
    )
    planted = os.getpid() + 7
    server, real_shutdown = _skewed_serving_daemon(root, monkeypatch, metadata_pid=planted)
    try:
        session_daemon.stop_session_daemon(str(root))
        assert targets == [os.getpid()], targets  # the in-process daemon's own (proven) pid
        assert planted not in targets
    finally:
        real_shutdown()
        server.server_close()
