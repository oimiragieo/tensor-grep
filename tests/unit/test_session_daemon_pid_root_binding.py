"""A kill by recorded pid must only ever hit THE tensor-grep daemon that serves THIS root.

Round 1 bound the kill to ``--root <R>``; round 2 found that "the module name appears somewhere in
argv" still accepted ``python -c "<sleep>" tensor_grep.cli.session_daemon --root R`` (an unrelated
process that never ran the daemon), and that a recorded pid says nothing about pid REUSE. So:

* the invocation is validated structurally: ``<python> [interpreter options] -m
  tensor_grep.cli.session_daemon <args>`` where ``-m`` is the module selector (``-c``, a script path,
  ``--`` or the module appearing only as an argument to something else are refused) and the daemon
  arguments are bound by the SAME argparse grammar the daemon uses;
* the process identity (create_time) captured at classification must still match when the signal
  is sent (PID-reuse guard).
If psutil is missing or the argv/identity cannot be read, nothing is signalled and the stop is
reported as unconfirmed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_MODULE = "tensor_grep.cli.session_daemon"
_PY = "python"


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


def _sleeper(*argv_tail: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *argv_tail])


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _real_ct(proc: subprocess.Popen[bytes]) -> float:
    """The sleeper's REAL create time: the Windows process handle verifies it, so a fabricated
    value would (correctly) be refused as PID reuse."""
    psutil = pytest.importorskip("psutil")
    return float(psutil.Process(proc.pid).create_time())


def _fake_info(
    proc: subprocess.Popen[bytes], argv: list[str], create_time: float | None = None
) -> Callable[[int], tuple[list[str], float]]:
    """Seam double: report ``argv`` for the real, harmless sleeper process ``proc``."""
    if create_time is None:
        create_time = _real_ct(proc)

    def _info(pid: int) -> tuple[list[str], float]:
        if pid != proc.pid:
            raise LookupError(pid)
        return list(argv), create_time

    return _info


# ---- the invocation grammar (pure) ----


@pytest.mark.parametrize(
    "argv",
    [
        [_PY, "-m", _MODULE, "--root", "R"],
        ["C:\\Python312\\python.exe", "-m", _MODULE, "--root", "R"],
        ["/usr/bin/python3.12", "-m", _MODULE, "--root", "R"],
        ["PYTHONW.EXE", "-m", _MODULE, "--root", "R"],
        [_PY, "-B", "-u", "-m", _MODULE, "--root", "R"],
        [_PY, "-X", "utf8", "-W", "ignore", "-m", _MODULE, "--root=R"],
        [_PY, "-Wignore", "-Xutf8", "-m", _MODULE, "--root", "R"],
        [_PY, "-Bm", _MODULE, "--root", "R"],
        [_PY, "-m" + _MODULE, "--root", "R"],
        [_PY, "-I", "-m", _MODULE, "--ro", "R"],  # argparse prefix matching binds --root
        [_PY, "-m", _MODULE, "--root", "first", "--root", "R"],  # argparse: the last one wins
    ],
)
def test_a_genuine_daemon_invocation_yields_its_root(argv: list[str]) -> None:
    assert trust._daemon_invocation_root(argv) == "R"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        [_PY],
        [_PY, "-c", "import time; time.sleep(9)", _MODULE, "--root", "R"],
        [_PY, "-c", "x", "-m", _MODULE, "--root", "R"],
        [_PY, "-ccode", _MODULE, "--root", "R"],
        [_PY, "script.py", _MODULE, "--root", "R"],
        [_PY, "script.py", "-m", _MODULE, "--root", "R"],
        [_PY, "-m", "other_module", _MODULE, "--root", "R"],
        [_PY, "-m", "other_module", "-m", _MODULE, "--root", "R"],
        [_PY, "--", "-m", _MODULE, "--root", "R"],
        [_PY, "-", _MODULE, "--root", "R"],
        [_PY, "--version", "-m", _MODULE, "--root", "R"],
        [_PY, "-m", _MODULE],  # no --root: the daemon itself would exit with an argparse error
        [_PY, "-m", _MODULE, "--root"],
        [_PY, "-m", _MODULE, "--root", "R", "--bogus"],
        [_PY, "-m", _MODULE, "--", "--root", "R"],
        [_PY, "-m", _MODULE, "extra", "--root", "R"],
        ["node", "-m", _MODULE, "--root", "R"],
        ["not-python", _PY, "-m", _MODULE, "--root", "R"],
        [_PY + "x", "-m", _MODULE, "--root", "R"],
        [_PY, "-m", _MODULE + ".extra", "--root", "R"],
        [_PY, "-m", "x" + _MODULE, "--root", "R"],
    ],
)
def test_anything_else_is_not_a_daemon_invocation(argv: list[str]) -> None:
    assert trust._daemon_invocation_root(argv) is None


# ---- live processes, real psutil (skipped where psutil is not installed) ----


def test_live_python_c_sleeper_carrying_the_module_name_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    sleeper = _sleeper(_MODULE, "--root", str(root))  # the exact round-2 repro
    try:
        _plant(root, sleeper.pid)
        result = sd.stop_session_daemon(str(root))
        assert sleeper.poll() is None, "an unrelated python -c process was killed"
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert sd._read_daemon_metadata(root) is not None
    finally:
        _reap(sleeper)


def test_live_script_with_a_fake_daemon_argv_is_refused(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    script = tmp_path / "fake_daemon.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    fake = subprocess.Popen([sys.executable, str(script), _MODULE, "--root", str(root)])
    try:
        _plant(root, fake.pid)
        result = sd.stop_session_daemon(str(root))
        assert fake.poll() is None
        assert result["running"] is True and result["stopped"] is False
    finally:
        _reap(fake)


def test_live_decoy_serving_a_different_root_is_not_signalled(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    other = (tmp_path / "rootB").resolve()
    other.mkdir()
    victim = _sleeper(_MODULE, "--root", str(other))
    try:
        _plant(root, victim.pid)
        result = sd.stop_session_daemon(str(root))
        assert victim.poll() is None
        assert result["running"] is True and result["stopped"] is False
    finally:
        _reap(victim)


def test_a_real_daemon_serving_this_root_is_signalled_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONTROL: a REAL daemon, launched exactly like ``_spawn_daemon_subprocess``, IS terminated.

    The recorded endpoint is replaced by a dead port so the cooperative path is unavailable and
    the pid-kill fallback is what must work (and must only work for the genuine daemon).
    """
    pytest.importorskip("psutil")
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 20.0)
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    sd._spawn_daemon_subprocess(root)
    live = None
    deadline = time.time() + 60
    while time.time() < deadline and live is None:
        live = sd._probe_daemon(root)
        time.sleep(0.2)
    assert live is not None, "the real daemon never became reachable"
    daemon_pid = int(live["pid"])
    try:
        # the signed ping is unavailable, but the daemon's own metadata HMAC verifies
        monkeypatch.setattr(sd, "_probe_daemon", lambda _root: None)
        assert trust._daemon_pid_state(sd._read_daemon_metadata(root), root) == "ours"
        result = sd.stop_session_daemon(str(root))
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert result["pid_reuse_guard"] in {"handle", "pidfd", "recheck"}
        if sys.platform == "win32":
            assert result["pid_reuse_guard"] == "handle"
        import psutil

        for _ in range(100):
            if not psutil.pid_exists(daemon_pid):
                break
            time.sleep(0.1)
        assert not psutil.pid_exists(daemon_pid)
    finally:
        try:
            import psutil

            psutil.Process(daemon_pid).kill()
        except Exception:
            pass


