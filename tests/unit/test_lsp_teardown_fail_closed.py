"""Round-3 audit findings for the LSP provider teardown (PR #1199).

1. no reliable tree containment -> do NOT launch; report ``containment_unavailable``
2. one absolute cleanup deadline; survivors / failed kills land in ``last_error``
3. the tree is terminated BEFORE stdin is closed; a blocking ``stdin.close()`` cannot hang stop
4. a failed resume/kill never leaks the Job handle
5. a Popen double with no handle/pid still gets its direct ``terminate``/``kill`` exactly once

Every arm runs on a worker thread with a hard join, so a regression FAILS instead of hanging.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import doctor_report, lsp_external_provider
from tensor_grep.cli import process_containment as pc

_HARD = 30.0
_MARGIN = 2.0


def _bounded(fn: Any) -> tuple[Any, float]:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:  # surfaced below
            box["error"] = exc

    started = time.monotonic()
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=_HARD)
    elapsed = time.monotonic() - started
    assert not thread.is_alive(), f"HUNG: still running after {_HARD}s"
    if "error" in box:
        raise box["error"]
    return box["result"], elapsed


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str] | None = None):
    monkeypatch.setattr(
        lsp_external_provider,
        "_provider_command",
        lambda language: list(command or [sys.executable, "-c", "pass"]),
    )
    return lsp_external_provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=1.0,
        initialize_timeout_seconds=1.0,
    )


class _FakeProcess:
    """Popen double with NO ``_handle`` and NO ``pid`` but working terminate/kill."""

    def __init__(self, *, stdin: Any = None, wait_times_out_first: bool = True) -> None:
        self.stdin = stdin
        self.stdout = None
        self.stderr = None
        self.events: list[str] = []
        self.terminate_calls = 0
        self.kill_calls = 0
        self._timeout_first = wait_times_out_first
        self._exited = False

    def poll(self) -> int | None:
        return 0 if self._exited else None

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.events.append("terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self.events.append("kill")
        self._exited = True

    def wait(self, timeout: float | None = None) -> int:
        if self._exited:
            return 0
        if self._timeout_first:
            self._timeout_first = False
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)
        self._exited = True
        return 0


# --- finding 1 ---------------------------------------------------------------------------


def test_no_job_object_means_provider_is_never_spawned_and_status_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "spawned.marker"
    script = tmp_path / "marker_lsp.py"
    script.write_text(
        f"open({str(marker)!r}, 'w').write('x')\nimport time\ntime.sleep(60)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = [sys.executable, str(script)]
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", "5")
    if sys.platform == "win32":

        def _boom() -> None:
            raise OSError("simulated CreateJobObjectW failure")

        monkeypatch.setattr(pc, "_create_kill_on_close_job", _boom)
    else:
        monkeypatch.delattr("os.killpg", raising=False)

    statuses, _ = _bounded(lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path)))

    time.sleep(0.5)
    assert not marker.exists(), "provider was launched without reliable containment"
    assert statuses[0]["health_status"] == "containment_unavailable"
    assert statuses[0]["lsp_proof"] is False
    assert "contain" in str(statuses[0]["last_error"]).lower()


# --- finding 2 ---------------------------------------------------------------------------


class _FailingContainment:
    """Containment whose kill fails and whose job still has a live member."""

    level = "job_object"
    degraded = False
    pid = 4242

    def __init__(self) -> None:
        self.kill_calls = 0
        self.terminate_calls = 0

    def terminate(self) -> list[str]:
        self.terminate_calls += 1
        return ["tree terminate failed: AccessDenied"]

    def kill(self) -> list[str]:
        self.kill_calls += 1
        return []

    def survivors(self, deadline: float) -> list[str]:
        return ["pid 4242 (AccessDenied)"]

    def release(self) -> None:
        pass


def test_failed_tree_kill_is_bounded_and_named_in_last_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    process = _FakeProcess()
    containment = _FailingContainment()
    client.process = process  # type: ignore[assignment]
    client._containment = containment  # type: ignore[assignment]
    grace = 0.2

    _, elapsed = _bounded(lambda: client.stop(grace_seconds=grace))

    assert elapsed <= grace + _MARGIN, f"stop took {elapsed:.2f}s for a {grace}s grace"
    assert "4242" in str(client.last_error), client.last_error
    assert "AccessDenied" in str(client.last_error), client.last_error
    assert containment.terminate_calls <= 1


# --- finding 3 ---------------------------------------------------------------------------


class _BlockingStdin:
    def __init__(self, events: list[str], gate: threading.Event) -> None:
        self._events = events
        self._gate = gate

    def close(self) -> None:
        self._events.append("stdin.close")
        self._gate.wait(timeout=20)

    def write(self, data: bytes) -> int:
        return len(data)

    def flush(self) -> None:
        pass


def test_blocking_stdin_close_cannot_hang_stop_and_tree_dies_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    gate = threading.Event()
    process = _FakeProcess()
    process.stdin = _BlockingStdin(process.events, gate)
    client.process = process  # type: ignore[assignment]
    try:
        _, elapsed = _bounded(lambda: client.stop(grace_seconds=0.4))
    finally:
        gate.set()
    assert elapsed <= 0.4 + _MARGIN, f"stop took {elapsed:.2f}s"
    assert process.terminate_calls == 1
    assert process.events.index("terminate") < process.events.index("stdin.close"), process.events


# --- finding 4 ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object resume path is Windows-only")
def test_resume_failure_then_kill_failure_still_closes_the_job_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[object] = []

    class _K32:
        def AssignProcessToJobObject(self, job: object, handle: int) -> int:
            return 1

        def CloseHandle(self, handle: object) -> int:
            closed.append(handle)
            return 1

    class _Nt:
        def NtResumeProcess(self, handle: int) -> int:
            return -1

    class _Popen:
        pid = 31337
        _handle = 99

        def __init__(self, argv: Any, **kwargs: Any) -> None:
            pass

        def kill(self) -> None:
            raise OSError("kill denied")

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 0.0)

    monkeypatch.setattr(pc, "_create_kill_on_close_job", lambda: ("JOB", _K32(), _Nt()))
    monkeypatch.setattr(subprocess, "Popen", _Popen)

    with pytest.raises(OSError) as info:
        pc.spawn_contained(["x"])

    assert "kill denied" not in str(info.value), "the kill failure must not mask the resume failure"
    assert closed == ["JOB"], "Job handle leaked"


# --- finding 5 ---------------------------------------------------------------------------


def test_popen_double_without_handle_or_pid_still_gets_direct_terminate_and_kill_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: fake)
    process, containment = pc.spawn_contained(["fake"])
    assert process is fake
    client = _client(tmp_path, monkeypatch)
    client.process = process  # type: ignore[assignment]
    client._containment = containment

    _bounded(lambda: client.stop(grace_seconds=1.0))

    assert fake.terminate_calls == 1
    assert fake.kill_calls == 1


# --- round-4 finding 1: the final state lock honours the cleanup deadline ---------------


def test_stop_does_not_wait_forever_for_a_held_client_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.process = _FakeProcess()  # type: ignore[assignment]
    held = threading.Event()
    gate = threading.Event()

    def holder() -> None:
        with client._lock:
            held.set()
            gate.wait(timeout=8)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(timeout=5)
    try:
        _, elapsed = _bounded(lambda: client.stop(grace_seconds=0.1))
    finally:
        gate.set()
        thread.join(timeout=10)
    assert elapsed <= 0.1 + _MARGIN, f"stop blocked {elapsed:.2f}s on a held lock"
    assert "teardown lock unavailable" in str(client.last_error), client.last_error
    with pytest.raises(lsp_external_provider.LSPTransportError):
        client.start()  # the client is marked unusable; it must not be reused


# --- round-4 finding 2: a failed survivor query is not "zero survivors" -----------------


def test_failed_job_query_is_an_error_but_a_successful_zero_is_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _K32:
        def CloseHandle(self, handle: object) -> int:
            return 1

    containment = pc.Containment(pc.LEVEL_JOB_OBJECT, degraded=False, pid=1, job="J", k32=_K32())

    monkeypatch.setattr(pc, "_job_active_processes", lambda k32, job: None)
    failed = containment.survivors(time.monotonic() + 0.2)
    assert failed and "could not verify the provider tree exited" in failed[0], failed

    monkeypatch.setattr(pc, "_job_active_processes", lambda k32, job: 0)
    assert containment.survivors(time.monotonic() + 0.2) == []


# --- round-4 finding 3: the report is built AFTER teardown ------------------------------


def test_successful_probe_with_failed_teardown_is_not_reported_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lsp_external_provider, "_provider_command", lambda language: [sys.executable, "-c", "pass"]
    )
    monkeypatch.setattr(
        lsp_external_provider, "_document_symbol_result_contains", lambda r, s: True
    )

    class _Client(lsp_external_provider.ExternalLSPClient):
        def start(self) -> None:
            self.initialized = True
            self.capabilities = {"documentSymbolProvider": True}

        def ensure_document(self, **kwargs: Any) -> None:
            pass

        def request(self, method: str, params: dict[str, Any]) -> Any:
            return []

        def stop(self, grace_seconds: float | None = None) -> None:
            self.teardown_error = "tree kill failed: AccessDenied"
            self.last_error = self.teardown_error

    client = _Client(language="python", workspace_root=tmp_path)
    manager = lsp_external_provider.ExternalLSPProviderManager()
    status = manager._verified_provider_status(
        client=client,
        language="python",
        workspace_root=tmp_path,
        probe_timeout_seconds=1.0,
        stop_after_probe=True,
    )
    assert status["health_status"] != "ready"
    assert status["lsp_proof"] is False
    assert "tree kill failed" in str(status["last_error"])


# --- round-4 finding 4: POSIX process_group escape is DETECTED, not hung on -------------


class _BlockingStream:
    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def close(self) -> None:
        self._gate.wait(timeout=20)


def test_pipes_still_held_after_group_kill_report_the_group_escape() -> None:
    gate = threading.Event()
    process = _FakeProcess(wait_times_out_first=False)
    process.stdout = _BlockingStream(gate)  # type: ignore[assignment]
    containment = pc.Containment(pc.LEVEL_PROCESS_GROUP, degraded=False, pid=0)
    try:
        errors, elapsed = _bounded(
            lambda: pc.teardown_provider(process, containment, deadline=time.monotonic() + 0.3)
        )
    finally:
        gate.set()
    assert elapsed <= 0.3 + _MARGIN
    assert any("outside the provider's process group still holds its pipes" in e for e in errors)


@pytest.mark.skipif(sys.platform == "win32", reason="setsid escape is a POSIX-only scenario")
def test_setsid_grandchild_holding_the_pipes_is_reported_and_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    pid_file = tmp_path / "escaped.pid"
    script = tmp_path / "escape_lsp.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],\n"
        "                      start_new_session=True)\n"
        f"open({str(pid_file)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = [sys.executable, str(script)]
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "1")
    budget = 2.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))
    try:
        statuses, elapsed = _bounded(
            lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
        )
        assert elapsed <= budget + _MARGIN
        assert statuses[0]["health_status"] != "ready"
        assert "outside the provider's process group" in str(statuses[0]["last_error"])
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
