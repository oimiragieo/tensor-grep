"""Argv cannot prove that the installed daemon is the code running; an HMAC can (PR #1197 round 3).

A shadow package ``tensor_grep/cli/session_daemon.py`` elsewhere on ``sys.path``, launched with
``python -m tensor_grep.cli.session_daemon --root R``, passes every argv, root and create-time check.
So PID escalation without a live signed ping needs a real proof: at startup the REAL daemon writes into
``daemon.json`` an HMAC over (pid, create_time, port, canonical root, package_version) under the
per-user secret. Unauthenticated escalation is allowed only if that HMAC verifies AND the live
process's create time matches the signed one AND the guard opens on that same process. Metadata
without an attestation (older daemons) or with any field changed is never PID-escalated.

A planted daemon.json or a decoy cannot produce a valid HMAC without the user's secret. An attacker
running as the user's own uid can read that secret: that boundary is a documented residual.

A "ours" process whose termination FAILS is unconfirmed (running True), never "stopped".
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
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
    monkeypatch.setattr(sd, "_DAEMON_START_TIMEOUT_SECONDS", 0.4)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def _reap(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _real_ct(proc: subprocess.Popen[bytes]) -> float:
    psutil = pytest.importorskip("psutil")
    return float(psutil.Process(proc.pid).create_time())


def _signed(root: Path, pid: int, created: float, port: int, **overrides: Any) -> dict[str, Any]:
    """daemon.json as the REAL daemon would have written it (attestation under the user secret)."""
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    pv = _expected_tg_version()
    meta: dict[str, Any] = {
        "version": 1,
        "package_version": pv,
        "root": str(root),
        "host": "127.0.0.1",
        "port": port,
        "pid": pid,
        "started_at": "x",
        "token": "t",
        "create_time": created,
        "attestation": trust._attestation_hmac(secret, pid, created, port, str(root), pv),
    }
    meta.update(overrides)
    return meta


def _info_for(proc: subprocess.Popen[bytes], root: Path, created: float | None = None) -> Any:
    ct = _real_ct(proc) if created is None else created
    argv = [_PY, "-m", _MODULE, "--root", str(root)]

    def _info(pid: int) -> tuple[list[str], float]:
        if pid != proc.pid:
            raise LookupError(pid)
        return list(argv), ct

    return _info


def _plant(root: Path, meta: dict[str, Any]) -> None:
    sd._write_daemon_metadata(root, meta)


# ---- the proof itself ----


def test_a_correctly_attested_live_daemon_is_ours_and_is_signalled_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        meta = _signed(root, proc.pid, _real_ct(proc), _free_port())
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, root))
        assert trust._daemon_pid_state(meta, root) == "ours"
        assert sd._terminate_daemon_by_pid(meta, root=root) is True
        proc.wait(timeout=10)
    finally:
        _reap(proc)


@pytest.mark.parametrize(
    "tamper",
    [
        "create_time",
        "port",
        "package_version",
        "attestation_flipped",
        "attestation_missing",
        "attestation_none",
        "attestation_non_ascii",
        "attestation_wrong_type",
        "create_time_missing",
        "create_time_bool",
        "create_time_string",
        "other_secret",
        "other_root",
    ],
)
def test_any_tampered_field_means_the_pid_is_never_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        created, port = _real_ct(proc), _free_port()
        meta = _signed(root, proc.pid, created, port)
        stop_root = root
        if tamper == "create_time":
            meta["create_time"] = created + 5.0
        elif tamper == "port":
            meta["port"] = port + 1
        elif tamper == "package_version":
            meta["package_version"] = "0.0.1-tampered"
        elif tamper == "attestation_flipped":
            att = meta["attestation"]
            meta["attestation"] = ("0" if att[0] != "0" else "1") + att[1:]
        elif tamper == "attestation_missing":
            del meta["attestation"]
        elif tamper == "attestation_none":
            meta["attestation"] = None
        elif tamper == "attestation_non_ascii":
            meta["attestation"] = "é" * 64  # must not raise TypeError in compare_digest
        elif tamper == "attestation_wrong_type":
            meta["attestation"] = 12345
        elif tamper == "create_time_missing":
            del meta["create_time"]
        elif tamper == "create_time_bool":
            meta["create_time"] = True
        elif tamper == "create_time_string":
            meta["create_time"] = str(created)
        elif tamper == "other_secret":
            meta["attestation"] = trust._attestation_hmac(
                b"x" * 32, proc.pid, created, port, str(root), _expected_tg_version()
            )
        else:  # the metadata is replayed against a different root's stop
            stop_root = (tmp_path / "rootB").resolve()
            stop_root.mkdir()
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, stop_root, created))
        assert trust._daemon_pid_state(meta, stop_root) == "unverifiable"
        assert sd._terminate_daemon_by_pid(meta, root=stop_root) is False
        assert proc.poll() is None
    finally:
        _reap(proc)


def test_old_metadata_without_an_attestation_is_never_pid_escalated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        meta = _signed(root, proc.pid, _real_ct(proc), _free_port())
        del meta["attestation"], meta["create_time"]  # what a pre-attestation daemon wrote
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, root))
        _plant(root, meta)
        result = sd.stop_session_daemon(str(root))
        assert proc.poll() is None
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert result["unconfirmed_reason"] == "pid_unproven"
        assert sd._read_daemon_metadata(root) is not None
    finally:
        _reap(proc)


def test_a_live_create_time_that_differs_from_the_signed_one_is_unproven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        signed_ct = _real_ct(proc) + 100.0  # the daemon the HMAC vouches for started elsewhere
        meta = _signed(root, proc.pid, signed_ct, _free_port())
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, root))
        assert trust._daemon_pid_state(meta, root) == "unverifiable"
        assert sd._terminate_daemon_by_pid(meta, root=root) is False
    finally:
        _reap(proc)


# ---- live processes ----


def test_a_shadow_module_decoy_is_not_killed_and_the_result_says_unproven(
    tmp_path: Path,
) -> None:
    """The round-3 repro: ``python -m tensor_grep.cli.session_daemon --root R`` that resolves to a
    SHADOW package in another directory passes argv, root and create-time checks."""
    pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    shadow = tmp_path / "shadow"
    (shadow / "tensor_grep" / "cli").mkdir(parents=True)
    (shadow / "tensor_grep" / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "tensor_grep" / "cli" / "__init__.py").write_text("", encoding="utf-8")
    (shadow / "tensor_grep" / "cli" / "session_daemon.py").write_text(
        "import time\ntime.sleep(120)\n", encoding="utf-8"
    )
    decoy = subprocess.Popen(
        [sys.executable, "-m", _MODULE, "--root", str(root)],
        cwd=str(shadow),
        env={**os.environ, "PYTHONPATH": str(shadow)},
    )
    try:
        time.sleep(1.0)
        assert decoy.poll() is None, "the shadow decoy did not start"
        # the attacker plants metadata: no valid attestation is possible without the secret
        _plant(
            root,
            {
                "version": 1,
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": "127.0.0.1",
                "port": _free_port(),
                "pid": decoy.pid,
                "started_at": "x",
                "token": "t",
                "create_time": 1.0,
                "attestation": "0" * 64,
            },
        )
        result = sd.stop_session_daemon(str(root))
        assert decoy.poll() is None, "the shadow-module decoy was killed"
        assert result["running"] is True
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert result["unconfirmed_reason"] == "pid_unproven"
        assert sd._read_daemon_metadata(root) is not None
    finally:
        _reap(decoy)


def test_a_real_daemon_writes_a_verifiable_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("psutil")
    root, pid = _start_real_daemon(tmp_path, monkeypatch)
    try:
        meta = sd._read_daemon_metadata(root)
        assert meta is not None
        assert isinstance(meta.get("attestation"), str)
        assert isinstance(meta.get("create_time"), float)
        assert trust._verify_attestation(meta, root) is True
        assert trust._verify_attestation({**meta, "port": int(meta["port"]) + 1}, root) is False
    finally:
        _kill(pid)


def test_a_real_daemon_whose_ping_is_blocked_but_whose_hmac_is_valid_is_escalated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("psutil")
    root, pid = _start_real_daemon(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(
            sd, "_probe_daemon", lambda _root: None
        )  # the signed ping is unavailable
        result = sd.stop_session_daemon(str(root))
        assert result["stop_method"] == "pid"
        assert result["stopped"] is True
        assert result["running"] is False
        assert result["pid_reuse_guard"] in {"handle", "pidfd", "recheck"}
        assert "unconfirmed_reason" not in result
        assert sd._read_daemon_metadata(root) is None
        import psutil

        for _ in range(100):
            if not psutil.pid_exists(pid):
                break
            time.sleep(0.1)
        assert not psutil.pid_exists(pid)
    finally:
        _kill(pid)


def _start_real_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, int]:
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
    return root, int(live["pid"])


def _kill(pid: int) -> None:
    try:
        import psutil

        psutil.Process(pid).kill()
    except Exception:
        pass


# ---- a failed termination of a genuine daemon is UNCONFIRMED ----


class _DeniedGuard(trust._PidGuard):
    level = "denied"

    def terminate(self) -> bool:
        return False  # OpenProcess/TerminateProcess: access denied


def test_failed_termination_of_our_own_daemon_keeps_the_metadata_and_reports_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        created = _real_ct(proc)
        port = _free_port()  # nothing listens: the connection is refused, yet the process is alive
        meta = (
            _signed(root, proc.pid, created, port)
            if hasattr(trust, "_attestation_hmac")
            else {
                "version": 1,
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": "127.0.0.1",
                "port": port,
                "pid": proc.pid,
                "started_at": "x",
                "token": "t",
            }
        )
        _plant(root, meta)
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, root, created))
        monkeypatch.setattr(trust, "_open_pid_guard", lambda pid: _DeniedGuard(pid))
        result = sd.stop_session_daemon(str(root))
        assert proc.poll() is None
        assert result["running"] is True, "a live daemon was reported as not running"
        assert result["stopped"] is False
        assert result["stop_method"] == "none"
        assert result["unconfirmed_reason"] == "termination_failed"
        assert sd._read_daemon_metadata(root) is not None  # daemon.json kept
    finally:
        _reap(proc)


def test_successful_termination_then_refusal_removes_the_stale_metadata_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    proc = _sleeper()
    try:
        created = _real_ct(proc)
        _plant(root, _signed(root, proc.pid, created, _free_port()))
        monkeypatch.setattr(trust, "_process_info", _info_for(proc, root, created))
        result = sd.stop_session_daemon(str(root))
        proc.wait(timeout=10)
        assert result["running"] is False
        assert result["stopped"] is True
        assert result["stop_method"] == "pid"
        assert sd._read_daemon_metadata(root) is None
    finally:
        _reap(proc)


def test_a_vanished_process_still_cleans_up_stale_metadata_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    secret_ok = trust._load_or_create_user_secret() is not None
    assert secret_ok
    _plant(root, _signed(root, 999_999, 1.0, _free_port()))

    def _gone(pid: int) -> tuple[list[str], float]:
        raise LookupError(pid)

    monkeypatch.setattr(trust, "_process_info", _gone)
    result = sd.stop_session_daemon(str(root))
    assert result["running"] is False
    assert sd._read_daemon_metadata(root) is None
