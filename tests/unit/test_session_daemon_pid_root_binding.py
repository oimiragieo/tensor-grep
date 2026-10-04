"""A kill by recorded pid must only ever hit a daemon that serves THIS root (PR #1197 residual).

``_pid_looks_like_tg_daemon`` checked the command line for the daemon module but not the root, so
a stale/planted daemon.json for root A naming root B's live daemon pid made A's ``stop`` signal B.
The candidate's argv must carry ``--root <R>`` with R resolving to the SAME canonical root as the
one being stopped; if psutil is missing or the argv cannot be read, the client refuses to signal
(never "signal anyway") and reports the stop as unconfirmed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_MODULE = "tensor_grep.cli.session_daemon"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.3)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _plant(root: Path, pid: int) -> None:
    sd._write_daemon_metadata(
        root,
        {
            "version": 1,
            "package_version": _expected_tg_version(),
            "root": str(root),
            "host": "127.0.0.1",
            "port": _free_port(),  # nothing listens: the probe fails -> stale-metadata branch
            "pid": pid,
            "started_at": "x",
            "token": "t",
        },
    )


def _decoy(*argv_tail: str) -> subprocess.Popen[bytes]:
    # argv looks like a tensor-grep daemon: python -c ... tensor_grep.cli.session_daemon --root R
    return subprocess.Popen([
        sys.executable,
        "-c",
        "import time; time.sleep(120)",
        _MODULE,
        *argv_tail,
    ])


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _fake_psutil_cmdline(proc: subprocess.Popen[bytes]) -> Callable[[int], list[str]]:
    def _cmdline(pid: int) -> list[str]:
        if pid != proc.pid:
            raise LookupError(pid)
        return [str(a) for a in proc.args]  # type: ignore[union-attr]

    return _cmdline


# ---- real psutil, real process (skipped where psutil is not installed) ----


def test_live_decoy_serving_a_different_root_is_not_signalled(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    other = (tmp_path / "rootB").resolve()
    other.mkdir()
    victim = _decoy("--root", str(other))
    try:
        _plant(root, victim.pid)
        result = sd.stop_session_daemon(str(root))
        assert victim.poll() is None, "root B's daemon was signalled by root A's stop"
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert sd._read_daemon_metadata(root) is not None  # metadata kept
    finally:
        _reap(victim)


def test_live_decoy_serving_the_same_root_is_signalled_control(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    mine = _decoy("--root", str(root))
    try:
        _plant(root, mine.pid)
        result = sd.stop_session_daemon(str(root))
        mine.wait(timeout=10)  # it really was terminated
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None
    finally:
        _reap(mine)


# ---- the same contract through the argv seam (runs everywhere, real processes, real signals) ----


def test_seam_different_root_refused_and_same_root_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    other = (tmp_path / "rootB").resolve()
    other.mkdir()
    victim = _decoy("--root", str(other))
    mine = _decoy("--root", str(root))
    try:
        monkeypatch.setattr(
            trust,
            "_process_cmdline",
            lambda pid: _fake_psutil_cmdline(victim if pid == victim.pid else mine)(pid),
        )
        assert sd._terminate_daemon_by_pid({"pid": victim.pid}, root=root) is False
        assert victim.poll() is None
        assert sd._terminate_daemon_by_pid({"pid": mine.pid}, root=root) is True
        mine.wait(timeout=10)
    finally:
        _reap(victim)
        _reap(mine)


@pytest.mark.parametrize("form", ["equals", "relative", "case"])
def test_root_spellings_that_resolve_to_the_same_root_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    if form == "equals":
        argv = ["--root=" + str(root)]
    elif form == "relative":
        monkeypatch.chdir(tmp_path)
        argv = ["--root", os.path.join(".", "rootA")]
    else:
        if sys.platform != "win32":
            pytest.skip("case-insensitive roots are a Windows property")
        argv = ["--root", str(root).swapcase()]
    mine = _decoy(*argv)
    try:
        monkeypatch.setattr(trust, "_process_cmdline", _fake_psutil_cmdline(mine))
        assert sd._terminate_daemon_by_pid({"pid": mine.pid}, root=root) is True
        mine.wait(timeout=10)
    finally:
        _reap(mine)


@pytest.mark.parametrize(
    "argv_tail",
    [[], ["--root"], ["--other", "x"], ["--root", "", "--root", "elsewhere"]],
    ids=["no-root", "dangling-root", "no-root-flag", "wrong-root"],
)
def test_argv_without_a_matching_root_is_never_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv_tail: list[str]
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    victim = _decoy(*argv_tail)
    try:
        monkeypatch.setattr(trust, "_process_cmdline", _fake_psutil_cmdline(victim))
        assert sd._terminate_daemon_by_pid({"pid": victim.pid}, root=root) is False
        assert victim.poll() is None
    finally:
        _reap(victim)


def test_a_root_argument_is_required_to_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    victim = _decoy("--root", str(root))
    try:
        monkeypatch.setattr(trust, "_process_cmdline", _fake_psutil_cmdline(victim))
        assert sd._terminate_daemon_by_pid({"pid": victim.pid}) is False  # no root: fail closed
        assert victim.poll() is None
    finally:
        _reap(victim)


@pytest.mark.parametrize("error", [PermissionError("access denied"), OSError("no psutil")])
def test_an_unreadable_argv_never_signals_and_reports_an_unconfirmed_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    victim = _decoy("--root", str(root))  # genuinely OUR daemon, but we cannot prove it
    try:

        def _denied(_pid: int) -> list[str]:
            raise error

        monkeypatch.setattr(trust, "_process_cmdline", _denied)
        _plant(root, victim.pid)
        result = sd.stop_session_daemon(str(root))
        assert victim.poll() is None
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert sd._read_daemon_metadata(root) is not None
    finally:
        _reap(victim)


def test_a_pid_that_no_longer_exists_still_cleans_up_stale_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CONTROL for the refusals above: a dead pid is not "unverifiable"; stale metadata is removed.
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    _plant(root, 999_999)

    def _gone(pid: int) -> list[str]:
        raise LookupError(pid)

    monkeypatch.setattr(trust, "_process_cmdline", _gone)
    result = sd.stop_session_daemon(str(root))
    assert result["running"] is False
    assert result["stop_method"] == "none"
    assert sd._read_daemon_metadata(root) is None


def test_the_probed_branch_passes_its_root_to_the_terminate_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    root = tmp_path.resolve()
    server = sd._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
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
            "pid": 1,
            "started_at": "x",
            "token": "tok",
        },
    )
    monkeypatch.setattr(server, "shutdown", lambda: None)  # ack stop, keep serving
    seen: list[Any] = []

    def _record(metadata: Any, **kwargs: Any) -> bool:
        seen.append(kwargs.get("root"))
        return False

    monkeypatch.setattr(sd, "_terminate_daemon_by_pid", _record)
    try:
        sd.stop_session_daemon(str(root))
    finally:
        real_shutdown()
        server.server_close()
    assert seen
    assert all(Path(str(r)) == root for r in seen), seen
