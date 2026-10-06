"""The version-skew stop trusts only the version the daemon SIGNED in its ping reply.

``daemon.json`` is repo-controlled: a current-version daemon whose metadata claims an old
``package_version`` must NOT be stopped through the skew path (Codex audit of PR #1207).
"""

from __future__ import annotations

import json
import platform
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon, session_daemon_stop
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_TRACE_PREFIX = "DAEMON_STOP_DIAGNOSTIC="
_TRACE_CAP = 128
_RESULT_FIELDS = {
    "version",
    "root",
    "running",
    "stopped",
    "stop_method",
    "proof",
    "pid_reuse_guard",
    "unconfirmed_reason",
    "endpoint_ok",
    "stop_reply_error",
    "error",
}


class _StopTrace:
    """Test-only bounded observations. Values are explicit primitives, never request payloads."""

    def __init__(self, node: str = "diagnostic-projection-control") -> None:
        self.node = node
        self._slots: list[dict[str, Any] | None] = [None] * _TRACE_CAP
        self._available: queue.SimpleQueue[int] = queue.SimpleQueue()
        for slot in range(_TRACE_CAP):
            self._available.put(slot)
        self.overflow = False
        self.local = threading.local()
        self.endpoint: tuple[str, int] | None = None
        self.close_entered = threading.Event()
        self.shutdown_held = threading.Event()
        self.shutdown_release: threading.Event | None = None
        self.shutdown_hold_expired = False
        self.pre_cleanup: dict[str, Any] | None = None
        self._shutdown_completions: queue.SimpleQueue[threading.Event] = queue.SimpleQueue()
        self.shutdown_completion_failed = False
        self.shutdown_completion_result: dict[str, Any] | None = None

    def add(self, name: str, **fields: Any) -> None:
        try:
            slot = self._available.get_nowait()
        except queue.Empty:
            self.overflow = True
            return
        safe = {
            key: value
            for key, value in fields.items()
            if value is None or type(value) in (bool, int, float, str)
        }
        self._slots[slot] = {
            "seq": slot + 1,
            "t": round(time.monotonic(), 6),
            "thread": threading.get_ident(),
            "event": name,
            **safe,
        }

    @property
    def events(self) -> list[dict[str, Any]]:
        return [event for event in self._slots.copy() if event is not None]

    def event_snapshot(self) -> dict[str, Any]:
        events = self.events
        # Read reservations after the slot copy: a concurrent new reservation can only make this
        # snapshot conservatively incomplete, never make a missing record look complete.
        outstanding = _TRACE_CAP - self._available.qsize() - len(events)
        unwaited_shutdowns = self._shutdown_completions.qsize()
        return {
            "events": events,
            "overflow": self.overflow,
            "cap": _TRACE_CAP,
            "outstanding_reservations": outstanding,
            "unwaited_shutdown_completions": unwaited_shutdowns,
            "incomplete": (
                self.overflow
                or outstanding != 0
                or self.shutdown_completion_failed
                or unwaited_shutdowns != 0
            ),
        }

    def wait_shutdown_completions(self) -> None:
        """Own entered wrappers only; one aggregate cleanup budget never resets per callback."""
        self.shutdown_completion_failed = True
        deadline = time.monotonic() + 5.0
        registered = completed = 0
        while True:
            try:
                completion = self._shutdown_completions.get_nowait()
            except queue.Empty:
                break
            registered += 1
            if completion.wait(max(0.0, deadline - time.monotonic())):
                completed += 1
        self.shutdown_completion_result = {
            "entered_wrappers": registered,
            "completed": completed,
            "cleanup_budget_seconds": 5.0,
            "timed_out": completed != registered,
        }
        self.shutdown_completion_failed = completed != registered
        if self.shutdown_completion_failed:
            raise AssertionError("owned shutdown observations did not complete")

    def stage(self) -> str:
        return getattr(self.local, "stage", "other")

    def set_stage(self, value: str) -> str:
        previous = self.stage()
        self.local.stage = value
        return previous

    @staticmethod
    def _error_fields(error: BaseException) -> dict[str, Any]:
        return {
            "exception": type(error).__name__,
            "errno": getattr(error, "errno", None),
            "winerror": getattr(error, "winerror", None),
        }

    @staticmethod
    def _present_fields(fields: object) -> dict[str, bool]:
        if not isinstance(fields, dict):
            return {"fields_is_dict": False}
        return {
            "fields_is_dict": True,
            "pid_present": "pid" in fields,
            "root_present": "root" in fields,
            "port_present": "port" in fields,
            "proof_present": "proof" in fields,
            "package_version_present": "package_version" in fields,
            "version_proof_present": "version_proof" in fields,
        }

    def install_protocol_spies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_request = session_daemon._daemon_request

        def request(host: str, port: int, body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            command = body.get("command")
            stage = (
                "rejected-stop-ping"
                if command == "ping" and self.stage() != "initial-probe"
                else ("initial-probe" if command == "ping" else "stop-ack")
            )
            previous = self.set_stage(stage)
            started = time.monotonic()
            self.add(
                "daemon_request_begin",
                command=command if isinstance(command, str) else "unknown",
                response_timeout=kwargs.get("response_timeout"),
                connect_timeout=kwargs.get("connect_timeout"),
                stage=stage,
            )
            try:
                try:
                    response = real_request(host, port, body, **kwargs)
                except BaseException as error:
                    self.add(
                        "daemon_request_error",
                        stage=stage,
                        elapsed=round(time.monotonic() - started, 6),
                        **self._error_fields(error),
                    )
                    raise
                self.add(
                    "daemon_request_end",
                    stage=stage,
                    elapsed=round(time.monotonic() - started, 6),
                    ok=response.get("ok") is True,
                    stopping=response.get("stopping") is True,
                )
                if command == "ping" and stage == "rejected-stop-ping":
                    self.add("rejected_stop_ping", ok=response.get("ok") is True)
                if (
                    command == "stop"
                    and response.get("ok") is True
                    and self.shutdown_release is not None
                ):
                    entered = self.shutdown_held.wait(1.5)
                    self.add("control_ack_waited_for_shutdown_hold", entered=entered)
                return response
            finally:
                self.set_stage(previous)

        monkeypatch.setattr(session_daemon, "_daemon_request", request)
        real_probe = session_daemon._probe_daemon

        def probe(root: Path) -> dict[str, Any] | None:
            previous = self.set_stage("initial-probe")
            started = time.monotonic()
            try:
                try:
                    result = real_probe(root)
                except BaseException as error:
                    self.add(
                        "probe_error",
                        elapsed=round(time.monotonic() - started, 6),
                        **self._error_fields(error),
                    )
                    raise
                self.add(
                    "probe_end",
                    elapsed=round(time.monotonic() - started, 6),
                    accepted=result is not None,
                )
                return result
            finally:
                self.set_stage(previous)

        monkeypatch.setattr(session_daemon, "_probe_daemon", probe)

        for module, name in (
            (session_daemon, "_verify_ping_reply"),
            (session_daemon_stop, "_verify_ping_reply"),
            (session_daemon_stop, "_signed_ping_version"),
        ):
            real = getattr(module, name)

            def wrap(real_fn: Any, label: str) -> Any:
                def observed(*args: Any, **kwargs: Any) -> Any:
                    started = time.monotonic()
                    try:
                        result = real_fn(*args, **kwargs)
                    except BaseException as error:
                        self.add(
                            f"{label}_error",
                            elapsed=round(time.monotonic() - started, 6),
                            **self._error_fields(error),
                        )
                        raise
                    if label == "signed_version":
                        ok, version = result
                        self.add(
                            label,
                            ok=ok is True,
                            version_ok=ok is True,
                            missing_version=version is None,
                            expected_version=version == _expected_tg_version(),
                        )
                    else:
                        self.add(
                            label,
                            verified=result is True,
                            elapsed=round(time.monotonic() - started, 6),
                        )
                    return result

                return observed

            label = (
                "signed_version"
                if name == "_signed_ping_version"
                else f"{module.__name__.rsplit('.', 1)[-1]}_verification"
            )
            monkeypatch.setattr(module, name, wrap(real, label))

        real_fields = session_daemon._ping_proof_fields

        def fields(*args: Any, **kwargs: Any) -> dict[str, Any]:
            started = time.monotonic()
            self.add("ping_proof_fields_entry")
            try:
                result = real_fields(*args, **kwargs)
            except BaseException as error:
                self.add(
                    "ping_proof_fields_error",
                    elapsed=round(time.monotonic() - started, 6),
                    **self._error_fields(error),
                )
                raise
            self.add(
                "ping_proof_fields",
                elapsed=round(time.monotonic() - started, 6),
                **self._present_fields(result),
            )
            return result

        monkeypatch.setattr(session_daemon, "_ping_proof_fields", fields)

        for module, name in (
            (session_daemon, "_await_endpoint_refused"),
            (session_daemon_stop, "_await_endpoint_refused"),
        ):
            real = getattr(module, name)

            def refusal_wrapper(real_fn: Any) -> Any:
                def observed(
                    host: object, port: object, timeout_seconds: float, *args: Any, **kwargs: Any
                ) -> bool:
                    previous = self.set_stage("refusal-poll")
                    started = time.monotonic()
                    self.add("refusal_poll_begin", deadline_seconds=timeout_seconds)
                    try:
                        try:
                            result = real_fn(host, port, timeout_seconds, *args, **kwargs)
                        except BaseException as error:
                            self.add(
                                "refusal_poll_error",
                                elapsed=round(time.monotonic() - started, 6),
                                **self._error_fields(error),
                            )
                            raise
                        self.add(
                            "refusal_poll_end",
                            elapsed=round(time.monotonic() - started, 6),
                            refused=result is True,
                        )
                        return result
                    finally:
                        self.set_stage(previous)

                return observed

            monkeypatch.setattr(module, name, refusal_wrapper(real))

        real_connect = socket.create_connection

        def connect(address: Any, *args: Any, **kwargs: Any) -> Any:
            if address != self.endpoint:
                return real_connect(address, *args, **kwargs)
            started = time.monotonic()
            stage = self.stage()
            self.add(
                "socket_attempt_begin",
                stage=stage,
                timeout=kwargs.get("timeout", args[0] if args else None),
            )
            try:
                connection = real_connect(address, *args, **kwargs)
            except BaseException as error:
                self.add(
                    "socket_attempt_error",
                    stage=stage,
                    elapsed=round(time.monotonic() - started, 6),
                    **self._error_fields(error),
                )
                raise
            self.add(
                "socket_attempt_connected",
                stage=stage,
                elapsed=round(time.monotonic() - started, 6),
            )
            return connection

        monkeypatch.setattr(socket, "create_connection", connect)

        real_terminate = session_daemon._terminate_identified

        def deny_signal(identity: Any) -> bool:
            self.add("signal_seam_reached", denied=True)
            return False

        monkeypatch.setattr(session_daemon, "_terminate_identified", deny_signal)
        self.add("signal_seam_installed", original_callable=callable(real_terminate))

    def serialize_result(self, result: object) -> dict[str, Any]:
        if not isinstance(result, dict) or not set(result).issubset(_RESULT_FIELDS):
            return {
                "result_serialization": "refused",
                "unexpected_field_count": len(result) if isinstance(result, dict) else -1,
            }
        if any(
            value is not None and type(value) not in (str, int, bool) for value in result.values()
        ):
            return {"result_serialization": "refused", "unexpected_value_type": True}
        safe = dict(result)
        for key in ("error", "stop_reply_error"):
            if key in safe:
                safe[key] = "redacted_exception_text"
        if "proof" in safe and safe["proof"] not in {
            "cooperative_refused",
            "pid_refused",
            "endpoint_refused",
            "no_metadata",
        }:
            return {"result_serialization": "refused", "unexpected_proof_value": True}
        return {"result_serialization": "ok", "result": safe}

    def payload(
        self, result: object, *, failure: str | None, cleanup_error: str | None, daemon: Any
    ) -> dict[str, Any]:
        return {
            "node": self.node,
            "source": str(Path(__file__).resolve()),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "timeouts": {
                "daemon_start_seconds": session_daemon._DAEMON_START_TIMEOUT_SECONDS,
                "poll_seconds": 0.05,
            },
            "result_before_cleanup": self.serialize_result(result),
            "trace": self.event_snapshot(),
            "failure_type": failure,
            "cleanup_error_type": cleanup_error,
            "shutdown_completion": self.shutdown_completion_result,
            "pre_cleanup": self.pre_cleanup,
            "post_cleanup": {
                "thread_alive": bool(daemon and daemon.thread.is_alive()),
                "server_close_entered": self.close_entered.is_set(),
            },
        }

    def snapshot_before_cleanup(self, daemon: Any) -> None:
        self.pre_cleanup = {
            "thread_alive": bool(daemon and daemon.thread.is_alive()),
            "listener_open": bool(daemon and daemon.server.socket.fileno() != -1),
            "server_close_entered": self.close_entered.is_set(),
        }


def _emit_trace(capsys: pytest.CaptureFixture[str], payload: dict[str, Any]) -> None:
    line = _TRACE_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with capsys.disabled():
        print(line, flush=True)


def _finish_diagnostic(
    trace: _StopTrace,
    daemon: Any,
    result: object,
    capsys: pytest.CaptureFixture[str],
    original_failure: BaseException | None,
    original_traceback: Any,
    release_shutdown: threading.Event | None = None,
) -> None:
    """Observation is fallible; release and close always run before selecting the primary error."""
    observer_errors: list[BaseException] = []
    cleanup_error: BaseException | None = None
    emergency_release = False

    def observer_failed(stage: str, error: BaseException) -> None:
        observer_errors.append(error)
        try:
            trace.add("observer_error", stage=stage, exception=type(error).__name__)
        except BaseException as recording_error:
            observer_errors.append(recording_error)

    try:
        try:
            trace.snapshot_before_cleanup(daemon)
        except BaseException as error:
            observer_failed("snapshot", error)
    finally:
        try:
            if release_shutdown is not None:
                try:
                    emergency_release = (
                        not trace.shutdown_held.is_set() or trace.shutdown_hold_expired
                    )
                    trace.add("control_shutdown_release", emergency=emergency_release)
                except BaseException as error:
                    observer_failed("release_observation", error)
                finally:
                    try:
                        release_shutdown.set()
                    except BaseException as error:
                        observer_failed("release", error)
        finally:
            if daemon is not None:
                try:
                    trace.set_stage("cleanup")
                except BaseException as error:
                    observer_failed("cleanup_stage", error)
                try:
                    daemon.close()
                except BaseException as error:
                    cleanup_error = error

    try:
        trace.wait_shutdown_completions()
    except BaseException as error:
        observer_failed("shutdown_completion", error)

    if release_shutdown is not None and (emergency_release or trace.shutdown_hold_expired):
        observer_failed(
            "control", AssertionError("control was not causally gated by the shutdown hold")
        )
    try:
        payload = trace.payload(
            result,
            failure=type(original_failure).__name__ if original_failure is not None else None,
            cleanup_error=type(cleanup_error).__name__ if cleanup_error is not None else None,
            daemon=daemon,
        )
        if payload["trace"]["incomplete"]:
            observer_failed("trace", AssertionError("diagnostic trace is incomplete"))
        if payload["result_before_cleanup"]["result_serialization"] != "ok":
            observer_failed("projection", AssertionError("diagnostic result projection refused"))
    except BaseException as error:
        observer_failed("payload", error)
        payload = {
            "node": trace.node,
            "failure_type": type(original_failure).__name__ if original_failure else None,
            "cleanup_error_type": type(cleanup_error).__name__ if cleanup_error else None,
            "trace": {"incomplete": True},
        }
    payload["observer_error_types"] = [type(error).__name__ for error in observer_errors]
    payload["diagnostic_incomplete"] = bool(observer_errors)
    if release_shutdown is not None:
        payload["control"] = {
            "name": "ack_before_shutdown",
            "emergency_release": emergency_release,
            "release_set": release_shutdown.is_set(),
            "hold_expired": trace.shutdown_hold_expired,
        }
    try:
        _emit_trace(capsys, payload)
    except BaseException as error:
        observer_failed("emission", error)
    if original_failure is not None:
        raise original_failure.with_traceback(original_traceback)
    if cleanup_error is not None:
        raise cleanup_error
    if observer_errors:
        raise observer_errors[0]


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1.0)


