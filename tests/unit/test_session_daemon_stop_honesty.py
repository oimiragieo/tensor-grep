"""`stop_session_daemon` must not report `stopped: True` for a daemon it has no evidence ended.

The cooperative path dispatches a `stop` request and then polls `_probe_daemon` until it returns
`None`. A `None` probe is ambiguous: the daemon exited, OR it stopped answering (wedged). The
`while ... else` escalation to a pid terminate only runs when the poll EXHAUSTS the deadline, so
when the `stop` request itself failed and the daemon then went unresponsive, the loop broke on the
first `None` probe and the function reported `stopped: True`, `stop_method: "none"` -- while the
process was still alive and its `daemon.json` had just been removed (undiscoverable until its
idle/max-uptime shutdown). The sibling branch for "probe is None up front" already reports
`stopped: <did we terminate it>`; this pins the same honesty on the cooperative tail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon


def _metadata(*, pid: int = 4321, port: int = 2222) -> dict[str, Any]:
    return {
        "version": 1,
        "package_version": "irrelevant-for-these-tests",
        "root": "irrelevant",
        "host": "127.0.0.1",
        "port": port,
        "pid": pid,
        "started_at": "test",
        "token": "tok",
    }


def _stop_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stop_request_raises: bool,
    terminate_returns: bool,
) -> tuple[dict[str, Any], list[int]]:
    """Drive stop_session_daemon: the first probe finds a daemon, every later probe finds none."""
    root = (tmp_path / "project").resolve()
    root.mkdir()
    metadata = _metadata()
    probe_calls = {"count": 0}
    terminate_calls: list[int] = []

    def _fake_probe(_root: Path) -> dict[str, Any] | None:
        probe_calls["count"] += 1
        return metadata if probe_calls["count"] == 1 else None

    def _fake_request(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if stop_request_raises:
            raise OSError("connection reset while delivering stop")
        return {"version": 1, "ok": True, "stopping": True}

    def _fake_terminate(meta: dict[str, Any] | None) -> bool:
        terminate_calls.append(int((meta or {}).get("pid", -1)))
        return terminate_returns

    monkeypatch.setattr(session_daemon, "_probe_daemon", _fake_probe)
    monkeypatch.setattr(session_daemon, "_daemon_request", _fake_request)
    monkeypatch.setattr(session_daemon, "_terminate_daemon_by_pid", _fake_terminate)
    return session_daemon.stop_session_daemon(str(root)), terminate_calls


def test_failed_stop_request_and_unresponsive_daemon_is_not_reported_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result, terminate_calls = _stop_with(
        monkeypatch, tmp_path, stop_request_raises=True, terminate_returns=False
    )

    # we tried to end it by pid (identity-validated) and could not, so we have NO evidence it ended
    assert terminate_calls == [4321]
    assert result["stopped"] is False, result
    assert result["stop_method"] == "none"


def test_failed_stop_request_is_escalated_to_a_pid_terminate_that_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result, terminate_calls = _stop_with(
        monkeypatch, tmp_path, stop_request_raises=True, terminate_returns=True
    )

    assert terminate_calls == [4321]
    assert result["stopped"] is True
    assert result["stop_method"] == "pid"


def test_a_cooperative_stop_that_succeeded_does_not_terminate_anything(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # CONTROL: the happy path is unchanged -- the stop request was accepted and the daemon went
    # away, so no pid terminate is attempted and the result is stopped / cooperative.
    result, terminate_calls = _stop_with(
        monkeypatch, tmp_path, stop_request_raises=False, terminate_returns=True
    )

    assert terminate_calls == []
    assert result["stopped"] is True
    assert result["stop_method"] == "cooperative"