# ---- the same contract through the process-info seam (real signals, fabricated argv) ----


def test_seam_different_root_refused_and_same_root_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    other = (tmp_path / "rootB").resolve()
    other.mkdir()
    victim = _sleeper()
    mine = _sleeper()
    try:
        infos = {
            victim.pid: ([_PY, "-m", _MODULE, "--root", str(other)], _real_ct(victim)),
            mine.pid: ([_PY, "-m", _MODULE, "--root", str(root)], _real_ct(mine)),
        }
        monkeypatch.setattr(trust, "_process_info", lambda pid: infos[pid])
        assert sd._terminate_daemon_by_pid(_signed_meta(root, victim.pid), root=root) is False
        assert victim.poll() is None
        assert sd._terminate_daemon_by_pid(_signed_meta(root, mine.pid), root=root) is True
        mine.wait(timeout=10)
    finally:
        _reap(victim)
        _reap(mine)


@pytest.mark.parametrize("form", ["equals", "case"])
def test_root_spellings_that_resolve_to_the_same_root_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    if form == "equals":
        tail = ["--root=" + str(root)]
    else:
        if sys.platform != "win32":
            pytest.skip("case-insensitive roots are a Windows property")
        tail = ["--root", str(root).swapcase()]
    mine = _sleeper()
    try:
        monkeypatch.setattr(trust, "_process_info", _fake_info(mine, [_PY, "-m", _MODULE, *tail]))
        assert sd._terminate_daemon_by_pid(_signed_meta(root, mine.pid), root=root) is True
        mine.wait(timeout=10)
    finally:
        _reap(mine)


