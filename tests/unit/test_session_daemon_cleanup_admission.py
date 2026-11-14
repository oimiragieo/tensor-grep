"""Cleanup admission deadlines and accepted command spelling survive implicit recovery."""

import io
import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from tensor_grep.cli import session_daemon as sd
from tests.unit.test_session_daemon_active_payload_lifetime import owned_daemon as owned_daemon


@pytest.mark.parametrize("phase", ["expired-before-lock", "expires-after-lock", "live"])
def test_implicit_cleanup_observes_deadline_before_filesystem_side_effects(
    tmp_path, monkeypatch, phase
):
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="owned"\nversion="0.0.0"\n')
    session_id = "cleanup-owned"
    payload = sd._session_payload_path(root, session_id)
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_text("{}")
    writes = []
    monkeypatch.setattr(sd, "_load_index", lambda root: [])
    monkeypatch.setattr(sd, "_write_index", lambda *args: writes.append(args))
    now = time.monotonic()
    deadline = now - 1 if phase == "expired-before-lock" else now + 10
    clock = {"now": now}
    monkeypatch.setattr(sd, "monotonic", lambda: clock["now"])
    real_index_lock = sd.index_lock
    acquired = []

    if phase == "expires-after-lock":

        @contextmanager
        def acquire_then_expire(*args, **kwargs):
            with real_index_lock(*args, **kwargs):
                acquired.append(True)
                clock["now"] = deadline + 1
                yield

        monkeypatch.setattr(sd, "index_lock", acquire_then_expire)

    sd._remove_implicit_session_payload(str(root), session_id, deadline_monotonic=deadline)
    if phase == "live":
        assert not payload.exists(), "live cleanup positive control did not remove owned leaf"
        assert len(writes) == 1
    else:
        if phase == "expires-after-lock":
            assert acquired == [True], "controlled clock did not advance inside acquired lock"
        assert payload.exists(), "expired cleanup still unlinked an owned payload"
        assert not writes, "expired cleanup still rewrote the session index"


@pytest.mark.parametrize("command", ["context_render", " CONTEXT_RENDER "])
def test_accepted_command_spelling_recovers_exact_missing_implicit_payload(
    owned_daemon, monkeypatch, command
):
    owned = owned_daemon
    opens, loads = [], []

    def opener(path, **kwargs):
        result = owned.opener(path, **kwargs)
        opens.append((result.session_id, kwargs["deadline_monotonic"]))
        return result

    def lose_first(cache, session_id, path, **kwargs):
        loads.append(session_id)
        if len(loads) == 1:
            owned.payloads[session_id].unlink()
        return owned.load(cache, session_id, path, **kwargs)

    monkeypatch.setattr(sd, "open_session", opener)
    monkeypatch.setattr(sd, "_load_payload_with_status_retry", lose_first)
    handler = sd._SessionDaemonHandler.__new__(sd._SessionDaemonHandler)
    handler.server = owned.server
    handler.connection = SimpleNamespace(settimeout=lambda value: None, close=lambda: None)
    handler.rfile = io.BytesIO(
        (
            json.dumps({
                "command": command,
                "path": str(owned.root),
                "max_repo_files": 20,
                "token": "owned-token",
            })
            + "\n"
        ).encode()
    )
    handler.wfile = io.BytesIO()
    sd._SessionDaemonHandler.handle(handler)
    response = json.loads(handler.wfile.getvalue())
    assert not any((
        owned.server._implicit_active_pins,
        owned.server._implicit_pending_evictions,
        owned.server._implicit_inflight,
        owned.server._implicit_cleanup_owned,
    )), "handler did not drain auxiliary lease state"
    assert response.get("ok") is True, response
    assert len(opens) == 2
    assert opens[0][0] != opens[1][0]
    assert opens[0][1] == opens[1][1], "command recovery reset original work deadline"
    assert loads == [opens[0][0], opens[1][0]]
