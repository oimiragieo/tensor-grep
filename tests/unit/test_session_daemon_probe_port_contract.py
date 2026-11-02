"""Benign in-memory port/authentication controls; no socket or credential IO."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust


@pytest.mark.parametrize(
    "case", ["valid", "timeout", "bad-proof", "stale", "bool", "string", "none"]
)
def test_probe_port_contract(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    root, key = Path("logical-root"), b"in-memory-test-key"
    port = {"bool": True, "string": "4242", "none": None}.get(case, 4242)
    metadata = {
        "host": "127.0.0.1",
        "port": port,
        "token": "test-token",
        "package_version": "stale" if case == "stale" else sd._expected_tg_version(),
    }
    original = dict(metadata)

    def reply(host, connected, request, **kwargs):
        if case == "timeout":
            raise TimeoutError
        nonce = request["nonce"]
        return {
            "ok": True,
            "pid": 77,
            "root": str(root),
            "port": 4242,
            "proof": "0" * 64
            if case == "bad-proof"
            else trust._daemon_ping_proof(key, nonce, 77, str(root), 4242),
        }

    request = Mock(side_effect=reply)
    monkeypatch.setattr(
        sd, "_read_daemon_metadata", lambda received: metadata if received == root else None
    )
    monkeypatch.setattr(sd, "_daemon_request", request)
    monkeypatch.setattr(trust, "_daemon_secret_path", lambda: Path("unused-key-path"))
    monkeypatch.setattr(trust, "_read_user_secret", lambda path: key)
    result = sd._probe_daemon(root)
    if case in {"bool", "string", "none"}:
        request.assert_not_called()
    else:
        assert request.call_count == 1
        args, kwargs = request.call_args
        assert args[:2] == ("127.0.0.1", 4242), "incorrect forwarded port"
        assert set(args[2]) == {"command", "nonce"} and args[2]["command"] == "ping"
        assert len(args[2]["nonce"]) == 32
        assert kwargs == {
            "response_timeout": sd._DAEMON_CONNECT_TIMEOUT_SECONDS,
            "token": "test-token",
        }
    assert metadata == original
    assert (result is not None) is (case == "valid")
    if result is not None:
        assert result == {**metadata, "pid": 77}
