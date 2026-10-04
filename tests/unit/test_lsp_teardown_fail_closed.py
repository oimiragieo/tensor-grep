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
