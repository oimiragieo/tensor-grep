"""Round-3 audit findings for the LSP provider teardown (PR #1199).

1. no reliable tree containment -> do NOT launch; report ``containment_unavailable``
2. one absolute cleanup deadline; survivors / failed kills land in ``last_error``
3. the tree is terminated BEFORE stdin is closed; a blocking ``stdin.close()`` cannot hang stop
4. a failed resume/kill never leaks the Job handle
5. a Popen double with no handle/pid still gets its direct ``terminate``/``kill`` exactly once

Every arm runs on a worker thread with a hard join, so a regression FAILS instead of hanging.
"""

from __future__ import annotations

import io
import queue
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


# --- round-5 finding 1: one watchdog bounds blocking transport writes ------------------


def _sleeping_provider(tmp_path: Path) -> list[str]:
    script = tmp_path / "unread_stdin_lsp.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8", newline="\n")
    return [sys.executable, str(script)]


def _assert_sweep_bounded_and_deadline_reported(
    statuses: list[dict[str, Any]], elapsed: float, budget: float
) -> None:
    assert elapsed <= budget + _MARGIN, f"sweep took {elapsed:.2f}s for a {budget}s budget"
    assert statuses[0]["health_status"] != "ready"
    assert statuses[0]["lsp_proof"] is False
    reported = f"{statuses[0]['health_status']} {statuses[0]['last_error']}".lower()
    assert "deadline" in reported or "unresponsive" in reported, reported


def test_oversized_initialize_write_to_an_unread_stdin_is_bounded_by_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _sleeping_provider(tmp_path)
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(
        lsp_external_provider,
        "_configuration_settings",
        lambda language: {"settings": {"blob": "x" * 3_000_000}},
    )
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "1")
    budget = 1.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))

    statuses, elapsed = _bounded(lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path)))

    _assert_sweep_bounded_and_deadline_reported(statuses, elapsed, budget)


@pytest.mark.skipif(sys.platform == "win32", reason="deep non-ASCII workspace repro is POSIX CI")
def test_percent_encoded_workspace_uri_larger_than_the_pipe_buffer_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deep = tmp_path
    for _ in range(20):
        deep = deep / ("é" * 50)
    deep.mkdir(parents=True)
    command = _sleeping_provider(tmp_path)
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "1")
    budget = 1.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))

    statuses, elapsed = _bounded(lambda: doctor_report._doctor_lsp_provider_statuses(str(deep)))

    _assert_sweep_bounded_and_deadline_reported(statuses, elapsed, budget)


# --- round-5 finding 2: graceful waiting cannot starve the forced kill and pipe close ---


class _SleepyWaitProcess(_FakeProcess):
    """First wait() burns its whole timeout then times out; later waits succeed."""

    def wait(self, timeout: float | None = None) -> int:
        if self._exited:
            return 0
        if self._timeout_first:
            self._timeout_first = False
            time.sleep(timeout or 0.0)
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)
        self._exited = True
        return 0


class _SlowCloseStream:
    def __init__(self, delay: float) -> None:
        self._delay = delay

    def close(self) -> None:
        time.sleep(self._delay)


def test_slow_graceful_wait_does_not_starve_pipe_close_into_a_false_escape() -> None:
    process = _SleepyWaitProcess()
    process.stdout = _SlowCloseStream(0.02)  # type: ignore[assignment]
    containment = pc.Containment(pc.LEVEL_PROCESS_GROUP, degraded=False, pid=0)

    errors, _ = _bounded(
        lambda: pc.teardown_provider(process, containment, deadline=time.monotonic() + 0.5)
    )

    assert not any("outside the provider's process group" in e for e in errors), errors
    assert errors == [], errors


