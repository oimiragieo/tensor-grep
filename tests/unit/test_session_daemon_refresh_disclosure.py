"""The daemon's rebuild-on-error path must say WHY it rebuilt.

With `refresh_on_stale`, `_ThreadedSessionDaemon`'s request handler catches ANY exception from the
first serve attempt (not only `SessionStaleError`), rebuilds the session and serves again, logging
nothing and adding no marker to the response. A non-staleness bug is therefore masked by a full
rebuild, and a persistently failing serve silently costs one rebuild per request. The behaviour is
kept (narrowing it risks regressing legitimate recoveries from a missing/corrupt payload), but it is
now disclosed: `serve_cache.refresh_trigger` names the exception class that caused the rebuild.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import session_daemon
from tensor_grep.cli.main import app


def _drive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    first_attempt_raises: Exception | None,
    refresh_on_stale: bool = True,
) -> dict[str, Any]:
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "payments.py").write_text("def create_invoice():\n    return 1\n", "utf-8")
    opened = json.loads(CliRunner().invoke(app, ["session", "open", str(project), "--json"]).stdout)

    attempts = {"count": 0}

    def _fake_serve(**_kwargs: Any) -> tuple[dict[str, Any], str]:
        attempts["count"] += 1
        if attempts["count"] == 1 and first_attempt_raises is not None:
            raise first_attempt_raises
        return {"session_id": opened["session_id"], "routing_reason": "test"}, "bypass"

    monkeypatch.setattr(session_daemon, "_serve_daemon_response_with_cache", _fake_serve)
    monkeypatch.setattr(session_daemon, "refresh_session", lambda *a, **k: {})

    server = session_daemon._ThreadedSessionDaemon(project.resolve(), ("127.0.0.1", 0), token="tok")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return session_daemon._daemon_request(
            str(server.server_address[0]),
            int(server.server_address[1]),
            {
                "command": "context_edit_plan",
                "session_id": opened["session_id"],
                "path": str(project),
                "query": "create invoice",
                "refresh_on_stale": refresh_on_stale,
            },
            token="tok",
        )
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()


def test_a_rebuild_triggered_by_a_non_staleness_error_names_the_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _drive(tmp_path, monkeypatch, first_attempt_raises=KeyError("surprise"))

    assert response["serve_cache"]["refresh_trigger"] == "KeyError"


def test_a_request_that_needs_no_rebuild_carries_no_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CONTROL: the normal path is unchanged -- no marker, same serve_cache shape as before.
    response = _drive(tmp_path, monkeypatch, first_attempt_raises=None)

    assert "refresh_trigger" not in response["serve_cache"]
    assert {"status", "session_count", "root_count"} <= set(response["serve_cache"])