class _Daemon:
    def __init__(
        self,
        root: Path,
        metadata_version: str,
        *,
        trace: _StopTrace | None = None,
        shutdown_release: threading.Event | None = None,
    ) -> None:
        self.server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
        self.trace = trace
        host, port = self.server.server_address
        if trace is not None:
            trace.endpoint = (str(host), int(port))
            trace.shutdown_release = shutdown_release
            self._spy_lifecycle()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        session_daemon._write_daemon_metadata(
            root,
            {
                "version": 1,
                "root": str(root),
                "host": str(host),
                "port": int(port),
                "pid": 0,
                "started_at": "t",
                "token": "tok",
                "package_version": metadata_version,
            },
        )

    def _spy_lifecycle(self) -> None:
        assert self.trace is not None
        for name in ("serve_forever", "shutdown", "server_close"):
            real = getattr(self.server, name)

            def lifecycle(real_fn: Any, label: str) -> Any:
                def observed(*args: Any, **kwargs: Any) -> Any:
                    completion = threading.Event() if label == "shutdown" else None
                    if completion is not None:
                        self.trace._shutdown_completions.put(completion)
                    try:
                        started = time.monotonic()
                        self.trace.add(f"{label}_entry")
                        if label == "server_close":
                            self.trace.close_entered.set()
                        if label == "shutdown" and self.trace.shutdown_release is not None:
                            self.trace.add("shutdown_hold_entry")
                            self.trace.shutdown_held.set()
                            released = self.trace.shutdown_release.wait(10.0)
                            if not released:
                                self.trace.shutdown_hold_expired = True
                            self.trace.add("shutdown_hold_end", released=released)
                        self.trace.add(f"{label}_real_call")
                        try:
                            result = real_fn(*args, **kwargs)
                        except BaseException as error:
                            self.trace.add(
                                f"{label}_error",
                                elapsed=round(time.monotonic() - started, 6),
                                **self.trace._error_fields(error),
                            )
                            raise
                        self.trace.add(
                            f"{label}_return",
                            elapsed=round(time.monotonic() - started, 6),
                        )
                        return result
                    finally:
                        if completion is not None:
                            completion.set()

                return observed

            setattr(self.server, name, lifecycle(real, name))

    def _serve(self) -> None:
        try:
            self.server.serve_forever(poll_interval=0.05)
        finally:
            self.server.server_close()

    def close(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
        self.thread.join(5.0)


def _record_commands(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    commands: list[str] = []
    real = session_daemon._daemon_request

    def _spy(host: str, port: int, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        commands.append(str(request.get("command")))
        return real(host, port, request, **kwargs)

    monkeypatch.setattr(session_daemon, "_daemon_request", _spy)
    return commands


def test_doctored_metadata_on_a_current_daemon_gets_no_skew_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    daemon = _Daemon(root, metadata_version="OLD")  # the daemon itself runs the CURRENT version
    commands = _record_commands(monkeypatch)
    try:
        assert session_daemon._probe_daemon(root) is None  # premise: metadata makes it "stale"
        commands.clear()
        outcome = session_daemon._stop_rejected_daemon(
            root, session_daemon._read_daemon_metadata(root)
        )[1]
        assert outcome == "current_version", outcome
        assert "stop" not in commands, commands
        assert daemon.thread.is_alive(), "a current-version daemon was stopped on doctored metadata"
    finally:
        daemon.close()


def test_a_genuinely_different_signed_version_is_stopped_cooperatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(trust, "_daemon_running_version", lambda: "0.0.0-older")
    # Metadata claims the CURRENT version: the signed version alone decides.
    daemon = _Daemon(root, metadata_version=_expected_tg_version())
    try:
        result = session_daemon.stop_session_daemon(str(root))
        assert result["stopped"] is True and result["stop_method"] == "cooperative", result
        daemon.thread.join(5.0)
        assert not daemon.thread.is_alive()
    finally:
        daemon.close()


def _without_version_fields(real: Any) -> Any:
    def _fields(nonce: object, root: Path, own_port: int) -> dict[str, Any]:
        fields = dict(real(nonce, root, own_port))
        fields.pop("package_version", None)
        fields.pop("version_proof", None)
        return fields

    return _fields


def test_a_verified_reply_with_no_signed_version_is_treated_as_skewed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    root = tmp_path.resolve()
    trace = _StopTrace(request.node.nodeid)
    monkeypatch.setattr(
        session_daemon, "_ping_proof_fields", _without_version_fields(trust._ping_proof_fields)
    )
    trace.install_protocol_spies(monkeypatch)
    daemon: _Daemon | None = None
    result: dict[str, Any] | None = None
    original_failure: BaseException | None = None
    original_traceback: Any = None
    try:
        daemon = _Daemon(root, metadata_version="OLD", trace=trace)
        result = session_daemon.stop_session_daemon(str(root))
        assert result["stopped"] is True and result["stop_method"] == "cooperative", result
        assert not any(event["event"] == "signal_seam_reached" for event in trace.events), (
            "self-PID classification reached the signal seam"
        )
    except BaseException as error:
        original_failure = error
        original_traceback = error.__traceback__
    finally:
        _finish_diagnostic(
            trace,
            daemon,
            result,
            capsys,
            original_failure,
            original_traceback,
        )


def test_ack_is_not_refusal_when_fixture_shutdown_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    root = tmp_path.resolve()
    trace = _StopTrace(request.node.nodeid)
    release_shutdown = threading.Event()
    monkeypatch.setattr(
        session_daemon, "_ping_proof_fields", _without_version_fields(trust._ping_proof_fields)
    )
    trace.install_protocol_spies(monkeypatch)
    daemon: _Daemon | None = None
    result: dict[str, Any] | None = None
    original_failure: BaseException | None = None
    original_traceback: Any = None
    try:
        daemon = _Daemon(
            root,
            metadata_version="OLD",
            trace=trace,
            shutdown_release=release_shutdown,
        )
        result = session_daemon.stop_session_daemon(str(root))
        assert any(
            event["event"] == "control_ack_waited_for_shutdown_hold" and event.get("entered")
            for event in trace.events
        ), "shutdown hold handshake did not occur"
        assert any(
            event["event"] == "session_daemon_stop_verification" and event.get("verified")
            for event in trace.events
        ), "real proof verification did not succeed"
        assert any(
            event["event"] == "signed_version"
            and event.get("missing_version")
            and event.get("version_ok")
            for event in trace.events
        )
        assert any(
            event.get("event") == "daemon_request_end"
            and event.get("stage") == "stop-ack"
            and event.get("ok")
            and event.get("stopping")
            for event in trace.events
        ), "stop ACK was not observed"
        assert any(
            event["event"] == "socket_attempt_connected" and event.get("stage") == "refusal-poll"
            for event in trace.events
        ), "no real connection succeeded while shutdown was held"
        assert any(
            event.get("event") == "refusal_poll_end" and event.get("refused") is False
            for event in trace.events
        ), "accepting fixture unexpectedly appeared refused"
        assert result["stopped"] is False and result["running"] is True, result
        assert result["stop_method"] == "none" and result["unconfirmed_reason"] == "pid_unproven", (
            result
        )
        assert not any(
            event["event"] in {"shutdown_real_call", "serve_forever_return", "server_close_entry"}
            for event in trace.events
        ), "fixture shutdown advanced before control release"
        assert not trace.shutdown_hold_expired, "shutdown hold expired before explicit release"
        assert not any(event.get("event") == "signal_seam_reached" for event in trace.events), (
            "self-PID classification reached the signal seam"
        )
    except BaseException as error:
        original_failure = error
        original_traceback = error.__traceback__
    finally:
        _finish_diagnostic(
            trace,
            daemon,
            result,
            capsys,
            original_failure,
            original_traceback,
            release_shutdown,
        )


@pytest.mark.parametrize("secret_field", ["token", "nonce", "hmac", "secret", "version_proof"])
def test_diagnostic_projection_refuses_secret_fields_and_overflow_is_explicit(
    secret_field: str, request: pytest.FixtureRequest
) -> None:
    trace = _StopTrace(request.node.nodeid)
    safe = {
        "running": False,
        "stopped": True,
        "stop_method": "cooperative",
        "proof": "cooperative_refused",
    }
    assert trace.serialize_result(safe)["result_serialization"] == "ok"
    refused = trace.serialize_result({**safe, secret_field: "diagnostic-secret-sentinel"})
    encoded = json.dumps(refused)
    assert refused["result_serialization"] == "refused"
    assert "diagnostic-secret-sentinel" not in encoded
    for field in ("error", "stop_reply_error"):
        redacted = trace.serialize_result({**safe, field: "diagnostic-exception-sentinel"})
        assert redacted["result"][field] == "redacted_exception_text"
        assert "diagnostic-exception-sentinel" not in json.dumps(redacted)
    refused_proof = trace.serialize_result({**safe, "proof": "f" * 64})
    assert refused_proof["result_serialization"] == "refused"
    assert "f" * 64 not in json.dumps(refused_proof)
    unconfirmed = {"stopped": False, "unconfirmed_reason": "pid_unproven"}
    assert trace.serialize_result(unconfirmed)["result"] == unconfirmed
    for index in range(_TRACE_CAP + 1):
        trace.add("bounded", value=index)
    payload = trace.payload(safe, failure=None, cleanup_error=None, daemon=None)
    assert payload["trace"]["overflow"] is True
    assert len(payload["trace"]["events"]) == _TRACE_CAP
    assert payload["node"] == request.node.nodeid
    assert "diagnostic-secret-sentinel" not in json.dumps(payload)


def test_diagnostic_parallel_reservations_have_unique_sequence_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _StopTrace()
    rendezvous = threading.Barrier(2)
    real_time = time.monotonic
    errors: list[BaseException] = []

    def interleaved_time() -> float:
        if threading.current_thread().name.startswith("trace-race-"):
            rendezvous.wait(2.0)
        return real_time()

    def writer() -> None:
        try:
            trace.add("interleaved")
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(time, "monotonic", interleaved_time)
    writers = [threading.Thread(target=writer, name=f"trace-race-{index}") for index in range(2)]
    try:
        for thread in writers:
            thread.start()
        for thread in writers:
            thread.join(3.0)
    finally:
        rendezvous.abort()
        for thread in writers:
            thread.join(3.0)
    assert not errors and not any(thread.is_alive() for thread in writers)
    snapshot = trace.event_snapshot()
    assert [event["seq"] for event in snapshot["events"]] == [1, 2]
    assert snapshot["outstanding_reservations"] == 0 and not snapshot["incomplete"]


def test_diagnostic_pending_reservation_and_parallel_cap_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _StopTrace()
    for _ in range(_TRACE_CAP - 1):
        trace.add("filled")
    entered, release = threading.Event(), threading.Event()
    real_time = time.monotonic
    errors: list[BaseException] = []

    def held_time() -> float:
        if threading.current_thread().name == "trace-cap-writer":
            entered.set()
            if not release.wait(3.0):
                raise AssertionError("reservation hold expired")
        return real_time()

    def writer() -> None:
        try:
            trace.add("last_slot")
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(time, "monotonic", held_time)
    thread = threading.Thread(target=writer, name="trace-cap-writer")
    thread.start()
    try:
        assert entered.wait(2.0), "last-slot reservation did not reach its hold"
        pending = trace.event_snapshot()
        assert len(pending["events"]) == _TRACE_CAP - 1
        assert pending["outstanding_reservations"] == 1
        assert pending["incomplete"] and not pending["overflow"]
        trace.add("over_cap")
        assert trace.event_snapshot()["overflow"]
    finally:
        release.set()
        thread.join(3.0)
    assert not errors and not thread.is_alive()
    final = trace.event_snapshot()
    assert [event["seq"] for event in final["events"]] == list(range(1, _TRACE_CAP + 1))
    assert len(final["events"]) == _TRACE_CAP
    assert final["outstanding_reservations"] == 0 and final["overflow"] and final["incomplete"]


def test_diagnostic_unfilled_reservation_cannot_finish_green(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _StopTrace()
    error = RuntimeError("reservation-projection-failed")

    def fail_time() -> float:
        raise error

    with monkeypatch.context() as scoped:
        scoped.setattr(time, "monotonic", fail_time)
        with pytest.raises(RuntimeError) as raised:
            trace.add("unfilled")
    assert raised.value is error
    snapshot = trace.event_snapshot()
    assert snapshot["events"] == [] and not snapshot["overflow"]
    assert snapshot["outstanding_reservations"] == 1 and snapshot["incomplete"]
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        sys.modules[__name__], "_emit_trace", lambda _capsys, body: emitted.append(body)
    )
    with pytest.raises(AssertionError, match="diagnostic trace is incomplete"):
        _finish_diagnostic(trace, None, {"stopped": True}, capsys, None, None)
    assert emitted[0]["diagnostic_incomplete"] and emitted[0]["trace"]["incomplete"]


def test_diagnostic_refused_projection_cannot_finish_green(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _StopTrace()
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        sys.modules[__name__], "_emit_trace", lambda _capsys, body: emitted.append(body)
    )
    with pytest.raises(AssertionError, match="diagnostic result projection refused"):
        _finish_diagnostic(trace, None, {"stopped": True, "token": "private"}, capsys, None, None)
    assert emitted[0]["diagnostic_incomplete"]
    assert emitted[0]["result_before_cleanup"]["result_serialization"] == "refused"
    assert "private" not in json.dumps(emitted)


def _stub_lifecycle_daemon(trace: _StopTrace, shutdown: Any) -> tuple[_Daemon, list[str]]:
    """Exercise actual lifecycle spies/close without creating sockets or a serving thread."""
    closed: list[str] = []

    class Stub:
        socket: Any

        def __init__(self) -> None:
            self.socket = self

        def serve_forever(self) -> None:
            pass

        def shutdown(self) -> None:
            shutdown()

        def server_close(self) -> None:
            pass

        def fileno(self) -> int:
            return -1

        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float) -> None:
            assert timeout == 5.0
            closed.append("serve_join")

    daemon = _Daemon.__new__(_Daemon)
    daemon.trace = trace
    daemon.server = daemon.thread = Stub()
    daemon._spy_lifecycle()
    return daemon, closed


def test_diagnostic_delayed_shutdown_observation_completes_during_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _StopTrace()
    held, release = threading.Event(), threading.Event()
    local = threading.local()
    real_add, real_time = trace.add, time.monotonic
    errors: list[BaseException] = []
    emitted: list[dict[str, Any]] = []

    def add(name: str, **fields: Any) -> None:
        if name == "shutdown_entry":
            assert trace._shutdown_completions.qsize() == 1
        local.return_observation = name == "shutdown_return"
        try:
            real_add(name, **fields)
        finally:
            local.return_observation = False

    def clock() -> float:
        if getattr(local, "return_observation", False):
            held.set()
            if not release.wait(3.0):
                raise AssertionError("return-observation hold expired")
        return real_time()

    monkeypatch.setattr(trace, "add", add)
    monkeypatch.setattr(time, "monotonic", clock)
    monkeypatch.setattr(
        sys.modules[__name__], "_emit_trace", lambda _capsys, body: emitted.append(body)
    )
    daemon, closed = _stub_lifecycle_daemon(trace, lambda: None)

    def worker() -> None:
        try:
            daemon.server.shutdown()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert held.wait(2.0), "return observation did not reserve its held ticket"
        pending = trace.event_snapshot()
        assert pending["outstanding_reservations"] == 1 and pending["incomplete"]
        assert pending["unwaited_shutdown_completions"] == 1
        completion = trace._shutdown_completions.get_nowait()
        trace._shutdown_completions.put(completion)
        real_wait = completion.wait

        def wait(timeout: float) -> bool:
            assert closed == ["serve_join"], "callback wait preceded existing fixture close/join"
            assert 0.0 <= timeout <= 5.0
            release.set()
            return real_wait(timeout)

        monkeypatch.setattr(completion, "wait", wait)
        _finish_diagnostic(trace, daemon, {"stopped": True}, capsys, None, None)
    finally:
        release.set()
        thread.join(3.0)
    assert not errors and not thread.is_alive()
    assert not emitted[0]["diagnostic_incomplete"]
    assert not emitted[0]["trace"]["incomplete"]
    assert emitted[0]["trace"]["outstanding_reservations"] == 0
    assert emitted[0]["trace"]["unwaited_shutdown_completions"] == 0
    assert emitted[0]["shutdown_completion"]["entered_wrappers"] == 1
    assert emitted[0]["shutdown_completion"]["completed"] == 1


def test_diagnostic_shutdown_completion_timeout_remains_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _StopTrace()
    entered, release = threading.Event(), threading.Event()
    emitted: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    def shutdown() -> None:
        entered.set()
        if not release.wait(10.0):
            raise AssertionError("completion-timeout fixture was not released")

    daemon, closed = _stub_lifecycle_daemon(trace, shutdown)
    monkeypatch.setattr(
        sys.modules[__name__], "_emit_trace", lambda _capsys, body: emitted.append(body)
    )

    def worker() -> None:
        try:
            daemon.server.shutdown()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(2.0), "owned shutdown did not enter its fixture hold"
        with pytest.raises(AssertionError, match="owned shutdown observations did not complete"):
            _finish_diagnostic(trace, daemon, {"stopped": True}, capsys, None, None)
        assert closed == ["serve_join"]
        assert emitted[0]["trace"]["incomplete"] and emitted[0]["diagnostic_incomplete"]
        assert emitted[0]["shutdown_completion"]["timed_out"]
        assert emitted[0]["shutdown_completion"]["entered_wrappers"] == 1
        assert emitted[0]["shutdown_completion"]["completed"] == 0
    finally:
        release.set()
        thread.join(3.0)
    assert not errors and not thread.is_alive()
    assert trace.event_snapshot()["incomplete"], "late completion erased the failed cleanup budget"


def test_diagnostic_completed_shutdown_with_unfilled_ticket_still_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trace = _StopTrace()
    daemon, _closed = _stub_lifecycle_daemon(trace, lambda: None)
    real_add, real_time = trace.add, time.monotonic
    local = threading.local()
    error = RuntimeError("private-return-observation-error")
    emitted: list[dict[str, Any]] = []

    def add(name: str, **fields: Any) -> None:
        local.fail_return = name == "shutdown_return"
        try:
            real_add(name, **fields)
        finally:
            local.fail_return = False

    def clock() -> float:
        if getattr(local, "fail_return", False):
            raise error
        return real_time()

    with monkeypatch.context() as scoped:
        scoped.setattr(trace, "add", add)
        scoped.setattr(time, "monotonic", clock)
        with pytest.raises(RuntimeError) as raised:
            daemon.server.shutdown()
    assert raised.value is error
    monkeypatch.setattr(
        sys.modules[__name__], "_emit_trace", lambda _capsys, body: emitted.append(body)
    )
    with pytest.raises(AssertionError, match="diagnostic trace is incomplete"):
        _finish_diagnostic(trace, daemon, {"stopped": True}, capsys, None, None)
    assert emitted[0]["shutdown_completion"]["completed"] == 1
    assert not emitted[0]["shutdown_completion"]["timed_out"]
    assert emitted[0]["trace"]["outstanding_reservations"] == 1
    assert emitted[0]["trace"]["incomplete"] and emitted[0]["diagnostic_incomplete"]
    assert "private-return-observation-error" not in json.dumps(emitted)


def test_diagnostic_shutdown_completions_share_one_cleanup_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = _StopTrace()
    clock_values = iter([10.0, 11.0, 14.0])
    budgets: list[float] = []
    for _ in range(2):
        completion = threading.Event()
        completion.set()
        real_wait = completion.wait

        def waiter(wait_fn: Any) -> Any:
            def wait(timeout: float) -> bool:
                budgets.append(timeout)
                return wait_fn(timeout)

            return wait

        monkeypatch.setattr(completion, "wait", waiter(real_wait))
        trace._shutdown_completions.put(completion)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock_values))
    trace.wait_shutdown_completions()
    assert budgets == [4.0, 1.0]
    assert trace.shutdown_completion_result is not None
    assert trace.shutdown_completion_result["completed"] == 2
    assert not trace.event_snapshot()["incomplete"]