def test_unverified_death_with_held_pipes_is_a_timeout_not_an_escape() -> None:
    class _NeverDies(_FakeProcess):
        def wait(self, timeout: float | None = None) -> int:
            time.sleep(timeout or 0.0)
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0.0)

    gate = threading.Event()
    process = _NeverDies()
    process.stdout = _BlockingStream(gate)  # type: ignore[assignment]
    containment = pc.Containment(pc.LEVEL_PROCESS_GROUP, degraded=False, pid=0)
    try:
        errors, _ = _bounded(
            lambda: pc.teardown_provider(process, containment, deadline=time.monotonic() + 0.3)
        )
    finally:
        gate.set()
    assert not any("outside the provider's process group" in e for e in errors), errors
    assert any("cleanup timed out" in e for e in errors), errors


# --- round-5 finding 3: a close() that raises is a cleanup failure ----------------------


class _RaisingClose:
    def close(self) -> None:
        raise OSError("close exploded")


def test_stream_close_error_is_returned_by_teardown() -> None:
    process = _FakeProcess(wait_times_out_first=False)
    process.stdout = _RaisingClose()  # type: ignore[assignment]
    errors, _ = _bounded(
        lambda: pc.teardown_provider(process, None, deadline=time.monotonic() + 0.5)
    )
    assert any("close exploded" in e for e in errors), errors


def test_stream_close_error_reaches_cleanup_error_and_clears_lsp_proof(
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
            fake = _FakeProcess(wait_times_out_first=False)
            fake.stdout = _RaisingClose()  # type: ignore[assignment]
            self.process = fake  # type: ignore[assignment]
            self.initialized = True
            self.capabilities = {"documentSymbolProvider": True}

        def ensure_document(self, **kwargs: Any) -> None:
            pass

        def request(self, method: str, params: dict[str, Any]) -> Any:
            return []

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
    assert "close exploded" in str(status.get("cleanup_error"))


# --- round-5 regression found while testing: re-entrant start() deadlock ---------------


def test_provider_that_dies_during_startup_fails_closed_instead_of_deadlocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """request("initialize") re-enters start(); a child that already died used to make that
    call wait forever on the non-reentrant _start_lock held by its own thread (the probe
    watchdog killing the child just before the handshake exposes it deterministically)."""
    client = _client(tmp_path, monkeypatch, command=[sys.executable, "-c", "pass"])
    real_request = client.request

    def slow_request(method: str, params: dict[str, Any]) -> Any:
        time.sleep(1.0)  # let the instantly-exiting child die before the handshake
        return real_request(method, params)

    monkeypatch.setattr(client, "request", slow_request)
    with pytest.raises(lsp_external_provider.LSPTransportError):
        _bounded(client.start)


# --- round-6 finding 1: a response at/after the deadline is never "ready" ---------------


def test_response_delivered_after_the_watchdog_fired_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lsp_external_provider, "_provider_command", lambda language: [sys.executable, "-c", "pass"]
    )
    monkeypatch.setattr(
        lsp_external_provider, "_document_symbol_result_contains", lambda r, s: True
    )

    class _LateClient(lsp_external_provider.ExternalLSPClient):
        def start(self) -> None:
            self.initialized = True
            self.capabilities = {"documentSymbolProvider": True}

        def ensure_document(self, **kwargs: Any) -> None:
            pass

        def request(self, method: str, params: dict[str, Any]) -> Any:
            time.sleep(0.8)  # the valid answer only arrives AFTER the watchdog fired
            return [{"name": "x"}]

    client = _LateClient(language="python", workspace_root=tmp_path)
    manager = lsp_external_provider.ExternalLSPProviderManager()
    status, _ = _bounded(
        lambda: manager._verified_provider_status(
            client=client,
            language="python",
            workspace_root=tmp_path,
            probe_timeout_seconds=5.0,
            stop_after_probe=True,
            deadline_monotonic=time.monotonic() + 0.5,
        )
    )
    assert status["health_status"] != "ready"
    assert status["lsp_proof"] is False
    assert "deadline" in str(status["last_error"]).lower(), status["last_error"]


