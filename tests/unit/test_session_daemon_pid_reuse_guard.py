"""PID reuse is closed by a kernel object, not by a check-then-signal (PR #1197 round 3).

The create_time re-check leaves a window between "verified" and "signalled" in which the pid can be
recycled. A process HANDLE (Windows) or a pidfd (Linux 5.3+) pins the process: while it is open the
pid cannot name another process, so verifying through it and signalling through it hits exactly the
verified process. Where neither exists (macOS, old kernels) the create_time re-check remains and the
stop result says so: ``pid_reuse_guard`` is ``"handle"``, ``"pidfd"`` or ``"recheck"``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_MODULE = "tensor_grep.cli.session_daemon"


def _pidfd_works() -> bool:
    """True where pidfd_open/pidfd_send_signal exist AND the kernel/sandbox allows them."""
    import signal

    if not (hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")):
        return False
    try:
        os.close(os.pidfd_open(os.getpid()))
    except OSError:
        return False
    return True


_HAS_PIDFD = _pidfd_works()


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.5)


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _real_create_time(proc: subprocess.Popen[bytes]) -> float:
    psutil = pytest.importorskip("psutil")
    return float(psutil.Process(proc.pid).create_time())


def _fake_info(proc: subprocess.Popen[bytes], root: Path) -> Any:
    created = _real_create_time(proc)
    argv = ["python", "-m", _MODULE, "--root", str(root)]
    return lambda pid: (
        (list(argv), created) if pid == proc.pid else (_ for _ in ()).throw(LookupError(pid))
    )


def _signed_meta(root: Path, pid: int) -> dict[str, Any]:
    """daemon.json as the REAL daemon writes it: with the HMAC attestation (round 3)."""
    psutil = pytest.importorskip("psutil")
    created = float(psutil.Process(pid).create_time())
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    port, version = _free_port(), _expected_tg_version()
    return {
        "version": 1,
        "package_version": version,
        "root": str(root),
        "host": "127.0.0.1",
        "port": port,
        "pid": pid,
        "started_at": "x",
        "token": "t",
        "create_time": created,
        "attestation": trust._attestation_hmac(secret, pid, created, port, str(root), version),
    }


def _plant(root: Path, pid: int) -> None:
    meta = _signed_meta(root, pid)
    meta["port"] = _free_port()  # dead endpoint: the stale-metadata pid path runs
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    meta["attestation"] = trust._attestation_hmac(
        secret, pid, meta["create_time"], meta["port"], str(root), meta["package_version"]
    )
    sd._write_daemon_metadata(root, meta)


# ---- Windows: one OpenProcess handle, opened BEFORE verification, used for termination ----


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_windows_terminates_through_the_handle_opened_before_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    events: list[tuple[str, Any]] = []
    ws = trust._winsec
    real_open = getattr(ws, "open_process", None)
    real_time = getattr(ws, "process_create_time", None)
    real_term = getattr(ws, "terminate_process", None)

    def _open(pid: int) -> Any:
        handle = real_open(pid)
        events.append(("open", handle))
        return handle

    def _time(handle: Any) -> Any:
        events.append(("verify", handle))
        return real_time(handle)

    def _term(handle: Any, *a: Any) -> Any:
        events.append(("terminate", handle))
        return real_term(handle, *a)

    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        if real_open is not None:
            monkeypatch.setattr(ws, "open_process", _open)
            monkeypatch.setattr(ws, "process_create_time", _time)
            monkeypatch.setattr(ws, "terminate_process", _term)
        assert sd._terminate_daemon_by_pid(_signed_meta(root, proc.pid), root=root) is True
        names = [name for name, _h in events]
        assert names.count("open") == 1, f"expected exactly one OpenProcess, got {events}"
        assert names.index("open") < names.index("verify") < names.index("terminate"), events
        opened = events[names.index("open")][1]
        assert [h for n, h in events if n in ("verify", "terminate")] == [opened, opened]
        proc.wait(timeout=10)
        assert trust._last_pid_guard_level() == "handle"
    finally:
        _reap(proc)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles")
def test_windows_create_time_mismatch_on_the_opened_handle_terminates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    ws = trust._winsec
    terminated: list[Any] = []
    closed: list[Any] = []
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        real_time = ws.process_create_time
        real_close = ws.close_handle
        monkeypatch.setattr(ws, "process_create_time", lambda h: (real_time(h) or 0.0) + 10.0)
        monkeypatch.setattr(ws, "terminate_process", lambda h, *a: terminated.append(h) or True)
        monkeypatch.setattr(ws, "close_handle", lambda h: closed.append(h) or real_close(h))
        assert sd._terminate_daemon_by_pid(_signed_meta(root, proc.pid), root=root) is False
        assert terminated == []
        assert proc.poll() is None
        assert closed, "the opened handle was leaked"
    finally:
        _reap(proc)


# ---- Linux: pidfd ----


@pytest.mark.skipif(not _HAS_PIDFD, reason="needs os.pidfd_open / signal.pidfd_send_signal")
def test_pidfd_send_signal_is_used_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    sent: list[int] = []
    real_send = trust._pidfd_send_signal
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        monkeypatch.setattr(
            trust, "_pidfd_send_signal", lambda fd, sig: sent.append(fd) or real_send(fd, sig)
        )
        assert sd._terminate_daemon_by_pid(_signed_meta(root, proc.pid), root=root) is True
        assert len(sent) == 1
        proc.wait(timeout=10)
        assert trust._last_pid_guard_level() == "pidfd"
    finally:
        _reap(proc)


# ---- no bound handle (macOS, old kernel, sandbox without pidfd): FAIL CLOSED, never signal ----
#
# Round 6: the old "recheck" fallback built a NEW psutil.Process(pid) AFTER the identity re-read. If
# the daemon exits and its pid is recycled in that window, terminate() signals the replacement.
# psutil 7.x `Process.terminate()` pre-checks `_raise_if_pid_reused()` and then `os.kill(pid)`: a
# check-then-kill with its own window, so it is not a bound handle either. Signals now go ONLY through
# a kernel handle bound to the verified process (Windows process handle, Linux pidfd); without one
# nothing is signalled and the stop is reported unconfirmed with reason `no_bound_process_handle`.
# The cooperative authenticated shutdown is unaffected on every platform.


def _spy_process_cls_factory(log: list[str]) -> Any:
    """Replacement for the old ``_psutil_process_cls()`` seam: returns a REAL psutil.Process builder
    that records every by-pid construction (the round-6 hazard). The fixed code never calls it."""

    def factory() -> Any:
        psutil = pytest.importorskip("psutil")

        def build(pid: int) -> Any:
            log.append(f"constructed:{pid}")
            return psutil.Process(pid)

        return build

    return factory


def test_pid_recycled_between_the_recheck_and_the_signal_never_signals_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    replacement = _sleeper()  # the process that now owns the recycled pid
    log: list[str] = []
    try:
        meta = _signed_meta(root, replacement.pid)
        monkeypatch.setattr(trust, "_process_info", _fake_info(replacement, root))
        # no kernel handle is available on this platform (simulated everywhere)
        monkeypatch.setattr(trust, "_open_pid_guard", lambda pid: trust._PidGuard(pid))
        monkeypatch.setattr(
            trust, "_psutil_process_cls", _spy_process_cls_factory(log), raising=False
        )
        assert sd._terminate_daemon_by_pid(meta, root=root) is False
        assert replacement.poll() is None, "the recycled pid's new owner was signalled"
        assert not any(entry.startswith("constructed") for entry in log), log
        assert trust._last_pid_guard_level() is None
    finally:
        _reap(replacement)


def test_without_a_bound_handle_the_stop_is_unconfirmed_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        monkeypatch.setattr(trust, "_open_pid_guard", lambda pid: trust._PidGuard(pid))
        _plant(root, proc.pid)  # dead endpoint: the stale-metadata pid path runs
        result = sd.stop_session_daemon(str(root))
        assert proc.poll() is None
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert result["unconfirmed_reason"] == "no_bound_process_handle"
        assert "pid_reuse_guard" not in result
        assert sd._read_daemon_metadata(root) is not None
    finally:
        _reap(proc)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows always has a process handle")
def test_without_pidfd_nothing_is_signalled_on_a_real_platform_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        monkeypatch.setattr(trust, "_pidfd_open", None)
        monkeypatch.setattr(trust, "_pidfd_send_signal", None)
        assert sd._terminate_daemon_by_pid(_signed_meta(root, proc.pid), root=root) is False
        assert proc.poll() is None
    finally:
        _reap(proc)


def test_the_stop_result_records_the_pid_reuse_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform != "win32" and not _HAS_PIDFD:
        pytest.skip("no bound process handle on this platform: covered by the fail-closed tests")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(proc, root))
        _plant(root, proc.pid)  # dead endpoint: the stale-metadata pid path runs
        result = sd.stop_session_daemon(str(root))
        proc.wait(timeout=10)
        assert result["stop_method"] == "pid"
        assert result["pid_reuse_guard"] == ("handle" if sys.platform == "win32" else "pidfd")
    finally:
        _reap(proc)


def test_a_refused_signal_reports_no_guard_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        monkeypatch.setattr(trust, "_process_info", lambda pid: (["python", "-c", "x"], 1.0))
        _plant(root, proc.pid)
        result = sd.stop_session_daemon(str(root))
        assert proc.poll() is None
        assert "pid_reuse_guard" not in result
    finally:
        _reap(proc)


# ---- the guard protocol, platform independent (stub guards record the order) ----


class _StubGuard(trust._PidGuard):
    level = "stub"
    bound = True

    def __init__(self, log: list[str], verify_ok: bool = True, term_ok: bool = True) -> None:
        super().__init__(1)
        self.log, self.verify_ok, self.term_ok = log, verify_ok, term_ok

    def verify(self, created: float) -> bool:
        self.log.append("verify")
        return self.verify_ok

    def terminate(self) -> bool:
        self.log.append("terminate")
        return self.term_ok

    def wait(self, seconds: float) -> None:
        self.log.append("wait")

    def close(self) -> None:
        self.log.append("close")


def test_the_guard_is_opened_first_then_verified_then_used_then_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []

    def _open(pid: int) -> _StubGuard:
        log.append("open")
        return _StubGuard(log)

    def _info(pid: int) -> tuple[list[str], float]:
        log.append("info")
        return ["a"], 5.0

    monkeypatch.setattr(trust, "_open_pid_guard", _open)
    monkeypatch.setattr(trust, "_process_info", _info)
    assert trust._terminate_identified((1, 5.0, ["a"])) is True
    assert log == ["open", "verify", "info", "terminate", "wait", "close"]
    assert trust._last_pid_guard_level() == "stub"


@pytest.mark.parametrize("failure", ["verify", "argv", "create_time", "terminate"])
def test_any_failed_verification_signals_nothing_and_releases_the_guard(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    log: list[str] = []
    guard = _StubGuard(log, verify_ok=failure != "verify", term_ok=failure != "terminate")
    monkeypatch.setattr(trust, "_open_pid_guard", lambda pid: guard)
    info = {"argv": (["other"], 5.0), "create_time": (["a"], 9.0)}.get(failure, (["a"], 5.0))
    monkeypatch.setattr(trust, "_process_info", lambda pid: info)
    assert trust._terminate_identified((1, 5.0, ["a"])) is False
    if failure != "terminate":
        assert "terminate" not in log
    assert log[-1] == "close"
    assert trust._last_pid_guard_level() is None


def test_an_unopenable_process_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trust, "_open_pid_guard", lambda pid: None)
    assert trust._terminate_identified((1, 5.0, ["a"])) is False
