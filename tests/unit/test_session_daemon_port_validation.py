"""Malformed numeric daemon ports must be rejected BEFORE any connection (PR #1197 audit, bypass 3).

``int()`` coerces ``True`` -> 1 and ``4242.9`` -> 4242 and raises ``OverflowError`` on ``inf``; a
planted daemon.json used to reach ``_daemon_request`` with those or crash the probe.
"""

from __future__ import annotations

import threading
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
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", lambda _m: False)
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


def test_a_valid_int_port_is_still_accepted(tmp_path: Path) -> None:
    # POSITIVE CONTROL: the rejections above would pass against a probe that rejects everything.
    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _plant(root, int(server.server_address[1]))
        meta = sd._read_daemon_metadata(root)
        assert meta is not None
        meta["token"] = "tok"
        sd._write_daemon_metadata(root, meta)
        assert sd._probe_daemon(root) is not None
    finally:
        server.shutdown()
        server.server_close()