# --- round-6 finding 2: an abandoned shutdown worker cannot stop a REPLACEMENT ----------


class _RecordingStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


# --- round-6 finding 3: writes are deadline-bounded independently of process death ------


class _StuckStdin:
    """Stands in for a pipe whose reader was inherited by an escaped descendant."""

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate

    def write(self, data: bytes) -> int:
        self._gate.wait(timeout=20)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_transport_write_is_bounded_even_if_killing_the_process_does_not_unblock_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    gate = threading.Event()
    process = _FakeProcess()
    process.stdin = _StuckStdin(gate)  # type: ignore[assignment]
    process.stdout = io.BytesIO()  # type: ignore[assignment]
    client.process = process  # type: ignore[assignment]
    client.request_timeout_seconds = 0.3

    def attempt() -> str:
        try:
            client.request("textDocument/documentSymbol", {})
        except lsp_external_provider.LSPTransportError as exc:
            return str(exc)
        return "no error"

    try:
        message, elapsed = _bounded(attempt)
        second, second_elapsed = _bounded(attempt)
    finally:
        gate.set()
    assert elapsed <= 0.3 + _MARGIN, f"write blocked {elapsed:.2f}s"
    assert "deadline" in message, message
    assert second_elapsed <= _MARGIN and "deadline" in second, "dead transport must fail fast"


@pytest.mark.skipif(sys.platform == "win32", reason="setsid stdin holder is a POSIX scenario")
def test_escaped_stdin_holder_with_oversized_initialize_is_bounded_and_reports_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import signal

    pid_file = tmp_path / "sleeper.pid"
    script = tmp_path / "stdin_holder_lsp.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "                      start_new_session=True)\n"
        f"open({str(pid_file)!r}, 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
        newline="\n",
    )
    command = [sys.executable, str(script)]
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    monkeypatch.setattr(
        lsp_external_provider,
        "_configuration_settings",
        lambda language: {"settings": {"blob": "x" * 3_000_000}},
    )
    monkeypatch.setattr(doctor_report, "_doctor_lsp_languages", lambda: ["python"])
    monkeypatch.setenv("TG_DOCTOR_LSP_PROBE_TIMEOUT_SECONDS", "2")
    budget = 3.0
    monkeypatch.setenv("TG_DOCTOR_LSP_TOTAL_TIMEOUT_SECONDS", str(budget))
    try:
        statuses, elapsed = _bounded(
            lambda: doctor_report._doctor_lsp_provider_statuses(str(tmp_path))
        )
        assert elapsed <= budget + _MARGIN, f"took {elapsed:.2f}s"
        assert statuses[0]["health_status"] != "ready"
        assert "outside the provider's process group" in str(statuses[0]["last_error"])
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


class _RecordingStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


# --- round-7 finding 2: stale teardown cannot reach a REPLACEMENT session --------------


def _live_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.unit.test_doctor_lsp_deadline import _fake_command

    command = _fake_command(tmp_path)  # answers `initialize`, then sleeps and never reads
    monkeypatch.setenv("FAKE_LSP_INIT_DELAY", "0")
    monkeypatch.setattr(lsp_external_provider, "_provider_command", lambda language: list(command))
    return lsp_external_provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=3.0,
        initialize_timeout_seconds=15.0,
    )


