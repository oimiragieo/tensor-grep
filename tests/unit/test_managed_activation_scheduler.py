import ctypes
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import native_frontdoor
from tensor_grep.cli import session_daemon_winsec as winsec

pytestmark = pytest.mark.skipif(os.name != "nt", reason="managed activation is Windows-only")


def _paths(tmp_path: Path) -> tuple[Path, str, Path]:
    generation = "0123456789abcdef0123456789abcdef"
    root = tmp_path / ".tensor-grep"
    python = root / "runtimes" / generation / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture python")
    return root, generation, python


def _pin_fixture(monkeypatch: pytest.MonkeyPatch, *, same_path: bool = True) -> None:
    monkeypatch.setattr(native_frontdoor, "_pin_managed_directory", lambda *_args: "pinned")
    monkeypatch.setattr(
        native_frontdoor, "_pin_managed_generation_executables", lambda *_args: None
    )
    monkeypatch.setattr(
        native_frontdoor,
        "_canonical_windows_path",
        lambda path: "same" if same_path else str(path),
    )


def _parent_handle() -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, os.getpid())
    assert handle
    kernel.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    assert kernel.SetHandleInformation(handle, 1, 1)
    return int(handle)


def test_ActivationScheduler_WhenWorkerSignalsReady_ReturnsStatusPath(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, _python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    parent = _parent_handle()
    calls: list[tuple[list[str], dict[str, object]]] = []
    real_popen = subprocess.Popen

    def popen(args: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        calls.append((args, kwargs))
        return real_popen([sys.executable, *args[1:]], **kwargs)

    monkeypatch.setattr(cli_main.subprocess, "Popen", popen)
    monkeypatch.setattr(
        native_frontdoor,
        "_ACTIVATION_WORKER_PROGRAM",
        "import ctypes,sys; "
        "k=ctypes.WinDLL('kernel32',use_last_error=True); "
        "k.SetEvent.argtypes=[ctypes.c_void_p]; "
        "raise SystemExit(0 if k.SetEvent(int(sys.argv[2])) else 2)",
    )
    event_handles: list[int] = []
    create_event = native_frontdoor._create_activation_ready_event

    def create_event_observed() -> int:
        handle = create_event()
        event_handles.append(handle)
        return handle

    monkeypatch.setattr(native_frontdoor, "_create_activation_ready_event", create_event_observed)

    with ExitStack() as held:
        status = native_frontdoor._schedule_managed_activation(
            Path(sys.executable), root, generation, "1.2.3", parent, held
        )

    assert status == root / "logs" / f"activation-{generation}.log"
    assert len(calls) == 1
    assert calls[0][0][0] == str(_python)
    assert calls[0][1]["close_fds"] is True
    assert calls[0][1]["startupinfo"].lpAttributeList["handle_list"] == [
        parent,
        event_handles[0],
    ]


def test_ActivationScheduler_WhenCandidatePathDoesNotMatch_RefusesBeforeSpawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, _python = _paths(tmp_path)
    _pin_fixture(monkeypatch, same_path=False)
    parent = 123
    monkeypatch.setattr(
        cli_main.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("unexpected spawn")
    )
    closed: list[int] = []
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with ExitStack() as held, pytest.raises(RuntimeError, match="does not match"):
        native_frontdoor._schedule_managed_activation(
            tmp_path / "outside" / "python.exe", root, generation, "1.2.3", parent, held
        )

    assert closed == [parent]


class _WinApiFunction:
    def __init__(self, result: int, error: BaseException | None = None) -> None:
        self.argtypes = None
        self.restype = None
        self.result = result
        self.error = error

    def __call__(self, *_args: object) -> int:
        if self.error is not None:
            raise self.error
        return self.result


class _Kernel:
    def __init__(
        self,
        waited: int,
        set_handle_information: int = 1,
        error: BaseException | None = None,
    ) -> None:
        self.SetHandleInformation = _WinApiFunction(set_handle_information, error)
        self.WaitForMultipleObjects = _WinApiFunction(waited)


class _Child:
    pid = 77

    def __init__(self, wait_error: BaseException | None = None) -> None:
        self.terminated = False
        self.wait_error = wait_error

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        return 0

    def poll(self) -> int | None:
        return 0 if self.terminated and self.wait_error is None else None


def _fake_scheduler_runtime(
    monkeypatch: pytest.MonkeyPatch,
    waited: int,
    *,
    process_handle: int | None = 987,
    settled: bool = True,
    set_handle_information: int = 1,
    error: BaseException | None = None,
    wait_error: BaseException | None = None,
) -> tuple[_Child, list[int]]:
    child = _Child(wait_error)
    closed: list[int] = []

    def terminate(_handle: int) -> bool:
        child.terminate()
        return True

    monkeypatch.setattr(native_frontdoor, "_create_activation_ready_event", lambda: 456)
    monkeypatch.setattr(cli_main.subprocess, "Popen", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(winsec, "open_process", lambda _pid: process_handle)
    monkeypatch.setattr(winsec, "terminate_process", terminate)
    monkeypatch.setattr(winsec, "wait_process", lambda _handle, _timeout: settled)
    monkeypatch.setattr(winsec, "close_handle", closed.append)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: _Kernel(waited, set_handle_information, error),
    )
    return child, closed


def test_ActivationScheduler_WhenWorkerExitsBeforeReady_Raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    _child, _closed = _fake_scheduler_runtime(monkeypatch, 1)

    with ExitStack() as held, pytest.raises(RuntimeError, match="exited before"):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)


def test_ActivationScheduler_WhenReadinessTimesOutAndTerminationIsUnconfirmed_MarksWorkerLive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    _child, _closed = _fake_scheduler_runtime(monkeypatch, 258, settled=False)

    with (
        ExitStack() as held,
        pytest.raises(native_frontdoor._ManagedActivationUnsettledError) as caught,
    ):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert getattr(caught.value.__cause__, native_frontdoor._MANAGED_ACTIVATION_WORKER_MAY_LIVE)


def test_ActivationScheduler_WhenReadinessTimesOutAndChildSettles_RaisesTimeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    child, _closed = _fake_scheduler_runtime(monkeypatch, 258, settled=True)

    with ExitStack() as held, pytest.raises(TimeoutError, match="within 30 seconds"):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert child.terminated


def test_ActivationScheduler_WhenProcessHandleCannotOpen_SettlesWithPopen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    child, _closed = _fake_scheduler_runtime(monkeypatch, 0, process_handle=None)

    with ExitStack() as held, pytest.raises(RuntimeError, match="cannot open activation helper"):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert child.terminated


def test_ActivationScheduler_WhenClearingInheritanceFails_SettlesWorker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    child, _closed = _fake_scheduler_runtime(monkeypatch, 0, set_handle_information=0)

    with ExitStack() as held, pytest.raises(OSError):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert child.terminated


def test_ActivationScheduler_WhenInterruptedAfterSpawn_SettlesWorker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    child, _closed = _fake_scheduler_runtime(monkeypatch, 0, error=KeyboardInterrupt())

    with ExitStack() as held, pytest.raises(KeyboardInterrupt):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert child.terminated


def test_ActivationScheduler_WhenInterruptedAndSettlementFails_MarksOriginalInterrupt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    child, _closed = _fake_scheduler_runtime(
        monkeypatch,
        0,
        error=KeyboardInterrupt(),
        wait_error=OSError("helper wait failed"),
    )

    with ExitStack() as held, pytest.raises(KeyboardInterrupt) as caught:
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)

    assert child.terminated
    assert getattr(caught.value, native_frontdoor._MANAGED_ACTIVATION_WORKER_MAY_LIVE)


def test_ActivationScheduler_WhenWaitFails_PreservesTheReadinessFailure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, generation, python = _paths(tmp_path)
    _pin_fixture(monkeypatch)
    _fake_scheduler_runtime(monkeypatch, 0xFFFFFFFF)

    with ExitStack() as held, pytest.raises(RuntimeError, match="readiness wait failed"):
        native_frontdoor._schedule_managed_activation(python, root, generation, "1.2.3", 123, held)
