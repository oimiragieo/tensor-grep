"""`tg doctor` must never hang on external LSP providers (fail closed as ``unresponsive``).

Receipt: `tests/unit/test_mcp_contract_stamp_ratchet.py` appeared to hang on a dev box with
real language servers installed -- `tg_doctor` probed every provider serially, each with its
own per-phase timeout and NO total deadline, so wall time was ``languages x phases x timeout``.

Hermetic: the "provider" is a fake child that never answers ``shutdown`` and ignores stdin once
its (optional) initialize answer is sent. Every arm runs the subject on a worker thread with a
hard join deadline so a regression is a test FAIL, not a CI hang.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from tensor_grep.cli import doctor_report, lsp_external_provider

_HARD_DEADLINE_SECONDS = 40.0
# The sweep must finish within budget + this fixed margin (cleanup slice + process spawn).
_MARGIN_SECONDS = 2.0

# Optional initialize answer after a delay, then never read stdin again, never answer anything.
_FAKE_SERVER = """\
import json
import os
import subprocess
import sys
import time

gc_pid_file = os.environ.get("GC_PID_FILE")
if gc_pid_file:
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.DEVNULL
    )
    with open(gc_pid_file, "w") as handle:
        handle.write(str(grandchild.pid))

delay = os.environ.get("FAKE_LSP_INIT_DELAY")
if delay is not None:
    stream = sys.stdin.buffer
    length = 0
    while True:
        line = stream.readline()
        if not line:
            sys.exit(0)
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
        if line in (b"\\r\\n", b"\\n"):
            break
    request = json.loads(stream.read(length))
    time.sleep(float(delay))
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request["id"], "result": {"capabilities": {}}}
    ).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body) + body)
    sys.stdout.buffer.flush()
time.sleep(600)
"""


def _fake_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "silent_lsp.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8", newline="\n")
    return [sys.executable, str(script)]


def _run_bounded(fn: Any) -> tuple[Any, float]:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    started = time.monotonic()
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=_HARD_DEADLINE_SECONDS)
    elapsed = time.monotonic() - started
    assert not thread.is_alive(), f"HUNG: still running after {_HARD_DEADLINE_SECONDS}s"
    if "error" in box:
        raise box["error"]
    return box["result"], elapsed


def test_doctor_lsp_sweep_has_total_deadline_and_reports_unresponsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _fake_command(tmp_path)
    languages = ["python", "go", "rust", "java", "typescript", "javascript"]
    budget = 2.0
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: list(languages))
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))

    statuses, elapsed = _run_bounded(
        lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
    )

    # Pre-fix: ~1s initialize + ~1s bounded stop per provider x 6 providers (> 10s).
    assert elapsed <= budget + _MARGIN_SECONDS, f"sweep took {elapsed:.1f}s for a {budget}s budget"
    assert [s["language"] for s in statuses] == languages
    assert all(s["lsp_proof"] is False for s in statuses)
    assert all(s["health_status"] in {"unhealthy", "unresponsive"} for s in statuses)
    assert statuses[-1]["health_status"] == "unresponsive"
    assert statuses[-1]["health_check"] == "deadline_exceeded"
    assert "deadline" in str(statuses[-1]["last_error"])


def test_total_deadline_bounds_slow_initialize_unanswered_symbols_and_ignored_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Init answers late, documentSymbol never, shutdown ignored: per-phase caps alone would
    spend ~2x the probe timeout probing plus an uncapped stop."""
    command = _fake_command(tmp_path)
    budget = 2.0
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("FAKE_LSP_INIT_DELAY", "1.2")
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))

    statuses, elapsed = _run_bounded(
        lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
    )

    assert elapsed <= budget + _MARGIN_SECONDS, f"took {elapsed:.1f}s for a {budget}s budget"
    assert statuses[0]["health_status"] == "unhealthy"
    assert statuses[0]["lsp_proof"] is False