def test_a_relative_root_is_refused_because_its_cwd_is_unknowable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The daemon is always launched with an ABSOLUTE root; a relative one would be resolved against
    # a cwd we cannot see, so it is never treated as ours even when it happens to resolve here.
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    victim = _sleeper()
    try:
        argv = [_PY, "-m", _MODULE, "--root", os.path.join(".", "rootA")]
        monkeypatch.setattr(trust, "_process_info", _fake_info(victim, argv))
        assert sd._terminate_daemon_by_pid(_signed_meta(root, victim.pid), root=root) is False
        assert victim.poll() is None
    finally:
        _reap(victim)


def test_pid_reuse_is_refused_when_the_create_time_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Classified as the daemon at its REAL signed create time; by the time the signal is sent the pid
    # belongs to a process created later (the original exited and the OS recycled the pid). The first
    # read must pass classification (signed real create time), so the guard opens and the
    # termination-time identity re-read is what refuses -- not an early classification mismatch.
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    bystander = _sleeper()
    try:
        real = _real_ct(bystander)
        argv = [_PY, "-m", _MODULE, "--root", str(root)]
        reads: list[float] = []

        def _info(pid: int) -> tuple[list[str], float]:
            reads.append(real)
            return list(argv), real if len(reads) == 1 else real + 500.0

        opened: list[Any] = []
        real_open = trust._open_pid_guard

        def _spy_open(pid: int) -> Any:
            guard = real_open(pid)
            opened.append(guard)
            return guard

        meta = _signed_meta(root, bystander.pid)
        monkeypatch.setattr(trust, "_process_info", _info)
        monkeypatch.setattr(trust, "_open_pid_guard", _spy_open)
        assert trust._daemon_pid_state(meta, root) == "ours"  # classification itself passes
        reads.clear()
        # now drive the real escalation: first read (classification) is genuine, the second differs
        assert sd._terminate_daemon_by_pid(meta, root=root) is False
        assert len(reads) >= 2, "the termination-time identity re-read never happened"
        assert opened and opened[0] is not None, "the guard never opened"
        assert trust._last_pid_guard_level() is None  # no signal was delivered
        assert bystander.poll() is None
    finally:
        _reap(bystander)


def test_an_unchanged_create_time_is_signalled_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    mine = _sleeper()
    try:
        monkeypatch.setattr(
            trust,
            "_process_info",
            _fake_info(mine, [_PY, "-m", _MODULE, "--root", str(root)]),
        )
        assert sd._terminate_daemon_by_pid(_signed_meta(root, mine.pid), root=root) is True
        mine.wait(timeout=10)
    finally:
        _reap(mine)


def test_a_root_argument_is_required_to_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    victim = _sleeper()
    try:
        monkeypatch.setattr(
            trust, "_process_info", _fake_info(victim, [_PY, "-m", _MODULE, "--root", str(root)])
        )
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
    victim = _sleeper()  # stands in for OUR daemon that we cannot prove is ours
    try:

        def _denied(_pid: int) -> tuple[list[str], float]:
            raise error

        monkeypatch.setattr(trust, "_process_info", _denied)
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

    def _gone(pid: int) -> tuple[list[str], float]:
        raise LookupError(pid)

    monkeypatch.setattr(trust, "_process_info", _gone)
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
