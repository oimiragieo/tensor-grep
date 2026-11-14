"""Bound stale implicit payload recovery through the existing authenticated handler."""

import errno
import io
import json
from types import SimpleNamespace

import pytest

from tensor_grep.cli import session_daemon as sd
from tests.unit.test_session_daemon_active_payload_lifetime import _handle
from tests.unit.test_session_daemon_active_payload_lifetime import owned_daemon as owned_daemon


def _observe_opens(owned, monkeypatch):
    calls = []

    def opener(path, **kwargs):
        result = owned.opener(path, **kwargs)
        calls.append((result.session_id, kwargs.get("deadline_monotonic")))
        return result

    monkeypatch.setattr(sd, "open_session", opener)
    return calls


def test_missing_owned_implicit_payload_reopens_once_with_original_deadline(
    owned_daemon, monkeypatch
):
    owned = owned_daemon
    calls = _observe_opens(owned, monkeypatch)
    loads = []

    def lose_first_payload(cache, session_id, path, **kwargs):
        loads.append(session_id)
        if len(loads) == 1:
            owned.payloads[session_id].unlink()
        return owned.load(cache, session_id, path, **kwargs)

    monkeypatch.setattr(sd, "_load_payload_with_status_retry", lose_first_payload)
    response = _handle(owned, 20)
    assert response.get("ok") is True, response
    assert len(calls) == 2, "missing implicit payload was not reacquired exactly once"
    assert calls[0][0] != calls[1][0]
    assert calls[0][1] == calls[1][1], "reacquisition reset the original work deadline"
    assert loads == [calls[0][0], calls[1][0]]
    assert owned.payloads[calls[1][0]].exists()


def test_repeated_owned_payload_loss_returns_existing_error_with_at_most_one_reopen(
    owned_daemon, monkeypatch
):
    owned = owned_daemon
    calls = _observe_opens(owned, monkeypatch)

    def lose_every_payload(cache, session_id, path, **kwargs):
        owned.payloads[session_id].unlink()
        return owned.load(cache, session_id, path, **kwargs)

    monkeypatch.setattr(sd, "_load_payload_with_status_retry", lose_every_payload)
    response = _handle(owned, 20)
    assert response["error"]["code"] == "invalid_request"
    assert len(calls) <= 2, "repeated payload loss caused unbounded reacquisition"
    assert all(deadline == calls[0][1] for _, deadline in calls)


@pytest.mark.parametrize("failure", ["root-mismatch", "permission", "unrelated-missing"])
def test_non_retryable_load_failure_does_not_reopen_implicit_session(
    owned_daemon, monkeypatch, failure
):
    owned = owned_daemon
    calls = _observe_opens(owned, monkeypatch)

    def refuse_load(cache, session_id, path, **kwargs):
        payload_path = owned.payloads[session_id]
        if failure == "root-mismatch":
            raise ValueError(f"Session root mismatch for {session_id}: controlled fixture")
        if failure == "permission":
            raise PermissionError(errno.EACCES, "controlled permission failure", str(payload_path))
        raise FileNotFoundError(
            errno.ENOENT, "unrelated missing fixture", str(owned.root / "unrelated.json")
        )

    monkeypatch.setattr(sd, "_load_payload_with_status_retry", refuse_load)
    response = _handle(owned, 20)
    assert response["error"]["code"] == "invalid_request"
    assert len(calls) == 1, "non-payload-loss failure was retried as a stale implicit ID"


def test_explicit_health_missing_payload_does_not_create_implicit_session(
    owned_daemon, monkeypatch
):
    owned = owned_daemon
    opened = owned.opener(str(owned.root))
    owned.payloads[opened.session_id].unlink()
    calls = _observe_opens(owned, monkeypatch)
    handler = sd._SessionDaemonHandler.__new__(sd._SessionDaemonHandler)
    handler.server = owned.server
    handler.connection = SimpleNamespace(settimeout=lambda value: None, close=lambda: None)
    handler.rfile = io.BytesIO(
        (
            json.dumps({
                "command": "health",
                "session_id": opened.session_id,
                "path": str(owned.root),
                "token": "owned-token",
            })
            + "\n"
        ).encode()
    )
    handler.wfile = io.BytesIO()
    sd._SessionDaemonHandler.handle(handler)
    response = json.loads(handler.wfile.getvalue())
    assert response["error"]["code"] == "invalid_request"
    assert not calls, "explicit health request unexpectedly created an implicit session"