def test_resumed_old_teardown_leaves_the_replacement_sessions_state_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _live_client(tmp_path, monkeypatch)
    client.start()
    session_a = client._session
    gate = threading.Event()
    paused = threading.Event()
    calls: list[Any] = []
    original = lsp_external_provider.reset_after_stop

    def paused_reset(session: Any, sentinel: Any) -> None:
        calls.append(session)
        if len(calls) == 1:
            paused.set()
            gate.wait(timeout=20)  # A's teardown stalls right before its final reset
        original(session, sentinel)

    monkeypatch.setattr(lsp_external_provider, "reset_after_stop", paused_reset)
    stopper = threading.Thread(target=client.stop, daemon=True)
    stopper.start()
    try:
        assert paused.wait(timeout=15), "old teardown never reached its final reset"
        client.start()  # a REAL restart through start(): provider B
        session_b = client._session
        assert session_b is not session_a
        client.capabilities = {"documentSymbolProvider": True}
        slot: queue.Queue[Any] = queue.Queue(maxsize=1)
        client._pending_requests[777] = slot
        writer_b = client._writer
        assert writer_b is not None and client.initialized is True
        gate.set()
        stopper.join(timeout=15)
        assert not stopper.is_alive()

        assert client._session is session_b
        assert client.capabilities == {"documentSymbolProvider": True}
        assert client.initialized is True
        assert client._pending_requests.get(777) is slot and slot.empty(), "B's request resolved"
        assert client._writer is writer_b, "B's writer was closed/replaced by A's teardown"
        assert client.process is not None and client.process.poll() is None
        assert session_a.process is None, "A's own state is cleared"
    finally:
        gate.set()
        client.stop()


def test_resumed_old_shutdown_worker_sends_nothing_to_a_really_restarted_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _live_client(tmp_path, monkeypatch)
    client.start()
    gate = threading.Event()
    workers: list[threading.Thread] = []
    original = client._request_shutdown_for_stop

    def paused(*args: Any, **kwargs: Any) -> None:
        workers.append(threading.current_thread())
        original(*args, **kwargs)
        gate.wait(timeout=20)  # old worker pauses AFTER its shutdown wait, BEFORE `exit`

    monkeypatch.setattr(client, "_request_shutdown_for_stop", paused)
    try:
        client.stop(grace_seconds=0.3)
        client.start()  # real replacement
        sent: list[str] = []
        monkeypatch.setattr(client, "_write_notification", lambda m, p: sent.append(m))
        monkeypatch.setattr(client, "_write_request", lambda i, m, p: sent.append(m))
        gate.set()
        for worker in workers:
            worker.join(timeout=10)
            assert not worker.is_alive(), "old worker must exit cleanly"
        assert sent == [], f"the replacement received stale traffic: {sent}"
    finally:
        gate.set()
        client.stop()


# --- round-7/8: GENERAL lock census (every lock, every method) ----------------------------

# Methods that may take a lock WITHOUT `client_lock`, each with the reason it never matters to a
# probe. Anything not listed (and not an `.acquire(timeout=...)`) must go through `client_lock`.
_LOCK_CENSUS_ALLOWLIST = {
    "get_client": "manager cache dict; in-memory section, no I/O; runs before a probe starts",
    "_cached_client": "manager cache dict; in-memory section, no I/O; runs before a probe starts",
    "_pop_all_clients": "manager cache dict; in-memory section, no I/O; used by stop_all cleanup",
    "wait_until_ready": "navigation readiness wait; never runs inside a doctor probe",
}


def _bare_lock_acquisitions(source: str) -> tuple[list[str], set[str]]:
    """Every `with <x>.<...lock>` and `<x>.<...lock>.acquire(...)` without a `timeout=`,
    reported as `function:line`; plus the set of functions that contain any lock use."""
    import ast

    found: list[str] = []
    users: set[str] = set()

    def is_lock(node: ast.AST) -> bool:
        return isinstance(node, ast.Attribute) and node.attr.endswith("lock")

    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.With):
                for item in node.items:
                    if is_lock(item.context_expr):
                        found.append(f"{fn.name}:{node.lineno}")
                        users.add(fn.name)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "acquire"
                and is_lock(node.func.value)
            ):
                users.add(fn.name)
                if not any(kw.arg == "timeout" for kw in node.keywords):
                    found.append(f"{fn.name}:{node.lineno}:acquire")
    return found, users