def test_client_stop_terminates_responsive_child_that_ignores_shutdown_and_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _fake_command(tmp_path)
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setenv("FAKE_LSP_INIT_DELAY", "0")
    client = lsp_external_provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=1.0,
        initialize_timeout_seconds=10.0,
    )
    holder: dict[str, subprocess.Popen[Any]] = {}

    def sequence() -> float:
        client.start()  # the fake DOES answer initialize
        process = client.process
        assert process is not None
        holder["process"] = process
        assert process.poll() is None, "child must be alive immediately before the timed stop()"
        started = time.monotonic()
        client.stop()
        elapsed = time.monotonic() - started
        assert process.poll() is not None, "child still alive after stop()"
        assert client.process is None
        return elapsed

    try:
        elapsed, _ = _run_bounded(sequence)
        assert elapsed < 10.0
    finally:
        leftover = holder.get("process")
        if leftover is not None and leftover.poll() is None:
            leftover.kill()


def test_doctor_lsp_total_timeout_env_default_and_valid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", raising=False)
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 90.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "7")
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 7.0


@pytest.mark.parametrize("raw", ["inf", "Infinity", "1e309", "nan", "-5", "0", "abc"])
def test_doctor_lsp_total_timeout_env_rejects_non_finite_and_non_positive(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", raw)
    assert doctor_report._doctor_lsp_total_timeout_seconds() == 90.0


def _pid_gone(pid: int, wait_seconds: float = 3.0) -> bool:
    end = time.monotonic() + wait_seconds
    while time.monotonic() < end:
        if not psutil.pid_exists(pid):
            return True
        time.sleep(0.05)
    return not psutil.pid_exists(pid)


def _kill_leftover(pid_file: Path) -> None:
    if pid_file.exists():
        try:
            psutil.Process(int(pid_file.read_text())).kill()
        except (psutil.Error, ValueError):
            pass


def test_sweep_kills_grandchild_that_holds_the_pipes_and_stays_in_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Killing only the direct child leaves a grandchild holding stdout/stderr, so reader
    threads stay blocked and stream.close() waits on the BufferedReader lock unboundedly."""
    command = _fake_command(tmp_path)
    pid_file = tmp_path / "grandchild.pid"
    budget = 5.0  # generous: python start-up on a loaded box must still reach the fork
    monkeypatch.setenv("GC_PID_FILE", str(pid_file))
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))
    try:
        statuses, elapsed = _run_bounded(
            lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
        )
        assert elapsed <= budget + _MARGIN_SECONDS, f"took {elapsed:.1f}s for a {budget}s budget"
        assert pid_file.exists(), "fake provider never spawned its grandchild; test is vacuous"
        assert _pid_gone(int(pid_file.read_text())), "grandchild survived provider cleanup"
        assert statuses[0]["lsp_proof"] is False
    finally:
        _kill_leftover(pid_file)


def test_grandchild_spawned_at_startup_is_contained_and_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider forks as its FIRST action (before answering initialize): containment must
    already be in force, so the grandchild dies with the tree."""
    command = _fake_command(tmp_path)
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("GC_PID_FILE", str(pid_file))
    monkeypatch.setenv("FAKE_LSP_INIT_DELAY", "0")
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    client = lsp_external_provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=1.0,
        initialize_timeout_seconds=10.0,
    )

    def sequence() -> None:
        client.start()
        assert pid_file.exists()
        grandchild_pid = int(pid_file.read_text())
        assert psutil.pid_exists(grandchild_pid), "grandchild must be alive before stop()"
        started = time.monotonic()
        client.stop()
        assert time.monotonic() - started <= _MARGIN_SECONDS, "stop() blocked on held pipes"
        assert _pid_gone(grandchild_pid), "grandchild survived client.stop()"
        assert client.last_error is None, (
            f"clean teardown must report no error: {client.last_error}"
        )

    try:
        _run_bounded(sequence)
    finally:
        client.stop()
        _kill_leftover(pid_file)


def test_containment_level_is_job_object_or_process_group_not_degraded(
    tmp_path: Path,
) -> None:
    from tensor_grep.cli import process_containment as pc

    process, containment = pc.spawn_contained(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        expected = pc.LEVEL_JOB_OBJECT if sys.platform == "win32" else pc.LEVEL_PROCESS_GROUP
        assert containment.level == expected
        assert containment.degraded is False
        assert process.poll() is None, "contained child must be running after resume"
    finally:
        containment.kill()
        containment.release()
        process.wait(timeout=10)