@pytest.mark.parametrize("observer_seam", ["snapshot", "payload", "emission", "completion"])
@pytest.mark.parametrize("primary", ["original", "cleanup", "both", "none"])
@pytest.mark.parametrize("control", [False, True], ids=["natural", "control"])
def test_diagnostic_observer_errors_always_cleanup_and_preserve_precedence(
    observer_seam: str,
    primary: str,
    control: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    trace = _StopTrace()
    original_error = (
        AssertionError("original behavior failed") if primary in {"original", "both"} else None
    )
    cleanup_error = OSError("cleanup failed") if primary in {"cleanup", "both"} else None
    observer_error = RuntimeError("private-observer-text-sentinel")
    release = threading.Event() if control else None
    trace.shutdown_held.set()
    calls: list[str] = []
    emitted: list[dict[str, Any]] = []

    class Fixture:
        def __init__(self) -> None:
            self.thread = self.server = self.socket = self

        def is_alive(self) -> bool:
            return False

        def fileno(self) -> int:
            return -1

        def close(self) -> None:
            calls.append("close")
            assert release is None or release.is_set(), "control was not released before close"
            if cleanup_error is not None:
                raise cleanup_error

    def fail(*args: Any, **kwargs: Any) -> Any:
        calls.append(observer_seam)
        raise observer_error

    def emit(_capsys: Any, body: dict[str, Any]) -> None:
        emitted.append(body)
        if observer_seam == "emission":
            fail()

    if observer_seam != "emission":
        monkeypatch.setattr(
            trace,
            {
                "snapshot": "snapshot_before_cleanup",
                "payload": "payload",
                "completion": "wait_shutdown_completions",
            }[observer_seam],
            fail,
        )
    monkeypatch.setattr(sys.modules[__name__], "_emit_trace", emit)
    expected = original_error or cleanup_error or observer_error
    with pytest.raises(type(expected)) as raised:
        _finish_diagnostic(
            trace, Fixture(), {"stopped": True}, capsys, original_error, None, release
        )
    assert raised.value is expected
    assert calls.count("close") == 1 and (release is None or release.is_set())
    assert emitted[0]["failure_type"] == (type(original_error).__name__ if original_error else None)
    assert emitted[0]["cleanup_error_type"] == (
        type(cleanup_error).__name__ if cleanup_error else None
    )
    assert "private-observer-text-sentinel" not in json.dumps(emitted)
    assert any(event["event"] == "observer_error" for event in trace.events)
    if observer_seam != "emission":
        assert emitted[0]["diagnostic_incomplete"]
        assert "RuntimeError" in emitted[0]["observer_error_types"]


@pytest.mark.parametrize("seam", ["request", "probe", "refusal", "proof"])
def test_diagnostic_spies_preserve_errors_and_restore_stage(
    seam: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = RuntimeError("diagnostic-private-exception-sentinel")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    target = {
        "request": (session_daemon, "_daemon_request"),
        "probe": (session_daemon, "_probe_daemon"),
        "refusal": (session_daemon_stop, "_await_endpoint_refused"),
        "proof": (session_daemon, "_ping_proof_fields"),
    }[seam]
    monkeypatch.setattr(*target, fail)
    trace = _StopTrace()
    trace.install_protocol_spies(monkeypatch)
    trace.set_stage("caller-stage")
    arguments = {
        "request": ("127.0.0.1", 4242, {"command": "ping"}),
        "probe": (Path("/r"),),
        "refusal": ("127.0.0.1", 4242, 1.0),
        "proof": ("diagnostic-private-nonce-sentinel", Path("/r"), 4242),
    }[seam]
    with pytest.raises(RuntimeError) as raised:
        getattr(*target)(*arguments)
    assert raised.value is error
    assert trace.stage() == "caller-stage"
    encoded = json.dumps(trace.events)
    assert "diagnostic-private-exception-sentinel" not in encoded
    assert "diagnostic-private-nonce-sentinel" not in encoded
    if seam == "proof":
        assert [event["event"] for event in trace.events[1:]] == [
            "ping_proof_fields_entry",
            "ping_proof_fields_error",
        ]


def test_diagnostic_socket_spy_leaves_unrelated_arguments_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returned = object()
    calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def connect(address: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((address, args, kwargs))
        return returned

    monkeypatch.setattr(socket, "create_connection", connect)
    trace = _StopTrace()
    trace.endpoint = ("127.0.0.1", 4242)
    trace.install_protocol_spies(monkeypatch)
    address = ("127.0.0.1", "invalid-port")
    assert socket.create_connection(address, timeout="unchanged") is returned
    assert calls == [(address, (), {"timeout": "unchanged"})]
    assert not any(event["event"].startswith("socket_attempt") for event in trace.events)


@pytest.mark.parametrize("tamper", ["forged_mac", "unsigned_version", "wrong_version_signed"])
def test_a_forged_or_unsigned_version_is_refused_not_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    root = tmp_path.resolve()
    real = trust._ping_proof_fields

    def _tampered(nonce: object, rt: Path, own_port: int) -> dict[str, Any]:
        fields = dict(real(nonce, rt, own_port))
        if tamper == "forged_mac":
            fields["package_version"] = "OLD"  # version changed, MAC still for the real one
        elif tamper == "unsigned_version":
            fields.pop("version_proof", None)
        else:  # the MAC is valid for a DIFFERENT nonce/version pair
            fields["version_proof"] = "0" * 64
        return fields

    monkeypatch.setattr(session_daemon, "_ping_proof_fields", _tampered)
    daemon = _Daemon(root, metadata_version="OLD")
    commands = _record_commands(monkeypatch)
    try:
        outcome = session_daemon._stop_rejected_daemon(
            root, session_daemon._read_daemon_metadata(root)
        )[1]
        assert outcome == "unverified", outcome
        assert "stop" not in commands, commands
        assert daemon.thread.is_alive()
    finally:
        daemon.close()


def test_signed_version_helpers_accept_the_real_reply_and_reject_a_stripped_proof() -> None:
    # POSITIVE CONTROL for the refusal tests: an untampered signed reply verifies and carries the
    # version; the helpers do not simply reject everything.
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    nonce, root, port, pid = "n" * 16, Path("/r"), 4242, 99
    reply = {
        "pid": pid,
        "root": str(root),
        "port": port,
        "package_version": "1.2.3",
        "version_proof": trust._daemon_version_proof(secret, nonce, pid, str(root), port, "1.2.3"),
    }
    assert trust._signed_ping_version(reply, nonce, root, port) == (True, "1.2.3")
    assert trust._signed_ping_version({**reply, "package_version": "9.9.9"}, nonce, root, port) == (
        False,
        None,
    )
    assert trust._signed_ping_version({"pid": pid, "root": str(root)}, nonce, root, port) == (
        True,
        None,
    )
