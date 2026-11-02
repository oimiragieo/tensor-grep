"""Root forwarding requires an unconfirmed endpoint; an acknowledged stop alone is insufficient."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tensor_grep.cli import session_daemon as sd


@pytest.mark.parametrize("refused", [True, False], ids=["cooperative-refused", "root-forwarded"])
def test_probed_stop_forwards_root_when_confirmation_requires_it(
    monkeypatch: pytest.MonkeyPatch, refused: bool
) -> None:
    root = Path("controlled-root")
    metadata = {"host": "mock-host.invalid", "port": None}
    original_metadata = dict(metadata)
    events = []
    probes = iter((metadata, None))
    ticks = iter((0.0, 0.0))

    def unexpected(*args, **kwargs):
        raise AssertionError("unexpected side-effect or rejected route")

    def probe(received_root):
        assert received_root == root
        reply = next(probes)
        events.append(("probe", reply is metadata))
        return reply

    def request(host, port, payload, *, token):
        assert (host, port, payload, token) == (
            "mock-host.invalid",
            None,
            {"command": "stop"},
            None,
        )
        events.append(("request",))
        return {"ok": True}

    def refusal(host, port, timeout):
        assert (host, port, timeout) == (
            "mock-host.invalid",
            None,
            sd._DAEMON_START_TIMEOUT_SECONDS,
        )
        events.append(("refused", refused))
        return refused

    def terminate(received_metadata, **kwargs):
        assert received_metadata is metadata
        assert kwargs == {"root": root}, "missing or incorrect root forwarding"
        events.append(("terminate",))
        return False

    def state(received_metadata, received_root):
        assert received_metadata is metadata and received_root == root
        events.append(("state",))
        return "unverifiable"

    resolver = Mock(return_value=root)
    token = Mock(return_value=None)
    monkeypatch.setattr(sd, "_resolve_root", resolver)
    monkeypatch.setattr(sd, "_probe_daemon", probe)
    monkeypatch.setattr(sd, "_daemon_token", token)
    monkeypatch.setattr(sd, "_daemon_request", request)
    monkeypatch.setattr(sd, "_await_endpoint_refused", refusal)
    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", terminate)
    monkeypatch.setattr(sd, "_daemon_pid_state", state)
    monkeypatch.setattr(sd, "time", SimpleNamespace(time=lambda: next(ticks), sleep=unexpected))
    for name in (
        "_stop_rejected_daemon",
        "_metadata_state",
        "_read_daemon_metadata",
        "_write_daemon_metadata",
        "_remove_daemon_metadata",
    ):
        monkeypatch.setattr(sd, name, unexpected)
    result = sd.stop_session_daemon("input-root")
    resolver.assert_called_once_with(Path("input-root"))
    token.assert_called_once_with(metadata)
    assert metadata == original_metadata
    assert next(probes, "exhausted") == "exhausted"
    assert next(ticks, "exhausted") == "exhausted"
    expected = [("probe", True), ("request",), ("probe", False), ("refused", refused)]
    if not refused:
        expected += [("terminate",), ("refused", False), ("state",)]
    assert events == expected
    assert result["root"] == str(root)
    assert result["running"] is (not refused)
    assert result["stopped"] is refused
    assert result["stop_method"] == ("cooperative" if refused else "none")
    if refused:
        assert result["proof"] == "cooperative_refused"
