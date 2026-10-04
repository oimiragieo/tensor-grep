"""``"gone"`` means ONLY independently established absence (PR #1197 round 4).

A live process that is unrelated, is the caller's own pid, or has a malformed recorded identity is
``"unverifiable"``: it proves nothing about the daemon the metadata names, so stale metadata is KEPT
and the stop is reported unconfirmed (``running True``, ``pid_unproven``). Before, a genuine daemon
whose metadata token had changed (so the ping failed to authenticate) plus a recorded pid changed to
a live ``python -c`` sleeper was classified ``"gone"``: stop deleted daemon.json and reported
``running=False`` while the real daemon was still listening.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.4)


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _serve(root: Path, *, server_token: str, metadata_token: str, pid: int) -> tuple[Any, Any]:
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token=server_token)
    real_shutdown = server.shutdown
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": int(server.server_address[1]),
            "pid": pid,
            "started_at": "x",
            "token": metadata_token,
        },
    )
    return server, real_shutdown


def test_a_live_unrelated_sleeper_pid_does_not_make_a_listening_daemon_look_gone(
    tmp_path: Path,
) -> None:
    pytest.importorskip("psutil")
    root = tmp_path.resolve()
    sleeper = _sleeper()
    # the genuine listener's token no longer matches the metadata, so the ping cannot authenticate
    server, real_shutdown = _serve(
        root, server_token="real-token", metadata_token="changed-token", pid=sleeper.pid
    )
    try:
        assert sd._probe_daemon(root) is None  # precondition: the ping fails to authenticate
        result = sd.stop_session_daemon(str(root))
        assert sleeper.poll() is None
        assert result["running"] is True, "a listening daemon was reported as not running"
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert result["unconfirmed_reason"] == "pid_unproven"
        assert sd._read_daemon_metadata(root) is not None  # daemon.json kept
    finally:
        real_shutdown()
        server.server_close()
        _reap(sleeper)


def test_a_live_unrelated_process_is_unverifiable_not_gone(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    sleeper = _sleeper()
    try:
        assert trust._daemon_pid_state({"pid": sleeper.pid}, tmp_path) == "unverifiable"
    finally:
        _reap(sleeper)


def test_the_callers_own_pid_is_unverifiable_not_gone(tmp_path: Path) -> None:
    assert trust._daemon_pid_state({"pid": os.getpid()}, tmp_path) == "unverifiable"
    assert sd._terminate_daemon_by_pid({"pid": os.getpid()}, root=tmp_path) is False


@pytest.mark.parametrize("pid", [None, "abc", "", 0, -5, 3.5, [1], True, float("inf")], ids=repr)
def test_a_malformed_recorded_identity_is_unverifiable_not_gone(tmp_path: Path, pid: Any) -> None:
    metadata = {"port": 4242} if pid is None else {"pid": pid, "port": 4242}
    assert trust._daemon_pid_state(metadata, tmp_path) == "unverifiable"


def test_no_metadata_at_all_is_still_gone(tmp_path: Path) -> None:
    # CONTROL: there is nothing recorded, hence nothing alive to be unsure about.
    assert trust._daemon_pid_state(None, tmp_path) == "gone"
    assert trust._daemon_pid_state({}, tmp_path) == "gone"


def test_a_truly_dead_pid_is_gone_and_stale_metadata_is_cleaned(tmp_path: Path) -> None:
    psutil = pytest.importorskip("psutil")
    root = tmp_path.resolve()
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(timeout=30)
    if psutil.pid_exists(done.pid):
        pytest.skip("the pid was recycled before the probe")
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": 1,
            "pid": done.pid,
            "started_at": "x",
            "token": "t",
        },
    )
    assert trust._daemon_pid_state(sd._read_daemon_metadata(root), root) == "gone"
    result = sd.stop_session_daemon(str(root))
    assert result["running"] is False
    assert "unconfirmed_reason" not in result
    assert sd._read_daemon_metadata(root) is None


def test_no_psutil_means_unverifiable_never_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_psutil(pid: int) -> tuple[list[str], float]:
        raise OSError("psutil unavailable")

    monkeypatch.setattr(trust, "_process_info", _no_psutil)
    assert trust._daemon_pid_state({"pid": 4242}, tmp_path) == "unverifiable"
