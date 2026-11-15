from __future__ import annotations

import json
import os
import socket
import socketserver
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


class _Fake:
    def __init__(self, reply: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        class H(socketserver.StreamRequestHandler):
            def handle(self_inner) -> None:
                req = json.loads(self_inner.rfile.readline())
                self_inner.wfile.write((json.dumps(reply(req)) + "\n").encode())

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.port = int(self.srv.server_address[1])
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _plant(root: Path, *, host: str = "127.0.0.1", port: int) -> None:
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": host,
            "port": port,
            "pid": 1,
            "started_at": "x",
            "token": "attacker",
        },
    )


def test_forged_ok_reply_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    fake = _Fake(lambda req: {"ok": True})
    try:
        _plant(root, port=fake.port)
        assert sd._probe_daemon(root) is None
    finally:
        fake.close()


def test_forged_proof_with_wrong_secret_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    sd._load_or_create_user_secret()

    holder: dict[str, int] = {}

    def reply(req: dict[str, Any]) -> dict[str, Any]:
        port = holder["port"]
        proof = sd._daemon_ping_proof(b"x" * 32, req["nonce"], 1, str(root), port)
        return {"ok": True, "pid": 1, "root": str(root), "port": port, "proof": proof}

    fake = _Fake(reply)
    holder["port"] = fake.port
    try:
        _plant(root, port=fake.port)
        assert sd._probe_daemon(root) is None
    finally:
        fake.close()


def test_non_loopback_host_is_never_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Record calls instead of raising: _probe_daemon swallows exceptions, so a raising canary
    # would pass on broken code (vacuous). Recording proves the request was never attempted.
    root = tmp_path.resolve()
    _plant(root, host="10.255.255.1", port=9)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(sd, "_daemon_request", lambda *a, **k: calls.append(a) or {"ok": True})
    assert sd._probe_daemon(root) is None
    assert calls == []


def test_relayed_proof_for_a_different_port_is_rejected(tmp_path: Path) -> None:
    # A loopback relay forwards the client's nonce to the GENUINE daemon and returns its valid
    # proof. The proof binds the genuine daemon's own listening port, which differs from the
    # relay's port the client connected to -> must be rejected.
    root = tmp_path.resolve()
    secret = sd._load_or_create_user_secret()
    assert secret is not None
    genuine_port = 1  # any port != the relay's

    def reply(req: dict[str, Any]) -> dict[str, Any]:
        proof = sd._daemon_ping_proof(secret, req["nonce"], 1, str(root), genuine_port)
        return {"ok": True, "pid": 1, "root": str(root), "port": genuine_port, "proof": proof}

    relay = _Fake(reply)
    try:
        _plant(root, port=relay.port)
        assert sd._probe_daemon(root) is None
    finally:
        relay.close()


@pytest.mark.parametrize("host", ["example.com", "10.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_daemon_request_refuses_any_host_but_the_canonical_bind(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    # Record instead of connecting: RED on main must not make a real outbound connection (council round 2).
    calls: list[Any] = []

    def _record(*a: Any, **k: Any) -> Any:
        calls.append(a)
        raise OSError("blocked by test")

    monkeypatch.setattr(socket, "create_connection", _record)
    with pytest.raises(ValueError):
        sd._daemon_request(host, 80, {"command": "ping"})
    assert calls == []


def test_same_port_relay_on_another_loopback_address_is_never_contacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Council round 2: a relay on 127.0.0.2 at the GENUINE daemon's port would carry a valid
    # port-bound proof; only the canonical bind host 127.0.0.1 is ever connected to.
    # Council round 3: record calls (a refused connection is swallowed by _probe_daemon, so
    # asserting only `is None` would be vacuous).
    root = tmp_path.resolve()
    _plant(root, host="127.0.0.2", port=45678)
    calls: list[Any] = []
    monkeypatch.setattr(sd, "_daemon_request", lambda *a, **k: calls.append(a) or {"ok": True})
    assert sd._probe_daemon(root) is None
    assert calls == []


def test_malformed_planted_port_is_rejected_not_crash(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": "not-a-port",
            "pid": 1,
            "started_at": "x",
            "token": "t",
        },
    )
    assert sd._probe_daemon(root) is None


def test_genuine_daemon_is_still_accepted(tmp_path: Path) -> None:
    # POSITIVE CONTROL: without it the tests above would pass against a probe that rejects everything.
    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sd._write_daemon_metadata(
            root,
            {
                "version": 1,
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": "127.0.0.1",
                "port": int(server.server_address[1]),
                "pid": 0,
                "started_at": "x",
                "token": "tok",
            },
        )
        assert sd._probe_daemon(root) is not None
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_group_readable_secret_is_not_trusted(tmp_path: Path) -> None:
    sd._load_or_create_user_secret()
    path = sd._daemon_secret_path()
    os.chmod(path, 0o640)
    assert sd._read_user_secret(path) is None


def test_symlinked_secret_is_not_trusted(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"secret": "a" * 64}), encoding="utf-8")
    link = sd._daemon_secret_path()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    assert sd._read_user_secret(link) is None


def _verify(tmp_path: Path, **overrides: Any) -> bool:
    root = tmp_path.resolve()
    secret = sd._load_or_create_user_secret()
    assert secret is not None
    nonce = "n" * 32
    reply: dict[str, Any] = {
        "ok": True,
        "pid": 7,
        "root": str(root),
        "port": 4242,
        "proof": sd._daemon_ping_proof(secret, nonce, 7, str(root), 4242),
    }
    reply.update(overrides)
    return bool(sd._verify_ping_reply(reply, nonce, root, 4242))


def test_verify_ping_reply_accepts_a_correct_proof_and_rejects_each_tamper(tmp_path: Path) -> None:
    assert _verify(tmp_path) is True  # positive control for the rejections below
    assert _verify(tmp_path, root=str(tmp_path.resolve() / "elsewhere")) is False
    assert _verify(tmp_path, pid=8) is False
    assert _verify(tmp_path, pid=True) is False
    assert _verify(tmp_path, port=4243) is False
    assert _verify(tmp_path, proof="é" * 64) is False  # non-ASCII must not raise TypeError
    assert _verify(tmp_path, proof=None) is False
