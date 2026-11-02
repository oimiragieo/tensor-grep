"""Malformed numeric daemon ports must be rejected BEFORE any connection (PR #1197 audit, bypass 3).

``int()`` coerces ``True`` -> 1 and ``4242.9`` -> 4242 and raises ``OverflowError`` on ``inf``; a
planted daemon.json used to reach ``_daemon_request`` with those or crash the probe.
"""

from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_DAEMON_REQUEST_FUNCTION = sd._daemon_request


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def _plant(root: Path, port: Any) -> None:
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": port,
            "pid": 1,
            "started_at": "x",
            "token": "t",
        },
    )


def _reply_shape(value: object) -> dict[str, bool | str]:
    response = value if type(value) is dict else {}
    shape: dict[str, bool | str] = {
        "response_type": type(value).__name__,
        "response_ok": bool(response.get("ok")),
    }
    for field in ("pid", "root", "port", "proof"):
        present = field in response
        shape[f"response_{field}_present"] = present
        shape[f"response_{field}_type"] = type(response[field]).__name__ if present else "missing"
    return shape


def _metadata_shape(value: object) -> dict[str, bool]:
    metadata = value if type(value) is dict else {}
    return {
        "metadata_present": type(value) is dict,
        "metadata_loopback": type(value) is dict
        and sd._is_loopback_host(metadata.get("host", "127.0.0.1")),
        "metadata_port_valid": type(value) is dict
        and sd._valid_daemon_port(metadata.get("port")) is not None,
        "metadata_version_matches": type(value) is dict
        and metadata.get("package_version") == _expected_tg_version(),
    }


_DAEMON_REQUEST_CALL_LINES = {521: "connect", 526: "send", 528: "read"}


def _request_failure_stage(error: BaseException, request_function: Callable[..., Any]) -> str:
    code = getattr(request_function, "__code__", None)
    if (
        request_function is not _DAEMON_REQUEST_FUNCTION
        or code is None
        or code.co_name != "_daemon_request"
        or request_function.__module__ != sd.__name__
        or request_function.__globals__ is not sd.__dict__
        or Path(code.co_filename).resolve() != Path(sd.__file__).resolve()
    ):
        return "unknown"
    frame = error.__traceback__
    request_line: int | None = None
    while frame is not None:
        if frame.tb_frame.f_code is code:
            request_line = frame.tb_lineno
        frame = frame.tb_next
    return _DAEMON_REQUEST_CALL_LINES.get(request_line, "unknown")


