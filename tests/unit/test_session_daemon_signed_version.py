"""The version-skew stop trusts only the version the daemon SIGNED in its ping reply.

``daemon.json`` is repo-controlled: a current-version daemon whose metadata claims an old
``package_version`` must NOT be stopped through the skew path (Codex audit of PR #1207).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1.0)


class _Daemon:
    def __init__(self, root: Path, metadata_version: str) -> None:
        self.server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    monkeypatch.setattr(
        session_daemon, "_ping_proof_fields", _without_version_fields(trust._ping_proof_fields)
    )
    daemon = _Daemon(root, metadata_version="OLD")
    try:
        result = session_daemon.stop_session_daemon(str(root))
        assert result["stopped"] is True and result["stop_method"] == "cooperative", result
    finally:
        daemon.close()


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