def test_every_lock_acquisition_in_the_provider_modules_is_deadline_aware_or_allowlisted() -> None:
    from tensor_grep.cli import lsp_readiness

    seen_users: set[str] = set()
    violations: list[str] = []
    for module in (lsp_external_provider, lsp_readiness):
        source = Path(module.__file__).read_text(encoding="utf-8")
        found, users = _bare_lock_acquisitions(source)
        seen_users |= users
        violations += [v for v in found if v.split(":")[0] not in _LOCK_CENSUS_ALLOWLIST]
    assert violations == [], f"unbounded lock acquisition outside client_lock: {violations}"
    stale = set(_LOCK_CENSUS_ALLOWLIST) - seen_users
    assert not stale, f"allowlist entries without a lock use (remove them): {stale}"


def test_lock_census_detects_bare_with_and_acquire_and_accepts_timeout_acquire() -> None:
    sample = (
        "class C:\n"
        "    def a(self):\n        with self._start_lock:\n            pass\n"
        "    def b(self):\n        self._lock.acquire()\n"
        "    def c(self):\n        self._lock.acquire(timeout=1.0)\n"
    )
    found, users = _bare_lock_acquisitions(sample)
    assert found == ["a:3", "b:6:acquire"] and users == {"a", "b", "c"}


def test_internal_code_never_uses_the_forwarding_attributes() -> None:
    """Only the compatibility layer (SessionBackedState) may spell a forwarded name on `self`;
    every other method must use the session it captured, so a stale reader/teardown can never
    resolve to the replacement session."""
    import ast

    from tensor_grep.cli import lsp_readiness, lsp_session

    session_src = Path(lsp_session.__file__).read_text(encoding="utf-8")
    forwarded = _forwarded_names(session_src)
    assert len(forwarded) >= 10, forwarded
    offenders: list[str] = []
    for module in (lsp_external_provider, lsp_readiness):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for node in ast.walk(fn):
                    if (
                        isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"
                        and node.attr in forwarded
                    ):
                        offenders.append(f"{module.__name__}.{fn.name}:{node.lineno}:{node.attr}")
    assert offenders == [], f"forwarded attribute used internally: {offenders}"
    assert _forwarded_names(
        "class SessionBackedState:\n    process = session_field('process')\n"
    ) == {"process"}


def _forwarded_names(session_source: str) -> set[str]:
    import ast

    names: set[str] = set()
    for node in ast.walk(ast.parse(session_source)):
        if isinstance(node, ast.ClassDef) and node.name == "SessionBackedState":
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", "") == "session_field"
                ):
                    names.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
    return names


# --- round-8 finding 1: a spawned provider is never leaked by a failed publication ---------


class _RecordingContainment:
    level = "job_object"
    degraded = False
    pid = 4343

    def __init__(self) -> None:
        self.terminated = 0
        self.killed = 0
        self.released = 0

    def terminate(self) -> list[str]:
        self.terminated += 1
        return []

    def kill(self) -> list[str]:
        self.killed += 1
        return []

    def survivors(self, deadline: float) -> list[str]:
        return []

    def release(self) -> None:
        self.released += 1


