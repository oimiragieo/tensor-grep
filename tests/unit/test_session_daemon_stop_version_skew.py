"""F-07: `tg session daemon stop` must stop a version-skewed daemon it can authenticate to,
and must not orphan a daemon it could not stop by deleting its daemon.json."""

from __future__ import annotations

import json
import os
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon
from tensor_grep.cli import session_daemon_trust as trust


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


def test_stop_cooperatively_stops_a_version_skewed_daemon(tmp_path: Path, monkeypatch: Any) -> None:
    # The daemon SIGNS the version it runs; the skew is a genuinely different signed version
    # (daemon.json's package_version alone is repo-controlled and proves nothing).
    monkeypatch.setattr(trust, "_daemon_running_version", lambda: "0.0.0-stale-fixture")
    root = tmp_path.resolve()
    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="test-token")

    def _serve() -> (
        None
    ):  # production-faithful: the listener CLOSES when serving ends (:2084 `with`)
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


def test_ineligible_remote_host_metadata_is_never_pid_killed_or_deleted(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # council wave-2b r1/r2: a planted non-loopback daemon.json must not reach the pid fallback
    root = tmp_path.resolve()
    calls: list[Any] = []
    monkeypatch.setattr(
        session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: calls.append(md) or True
    )
    session_daemon._write_daemon_metadata(
        root,
        {
            "version": 1,
            "root": str(root),
            "host": "10.0.0.1",
            "port": 9,
            "pid": os.getpid(),
            "started_at": "test",
            "token": "t",
            "package_version": "0.0.0-stale",
        },
    )
    result = session_daemon.stop_session_daemon(str(root))
    assert result["stop_method"] == "none", result
    assert calls == [], "pid fallback reached for planted remote-host metadata"
    assert session_daemon._read_daemon_metadata(root) is not None


@pytest.mark.parametrize("bad_port", [True, 1.5, 70000, 0, "8080"])
def test_malformed_port_is_ineligible_no_request_no_kill(
    tmp_path: Path, monkeypatch: Any, bad_port: Any
) -> None:
    # council wave-2b r3: strict type/range check BEFORE any network request or pid fallback
    root = tmp_path.resolve()
    kills: list[Any] = []
    requests: list[Any] = []
    monkeypatch.setattr(
        session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: kills.append(md) or True
    )
    monkeypatch.setattr(
        session_daemon, "_daemon_request", lambda *a, **k: requests.append(a) or {"ok": True}
    )
    session_daemon._write_daemon_metadata(
        root,
        {
            "version": 1,
            "root": str(root),
            "host": "127.0.0.1",
            "port": bad_port,
            "pid": os.getpid(),
            "started_at": "t",
            "token": "t",
            "package_version": "0.0.0-stale",
        },
    )
    result = session_daemon.stop_session_daemon(str(root))
    assert (result["stopped"], result["stop_method"]) == (False, "none"), result
    assert kills == [] and requests == []
    assert session_daemon._read_daemon_metadata(root) is not None


def test_other_roots_daemon_credentials_planted_here_are_not_stopped(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # council wave-2b r3: identity is PROVEN (wave-1 HMAC ping binds root), not inferred from creds
    root_a, root_b = (tmp_path / "a").resolve(), (tmp_path / "b").resolve()
    root_a.mkdir()
    root_b.mkdir()
    kills: list[Any] = []
    monkeypatch.setattr(
        session_daemon, "_terminate_daemon_by_pid", lambda md, **_k: kills.append(md) or True
    )
    server = session_daemon._ThreadedSessionDaemon(root_b, ("127.0.0.1", 0), token="tok-b")
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        host, port = server.server_address
        _publish(
            root_a, str(host), int(port), package_version="0.0.0-stale"
        )  # A's file, B's endpoint
        session_daemon._write_daemon_metadata(
            root_a, {**session_daemon._read_daemon_metadata(root_a), "token": "tok-b"}
        )
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
    monkeypatch.setattr(trust, "_daemon_running_version", lambda: "0.0.0-stale-fixture")
    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="test-token")
    ready = threading.Event()
    # This proves the service loop ran; it is not an authenticated ping.
    monkeypatch.setattr(server, "service_actions", ready.set)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    if not ready.wait(session_daemon._DAEMON_START_TIMEOUT_SECONDS):
        stopper = threading.Thread(target=server.shutdown, daemon=True)
        stopper.start()
        server.server_close()
        thread.join(timeout=1)
        stopper.join(timeout=1)
        pytest.fail(
            "daemon listener did not reach its serving loop; "
            f"server_thread_alive={thread.is_alive()} stopper_thread_alive={stopper.is_alive()}"
        )
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


def test_listener_readiness_timeout_requests_bounded_cleanup(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.stopped = threading.Event()
            self.closed = False

        def service_actions(self) -> None:
            pass

        def serve_forever(self, *, poll_interval: float) -> None:
            self.stopped.wait(5)

        def shutdown(self) -> None:
            self.stopped.set()

        def server_close(self) -> None:
            self.closed = True

    fake = FakeServer()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(session_daemon, "_ThreadedSessionDaemon", lambda *a, **k: fake)
    with pytest.raises(pytest.fail.Exception, match="did not reach its serving loop"):
        _skewed_serving_daemon(tmp_path.resolve(), monkeypatch, metadata_pid=0)
    assert fake.stopped.is_set() and fake.closed


def test_listener_readiness_timeout_reports_unresponsive_cleanup(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class FakeServer:
        def __init__(self) -> None:
            self.release = threading.Event()
            self.server_done = threading.Event()
            self.stopper_done = threading.Event()

        def service_actions(self) -> None:
            pass

        def serve_forever(self, *, poll_interval: float) -> None:
            self.release.wait(5)
            self.server_done.set()

        def shutdown(self) -> None:
            self.release.wait(5)
            self.stopper_done.set()

        def server_close(self) -> None:
            pass

    fake = FakeServer()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(session_daemon, "_ThreadedSessionDaemon", lambda *a, **k: fake)
    try:
        with pytest.raises(
            pytest.fail.Exception, match="server_thread_alive=True stopper_thread_alive=True"
        ):
            _skewed_serving_daemon(tmp_path.resolve(), monkeypatch, metadata_pid=0)
    finally:
        fake.release.set()
        assert fake.server_done.wait(1) and fake.stopper_done.wait(1)


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
    tmp_path: Path,
    monkeypatch: Any,
    request: pytest.FixtureRequest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.unit.test_session_daemon_signed_version import _StopTrace

    # council wave-2b r3/r24: the pid used for any fallback is the one in the SIGNED reply.
    root = tmp_path.resolve()
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0.6)
    targets: list[Any] = []
    stages: list[tuple[str, object]] = []

    def observe(name: str) -> None:
        original = getattr(session_daemon, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            if len(stages) < 32:
                if name == "_probe_daemon":
                    detail: object = result is not None
                else:
                    known = {
                        "absent",
                        "ineligible",
                        "not_listening",
                        "unverified",
                        "current_version",
                        "cooperative",
                        "unresponsive",
                        "pid",
                    }
                    detail = (result[0] is not None, result[1] if result[1] in known else "other")
                stages.append((name, detail))
            return result

        monkeypatch.setattr(session_daemon, name, wrapped)

    for name in ("_probe_daemon", "_stop_rejected_daemon"):
        observe(name)
    monkeypatch.setattr(
        session_daemon,
        "_terminate_daemon_by_pid",
        lambda md, **_k: targets.append(md["pid"]) or False,
    )
    planted = os.getpid() + 7
    trace = _StopTrace(request.node.nodeid)
    observer_errors: list[BaseException] = []
    callbacks: queue.Queue[threading.Event] = queue.Queue(maxsize=128)
    original_add = trace.add

    def observe_safely(action: Any) -> Any:
        try:
            return action()
        except BaseException as error:
            if len(observer_errors) < 32:
                observer_errors.append(error)
            return None

    def record(name: str, **fields: Any) -> None:
        observe_safely(lambda: original_add(name, **fields))

    def wrap_call(original: Any, label: str) -> Any:
        def observed(*args: Any, **kwargs: Any) -> Any:
            done = threading.Event()
            observe_safely(lambda: callbacks.put_nowait(done))
            started = observe_safely(time.monotonic)
            record(f"{label}_begin")
            try:
                try:
                    value = original(*args, **kwargs)
                except BaseException as error:
                    fields = observe_safely(lambda: trace._error_fields(error)) or {}
                    record(f"{label}_error", **fields)
                    raise
                elapsed = observe_safely(lambda: time.monotonic() - started)
                record(f"{label}_end", available=value is not None, elapsed=elapsed)
                return value
            finally:
                observe_safely(done.set)

        return observed

    server, real_shutdown = _skewed_serving_daemon(root, monkeypatch, metadata_pid=planted)
    result: object = None
    primary: BaseException | None = None
    primary_tb: Any = None
    cleanup_errors: list[BaseException] = []
    try:
        trace.endpoint = (str(server.server_address[0]), int(server.server_address[1]))
        monkeypatch.setattr(trace, "add", record)
        trace.install_protocol_spies(monkeypatch)
        for name in ("_read_user_secret", "_load_or_create_user_secret"):
            monkeypatch.setattr(trust, name, wrap_call(getattr(trust, name), name))
        monkeypatch.setattr(
            session_daemon,
            "_ping_proof_fields",
            wrap_call(session_daemon._ping_proof_fields, "fields"),
        )
        result = session_daemon.stop_session_daemon(str(root))
        diagnostics = {key: result.get(key) for key in ("running", "stopped", "stop_method")}
        assert targets == [os.getpid()], (targets, diagnostics, stages)
        assert planted not in targets
        if not observer_errors:
            assert any(event["event"] == "_read_user_secret_end" for event in trace.events)
            assert not any(event["event"] == "signal_seam_reached" for event in trace.events)
    except BaseException as error:
        primary, primary_tb = error, error.__traceback__
    finally:
        before = observe_safely(trace.event_snapshot)
        projection = observe_safely(lambda: trace.serialize_result(result))
        try:
            real_shutdown()
        except BaseException as error:
            cleanup_errors.append(error)
        finally:
            try:
                server.server_close()
            except BaseException as error:
                cleanup_errors.append(error)

        def wait_callbacks() -> bool:
            # shortcut: entered observation callbacks only; track request threads for lifecycle claims.
            deadline = time.monotonic() + 5.0
            complete = True
            while True:
                try:
                    done = callbacks.get_nowait()
                except queue.Empty:
                    return complete
                complete = done.wait(max(0.0, deadline - time.monotonic())) and complete

        callbacks_done = observe_safely(wait_callbacks) is True
        snapshot = observe_safely(trace.event_snapshot)
        incomplete = bool(
            observer_errors
            or cleanup_errors
            or not callbacks_done
            or not snapshot
            or snapshot["incomplete"]
            or not projection
            or projection["result_serialization"] != "ok"
        )
        payload = {
            "node": request.node.nodeid,
            "trace": snapshot,
            "before_cleanup": before,
            "result_before_cleanup": projection,
            "registered_callbacks_complete": callbacks_done,
            "callback_wait_scope": "registered wrappers only; request-thread settlement unverified",
            "diagnostic_incomplete": incomplete,
            "failure_type": type(primary).__name__ if primary else None,
            "cleanup_error_types": [type(error).__name__ for error in cleanup_errors],
            "observer_error_types": [type(error).__name__ for error in observer_errors],
        }

        def emit() -> None:
            with capsys.disabled():
                print(
                    "DAEMON_ESCALATION_DIAGNOSTIC=" + json.dumps(payload, sort_keys=True),
                    flush=True,
                )

        observe_safely(emit)
    if primary is not None:
        for error in cleanup_errors + observer_errors:
            primary.add_note(f"diagnostic secondary error: {type(error).__name__}")
        raise primary.with_traceback(primary_tb)
    if cleanup_errors or observer_errors:
        raise (cleanup_errors + observer_errors)[0]
    assert not incomplete, "daemon escalation diagnostic is incomplete"


@pytest.mark.parametrize("fail_primary", [False, True])
def test_escalation_diagnostic_preserves_observer_and_primary_failures(
    tmp_path: Path,
    monkeypatch: Any,
    request: pytest.FixtureRequest,
    capsys: pytest.CaptureFixture[str],
    fail_primary: bool,
) -> None:
    from tests.unit.test_session_daemon_signed_version import _StopTrace

    def fail_recording(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("observer-control")

    monkeypatch.setattr(_StopTrace, "add", fail_recording)
    if fail_primary:
        monkeypatch.setattr(session_daemon, "stop_session_daemon", lambda *_a: {"running": True})
    expected = AssertionError if fail_primary else RuntimeError
    with pytest.raises(expected) as caught:
        test_escalation_for_a_skewed_daemon_targets_the_proven_pid_never_the_planted_one(
            tmp_path, monkeypatch, request, capsys
        )
    if fail_primary:
        assert any("RuntimeError" in note for note in caught.value.__notes__)
    else:
        assert "observer-control" in str(caught.value)