def _observe_call(
    real: Callable[..., Any], diagnostics: dict[str, dict[str, Any]], stage: str
) -> Callable[..., Any]:
    def observed(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        row: dict[str, Any] = {"called": True}
        diagnostics[stage] = row
        try:
            result = real(*args, **kwargs)
        except BaseException as error:
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            row["exception_class"] = type(error).__name__
            if stage == "request":
                row["failure_stage"] = _request_failure_stage(error, real)
            raise
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        if stage == "metadata":
            row.update(_metadata_shape(result))
        elif stage == "request":
            row.update(_reply_shape(result))
        elif stage == "verification":
            row["signed_ping_accepted"] = result is True
        return result

    return observed


def _observe_substep(
    real: Callable[..., Any], diagnostics: dict[str, dict[str, Any]], stage: str
) -> Callable[..., Any]:
    def observed(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        diagnostics[stage] = row = dict[str, Any](called=True, completed=False)
        try:
            result = real(*args, **kwargs)
        finally:
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        row["completed"] = True
        return result

    return observed


def _prepared_ping_handler(secret: bytes, diagnostics: dict[str, dict[str, Any]]) -> type:
    real_proof = trust._ping_proof_fields
    proof = FunctionType(
        real_proof.__code__,
        {
            **real_proof.__globals__,
            "_load_or_create_user_secret": _observe_substep(
                lambda: secret, diagnostics, "proof_secret"
            ),
            "_daemon_running_version": _observe_substep(
                trust._daemon_running_version, diagnostics, "proof_version"
            ),
        },
        argdefs=real_proof.__defaults__,
        closure=real_proof.__closure__,
    )
    real_handle = sd._SessionDaemonHandler.handle

    class PreparedHandler(sd._SessionDaemonHandler):
        handle = FunctionType(
            real_handle.__code__,
            {
                **real_handle.__globals__,
                "_ping_proof_fields": _observe_call(proof, diagnostics, "proof_fields"),
            },
            argdefs=real_handle.__defaults__,
            closure=real_handle.__closure__,
        )

    return PreparedHandler


def _settle_daemon(server: Any, listener: Any, workers: list[Any]) -> None:
    errors: list[str] = []

    def shutdown() -> None:
        try:
            server.shutdown()
        except BaseException as error:
            errors.append(type(error).__name__)

    stopper = threading.Thread(target=shutdown, daemon=True)
    try:
        if listener.ident is not None:
            stopper.start()
            stopper.join(timeout=2.0)
    except BaseException as error:
        errors.append(type(error).__name__)
    try:
        server.server_close()
    except BaseException as error:
        errors.append(type(error).__name__)
    for thread in [listener, *workers]:
        try:
            if thread.ident is not None:
                thread.join(timeout=2.0)
        except BaseException as error:
            errors.append(type(error).__name__)
    alive = [thread.is_alive() for thread in [stopper, listener, *workers]]
    assert not errors and not any(alive), (
        f"daemon settlement failed: errors={errors}, alive={alive}"
    )


@pytest.fixture
def _running_daemon(tmp_path: Path) -> Iterator[tuple[Path, sd._ThreadedSessionDaemon, dict]]:
    secret = trust._load_or_create_user_secret()
    assert isinstance(secret, bytes)
    diagnostics: dict[str, dict[str, Any]] = {}
    handler = _prepared_ping_handler(secret, diagnostics)
    root = tmp_path.resolve()
    workers: list[Any] = []
    thread = threading.Thread(target=lambda: server.serve_forever(), daemon=True)
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    primary: BaseException | None = None
    try:
        server.RequestHandlerClass = handler
        # Track daemon workers before start; settle them ourselves with bounded joins.
        server._threads = SimpleNamespace(append=workers.append, join=lambda: None)
        thread.start()
        yield root, server, diagnostics
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            _settle_daemon(server, thread, workers)
        except BaseException as cleanup:
            if primary is None:
                raise
            primary.add_note(f"daemon cleanup failed: {type(cleanup).__name__}")


def test_prepared_handler_owns_actual_proof_code_without_global_signer_patch() -> None:
    key = b"fixture-owned-test-key"
    handler = _prepared_ping_handler(key, {})
    observed = handler.handle.__globals__["_ping_proof_fields"]
    proof = inspect.getclosurevars(observed).nonlocals["real"]
    assert handler.handle.__code__ is sd._SessionDaemonHandler.handle.__code__
    assert proof.__code__ is trust._ping_proof_fields.__code__
    for bound, original, changed in (
        (handler.handle, sd._SessionDaemonHandler.handle, {"_ping_proof_fields"}),
        (
            proof,
            trust._ping_proof_fields,
            {"_load_or_create_user_secret", "_daemon_running_version"},
        ),
    ):
        assert bound.__defaults__ is original.__defaults__
        assert bound.__closure__ is original.__closure__
        assert {
            name
            for name in original.__globals__
            if bound.__globals__[name] is not original.__globals__[name]
        } == changed
    assert proof.__globals__["_load_or_create_user_secret"]() is key


@pytest.mark.parametrize("failure", [None, "worker", "close"])
def test_settlement_is_bounded_and_reports_incomplete_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    server = Mock()
    listener, worker, stopper = (Mock(ident=1) for _ in range(3))
    for thread in (listener, worker, stopper):
        thread.is_alive.return_value = False
    worker.is_alive.return_value = failure == "worker"
    stopper.start.side_effect = server.shutdown
    if failure == "close":
        server.server_close.side_effect = OSError("private cleanup detail")
    monkeypatch.setattr(f"{__name__}.threading", SimpleNamespace(Thread=Mock(return_value=stopper)))
    if failure:
        with pytest.raises(AssertionError, match="daemon settlement failed") as raised:
            _settle_daemon(server, listener, [worker])
        assert "private cleanup detail" not in str(raised.value)
    else:
        _settle_daemon(server, listener, [worker])
    server.server_close.assert_called_once_with()
    for thread in (listener, worker, stopper):
        thread.join.assert_called_once_with(timeout=2.0)


class _RequestBoundaryConnection:
    def __init__(self, failure_stage: str, error: BaseException) -> None:
        self.failure_stage = failure_stage
        self.error = error
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def create_connection(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if self.failure_stage == "connect":
            raise self.error
        return self

    def __enter__(self) -> _RequestBoundaryConnection:
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False

    def settimeout(self, _timeout: float | None) -> None:
        return None

    def sendall(self, _payload: bytes) -> None:
        if self.failure_stage == "send":
            raise self.error

    def makefile(self, *_args: Any, **_kwargs: Any) -> _RequestBoundaryConnection:
        return self

    def readline(self) -> str:
        raise self.error


BAD_PORTS = [float("inf"), True, 4242.9, 0, 70000, -1, "8080", None, [8080]]


@pytest.mark.parametrize("port", BAD_PORTS, ids=repr)
def test_probe_rejects_malformed_port_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: Any
) -> None:
    root = tmp_path.resolve()
    _plant(root, port)
    calls: list[Any] = []
    monkeypatch.setattr(sd, "_daemon_request", lambda *a, **k: calls.append(a) or {"ok": True})
    assert sd._probe_daemon(root) is None
    assert calls == []


@pytest.mark.parametrize("port", BAD_PORTS, ids=repr)
def test_daemon_request_itself_refuses_a_malformed_port(
    monkeypatch: pytest.MonkeyPatch, port: Any
) -> None:
    import socket

    connects: list[Any] = []

    def _record(*a: Any, **k: Any) -> Any:
        connects.append(a)
        raise OSError("blocked by test")

    monkeypatch.setattr(socket, "create_connection", _record)
    with pytest.raises(ValueError):
        sd._daemon_request("127.0.0.1", port, {"command": "ping"})
    assert connects == []


@pytest.mark.parametrize("port", BAD_PORTS, ids=repr)
def test_stop_does_not_dial_a_malformed_planted_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: Any
) -> None:
    root = tmp_path.resolve()
    _plant(root, port)
    import socket

    connects: list[Any] = []

    def _record(*a: Any, **k: Any) -> Any:
        connects.append(a)
        raise OSError("blocked by test")

    monkeypatch.setattr(socket, "create_connection", _record)
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda _m, **_k: False)
    sd.stop_session_daemon(str(root))  # must not raise and must not connect
    assert connects == []


