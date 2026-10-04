"""A failed stale-session rebuild must be reported as ``refresh_failed`` with its trigger (F-03).

Before: when ``refresh_on_stale`` rebuilt after an exception and the rebuild itself failed, the
outer handler emitted ``invalid_request`` with only the rebuild's message -- the original trigger
was lost and the code was mislabeled as a client error.
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
    second_attempt_raises: Exception | None = None,
    refresh_session_impl: Any = None,
) -> dict[str, Any]:
    # Extended copy of test_session_daemon_refresh_disclosure._drive (pytest runs with
    # --import-mode=importlib and tests/ is not a package, so test modules cannot import each other).
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "payments.py").write_text("def create_invoice():\n    return 1\n", "utf-8")
    opened = json.loads(CliRunner().invoke(app, ["session", "open", str(project), "--json"]).stdout)

    attempts = {"count": 0}

    def _fake_serve(**_kwargs: Any) -> tuple[dict[str, Any], str]:
        attempts["count"] += 1
        if attempts["count"] == 1 and first_attempt_raises is not None:
            raise first_attempt_raises
        if attempts["count"] == 2 and second_attempt_raises is not None:
            raise second_attempt_raises
        return {"session_id": opened["session_id"], "routing_reason": "test"}, "bypass"

    monkeypatch.setattr(session_daemon, "_serve_daemon_response_with_cache", _fake_serve)
    monkeypatch.setattr(
        session_daemon,
        "refresh_session",
        refresh_session_impl if refresh_session_impl is not None else (lambda *a, **k: {}),
    )

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


def _raise(exc: BaseException):
    def _f(*_a: Any, **_k: Any) -> Any:
        raise exc

    return _f


def test_rebuild_failure_discloses_trigger_and_both_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = _drive(
        tmp_path,
        monkeypatch,
        first_attempt_raises=KeyError("orig"),
        refresh_session_impl=_raise(OSError("disk gone")),
    )["error"]
    assert err["code"] == "refresh_failed"
    assert err["refresh_trigger"] == "KeyError"
    assert "orig" in err["original_error"]
    assert "disk gone" in err["rebuild_error"]


def test_failure_of_the_post_rebuild_serve_is_also_refresh_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = _drive(
        tmp_path,
        monkeypatch,
        first_attempt_raises=KeyError("orig"),
        second_attempt_raises=RuntimeError("serve again failed"),
    )["error"]
    assert err["code"] == "refresh_failed"
    assert "serve again failed" in err["rebuild_error"]


def test_without_refresh_on_stale_error_code_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    err = _drive(
        tmp_path, monkeypatch, first_attempt_raises=KeyError("orig"), refresh_on_stale=False
    )["error"]
    assert err["code"] == "invalid_request"