def test_spawned_provider_is_torn_down_when_session_publication_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    fake = _FakeProcess(wait_times_out_first=False)
    containment = _RecordingContainment()
    monkeypatch.setattr(
        lsp_external_provider, "spawn_contained", lambda argv, **kwargs: (fake, containment)
    )
    held = threading.Event()
    gate = threading.Event()

    def holder() -> None:
        with client._lock:
            held.set()
            gate.wait(timeout=10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(timeout=5)
    client.deadline_monotonic = time.monotonic() + 0.3  # the probe's absolute deadline
    try:
        with pytest.raises(TimeoutError):
            _bounded(client.start)
    finally:
        gate.set()
        thread.join(timeout=10)
    assert fake.terminate_calls >= 1, "the spawned provider process leaked"
    assert containment.terminated + containment.killed >= 1, "the process tree was not killed"
    assert containment.released == 1, "the containment (Job handle) was not released"
    assert client._session.process is None, "the failed spawn must not become the session"


# --- round-8 finding 2: a stale reader cannot reach the replacement session ----------------


def test_stale_reader_stages_cannot_touch_a_replacement_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _live_client(tmp_path, monkeypatch)
    client.start()
    session_a = client._session
    client.stop()
    client.start()  # REAL restart into provider B
    session_b = client._session
    assert session_b is not session_a
    try:
        slot: queue.Queue[Any] = queue.Queue(maxsize=1)
        session_b.pending_requests[5] = slot
        orphans_before = dict(session_b.orphan_responses)
        writer_b = session_b.writer
        sent: list[Any] = []
        monkeypatch.setattr(
            lsp_external_provider, "_write_message", lambda stream, payload: sent.append(payload)
        )

        # reader A, paused after its identity check, resumes with a RESPONSE and a SERVER REQUEST
        client._dispatch_response({"jsonrpc": "2.0", "id": 5, "result": "stale"}, session_a)
        client._dispatch_response({"jsonrpc": "2.0", "id": 99, "result": "orphan?"}, session_a)
        handled = client._handle_server_request(
            {"jsonrpc": "2.0", "id": 9, "method": "workspace/configuration", "params": {}},
            session_a,
        )

        assert handled is True
        assert slot.empty(), "stale response resolved B's pending request"
        assert session_b.pending_requests == {5: slot}
        assert session_b.orphan_responses == orphans_before, "B's orphan buffer was written"
        assert session_b.writer is writer_b
        assert sent == [], f"a write reached the replacement provider: {sent}"
    finally:
        client.stop()


def test_probe_ends_within_budget_when_the_client_lock_is_held(
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
            fake = _FakeProcess(wait_times_out_first=False)
            fake.stdin = _RecordingStdin()  # type: ignore[assignment]
            fake.stdout = io.BytesIO()  # type: ignore[assignment]
            self.process = fake  # type: ignore[assignment]
            self.initialized = True

    client = _Client(language="python", workspace_root=tmp_path)
    held = threading.Event()
    gate = threading.Event()

    def holder() -> None:
        with client._lock:
            held.set()
            gate.wait(timeout=10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(timeout=5)
    manager = lsp_external_provider.ExternalLSPProviderManager()
    budget = 0.2
    try:
        status, elapsed = _bounded(
            lambda: manager._verified_provider_status(
                client=client,
                language="python",
                workspace_root=tmp_path,
                probe_timeout_seconds=5.0,
                stop_after_probe=True,
                deadline_monotonic=time.monotonic() + budget,
            )
        )
    finally:
        gate.set()
        thread.join(timeout=10)
    assert elapsed <= budget + _MARGIN, f"probe took {elapsed:.2f}s for a {budget}s budget"
    assert status["health_status"] != "ready"
    assert status["lsp_proof"] is False
    assert "deadline" in str(status["last_error"]).lower(), status["last_error"]


def test_probe_ends_within_budget_when_the_start_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    held = threading.Event()
    gate = threading.Event()

    def holder() -> None:
        with client._start_lock:
            held.set()
            gate.wait(timeout=10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert held.wait(timeout=5)
    manager = lsp_external_provider.ExternalLSPProviderManager()
    budget = 0.3
    try:
        status, elapsed = _bounded(
            lambda: manager._verified_provider_status(
                client=client,
                language="python",
                workspace_root=tmp_path,
                probe_timeout_seconds=5.0,
                stop_after_probe=True,
                deadline_monotonic=time.monotonic() + budget,
            )
        )
    finally:
        gate.set()
        thread.join(timeout=10)
    assert elapsed <= budget + _MARGIN, f"probe took {elapsed:.2f}s for a {budget}s budget"
    assert status["health_status"] != "ready"
    assert "deadline" in str(status["last_error"]).lower(), status["last_error"]