def test_daemon_identity_never_raises_on_an_overflowing_number() -> None:
    # _daemon_identity only guards unlinking daemon.json (it never dials), so it keeps its
    # tolerant coercion (a string "2222" must still match -- see the metadata-ownership test);
    # what it must not do is raise OverflowError on a planted inf.
    assert sd._daemon_identity({"pid": 5, "port": float("inf")}) == (5, None)
    assert sd._daemon_identity({"pid": float("inf"), "port": 4242}) == (None, 4242)
    assert sd._daemon_identity({"pid": 5, "port": 4242}) == (5, 4242)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(4242, 4242), (1, 1), (65535, 65535), (0, None), (65536, None), (True, None), ("1", None)],
)
def test_valid_daemon_port_is_exactly_an_int_in_range(value: Any, expected: Any) -> None:
    assert sd._valid_daemon_port(value) == expected


def test_a_valid_int_port_is_still_accepted(
    _running_daemon: tuple[Path, sd._ThreadedSessionDaemon, dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # POSITIVE CONTROL: the rejections above would pass against a probe that rejects everything.
    root, server, diagnostics = _running_daemon
    port = int(server.server_address[1])
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": port,
            "started_at": "x",
            "token": "tok",
        },
    )
    real_metadata = sd._read_daemon_metadata
    real_request = sd._daemon_request
    real_verify = sd._verify_ping_reply
    metadata_observer = _observe_call(real_metadata, diagnostics, "metadata")
    request_observer = _observe_call(real_request, diagnostics, "request")
    verify_observer = _observe_call(real_verify, diagnostics, "verification")
    monkeypatch.setattr(sd, "_read_daemon_metadata", metadata_observer)
    monkeypatch.setattr(sd, "_daemon_request", request_observer)
    monkeypatch.setattr(sd, "_verify_ping_reply", verify_observer)

    observed = sd._probe_daemon(root)
    assert observed is not None, f"sanitized daemon probe diagnostics: {diagnostics!r}"
    assert diagnostics["metadata"]["metadata_present"] is True
    assert diagnostics["metadata"]["metadata_loopback"] is True
    assert diagnostics["metadata"]["metadata_port_valid"] is True
    assert diagnostics["metadata"]["metadata_version_matches"] is True
    assert diagnostics["request"]["response_type"] == "dict"
    assert diagnostics["request"]["response_ok"] is True
    assert diagnostics["request"]["response_proof_present"] is True
    assert diagnostics["verification"]["signed_ping_accepted"] is True
    assert diagnostics["proof_fields"]["called"] is True
    assert set(diagnostics["proof_fields"]) == {"called", "elapsed_ms"}
    assert type(diagnostics["proof_fields"]["elapsed_ms"]) is float
    assert diagnostics["proof_secret"]["completed"] is True
    assert diagnostics["proof_version"]["completed"] is True


def test_ProbeObserver_RealCall_PreservesResultAndArguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 7.0)
    positional = object()
    keyword_value = object()
    result = object()
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    diagnostics: dict[str, dict[str, Any]] = {}
    rows_seen_at_entry: list[dict[str, Any]] = []

    def real(*args: Any, **kwargs: Any) -> object:
        calls.append((args, kwargs))
        rows_seen_at_entry.append(dict(diagnostics["sentinel"]))
        return result

    observed = _observe_call(real, diagnostics, "sentinel")

    returned = observed(positional, payload=keyword_value)

    assert returned is result
    assert calls == [((positional,), {"payload": keyword_value})]
    assert calls[0][0][0] is positional
    assert calls[0][1]["payload"] is keyword_value
    assert rows_seen_at_entry == [{"called": True}]
    assert diagnostics == {"sentinel": {"called": True, "elapsed_ms": 0.0}}
    assert type(diagnostics["sentinel"]["elapsed_ms"]) is float


