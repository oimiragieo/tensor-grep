"""Malformed numeric daemon ports must be rejected BEFORE any connection (PR #1197 audit, bypass 3).

``int()`` coerces ``True`` -> 1 and ``4242.9`` -> 4242 and raises ``OverflowError`` on ``inf``; a
planted daemon.json used to reach ``_daemon_request`` with those or crash the probe.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


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


def _observe_call(
    real: Callable[..., Any], diagnostics: dict[str, dict[str, Any]], stage: str
) -> Callable[..., Any]:
    def observed(*args: Any, **kwargs: Any) -> Any:
        started = time.monotonic()
        row: dict[str, Any] = {"called": True}
        try:
            result = real(*args, **kwargs)
        except BaseException as error:
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            row["exception_class"] = type(error).__name__
            diagnostics[stage] = row
            raise
        row["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        if stage == "metadata":
            row.update(_metadata_shape(result))
        elif stage == "request":
            row.update(_reply_shape(result))
        elif stage == "verification":
            row["signed_ping_accepted"] = result is True
        diagnostics[stage] = row
        return result

    return observed


@pytest.fixture
def _running_daemon(
    tmp_path: Path,
) -> Iterator[tuple[Path, sd._ThreadedSessionDaemon]]:
    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "daemon server thread did not stop within 2 seconds"


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
    _running_daemon: tuple[Path, sd._ThreadedSessionDaemon],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # POSITIVE CONTROL: the rejections above would pass against a probe that rejects everything.
    root, server = _running_daemon
    _plant(root, int(server.server_address[1]))
    meta = sd._read_daemon_metadata(root)
    assert meta is not None
    meta["token"] = "tok"
    sd._write_daemon_metadata(root, meta)

    diagnostics: dict[str, dict[str, Any]] = {}
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


def test_ProbeObserver_RealCall_PreservesResultAndArguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 7.0)
    positional = object()
    keyword_value = object()
    result = object()
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def real(*args: Any, **kwargs: Any) -> object:
        calls.append((args, kwargs))
        return result

    diagnostics: dict[str, dict[str, Any]] = {}
    observed = _observe_call(real, diagnostics, "sentinel")

    returned = observed(positional, payload=keyword_value)

    assert returned is result
    assert calls == [((positional,), {"payload": keyword_value})]
    assert calls[0][0][0] is positional
    assert calls[0][1]["payload"] is keyword_value
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