def test_ProbeObserver_RealException_PreservesIdentity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 7.0)
    error = TimeoutError("observer-private-exception-sentinel")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def real(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise error

    diagnostics: dict[str, dict[str, Any]] = {}
    observed = _observe_call(real, diagnostics, "request")

    with pytest.raises(TimeoutError) as raised:
        observed("127.0.0.1", 4242, command="ping")

    assert raised.value is error
    assert calls == [(("127.0.0.1", 4242), {"command": "ping"})]
    assert diagnostics == {
        "request": {
            "called": True,
            "elapsed_ms": 0.0,
            "exception_class": "TimeoutError",
            "failure_stage": "unknown",
        }
    }
    assert "observer-private-exception-sentinel" not in repr(diagnostics)


def test_ProbeDiagnostics_SensitiveInputs_ExcludesSensitiveValues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 7.0)
    diagnostics: dict[str, dict[str, Any]] = {}
    metadata = {
        "host": "127.0.0.1",
        "port": 4242,
        "package_version": _expected_tg_version(),
        "root": "observer-private-root-sentinel",
        "token": "observer-private-token-sentinel",
    }
    response = {
        "ok": True,
        "pid": 77,
        "root": "observer-private-root-sentinel",
        "port": 4242,
        "proof": "observer-private-proof-sentinel",
        "nonce": "observer-private-nonce-sentinel",
        "token": "observer-private-token-sentinel",
    }

    metadata_result = _observe_call(lambda: metadata, diagnostics, "metadata")()
    request_result = _observe_call(lambda: response, diagnostics, "request")()
    verification_result = _observe_call(lambda: True, diagnostics, "verification")()

    assert metadata_result is metadata
    assert request_result is response
    assert verification_result is True
    assert diagnostics == {
        "metadata": {
            "called": True,
            "elapsed_ms": 0.0,
            "metadata_present": True,
            "metadata_loopback": True,
            "metadata_port_valid": True,
            "metadata_version_matches": True,
        },
        "request": {
            "called": True,
            "elapsed_ms": 0.0,
            "response_type": "dict",
            "response_ok": True,
            "response_pid_present": True,
            "response_pid_type": "int",
            "response_root_present": True,
            "response_root_type": "str",
            "response_port_present": True,
            "response_port_type": "int",
            "response_proof_present": True,
            "response_proof_type": "str",
        },
        "verification": {
            "called": True,
            "elapsed_ms": 0.0,
            "signed_ping_accepted": True,
        },
    }
    assert "observer-private-root-sentinel" not in repr(diagnostics)
    assert "observer-private-token-sentinel" not in repr(diagnostics)
    assert "observer-private-proof-sentinel" not in repr(diagnostics)
    assert "observer-private-nonce-sentinel" not in repr(diagnostics)


@pytest.mark.parametrize(
    ("failure_stage", "expected_stage", "message"),
    [
        ("connect", "connect", "observer-private-connect-sentinel"),
        ("send", "send", "observer-private-send-sentinel"),
        ("read", "read", "observer-private-read-sentinel"),
    ],
    ids=["connect", "send", "read"],
)
def test_ProbeObserver_RequestTimeout_ReportsStageAndPreservesIdentity(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    expected_stage: str,
    message: str,
) -> None:
    import socket

    error = TimeoutError(message)
    connection = _RequestBoundaryConnection(failure_stage, error)
    monkeypatch.setattr(socket, "create_connection", connection.create_connection)
    diagnostics: dict[str, dict[str, Any]] = {}
    observed = _observe_call(_DAEMON_REQUEST_FUNCTION, diagnostics, "request")
    monkeypatch.setattr(sd, "_daemon_request", observed)

    with pytest.raises(TimeoutError) as raised:
        sd._daemon_request(
            "127.0.0.1",
            4242,
            {"command": "ping", "nonce": "observer-private-nonce-sentinel"},
            token="observer-private-token-sentinel",
        )

    assert raised.value is error
    assert len(connection.calls) == 1
    assert connection.calls[0][0] == (("127.0.0.1", 4242),)
    assert diagnostics["request"]["failure_stage"] == expected_stage
    assert diagnostics["request"]["exception_class"] == "TimeoutError"
    assert message not in repr(diagnostics)
    assert "observer-private-nonce-sentinel" not in repr(diagnostics)
    assert "observer-private-token-sentinel" not in repr(diagnostics)
