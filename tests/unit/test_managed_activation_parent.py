import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import native_frontdoor, windows_parent_process

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native parent identity is Windows-only")


class _WinFunction:
    def __init__(self, call):
        self.call = call
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.call(*args)


class _Kernel:
    def __init__(self, image_path, *, query_result=True, inherited=True):
        self.open_calls = []
        self.inherit_calls = []
        self.GetCurrentProcess = _WinFunction(lambda: 1000)
        self.GetProcessTimes = _WinFunction(
            lambda handle, creation, *_args: (
                setattr(creation._obj, "low", {112: 10, 1000: 20}[handle]) or True
            )
        )
        self.WaitForSingleObject = _WinFunction(lambda _handle, _timeout: 0x00000102)
        self.OpenProcess = _WinFunction(
            lambda access, inherit, pid: self.open_calls.append((access, inherit, pid)) or 112
        )

        def query(_handle, _flags, image, image_size):
            if query_result:
                image.value = image_path
                image_size._obj.value = len(image_path)
            return query_result

        self.QueryFullProcessImageNameW = _WinFunction(query)
        self.SetHandleInformation = _WinFunction(
            lambda handle, mask, flags: (
                self.inherit_calls.append((handle, mask, flags)) or inherited
            )
        )


def _install_kernel(monkeypatch, kernel):
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel)
    monkeypatch.setattr(native_frontdoor.os, "getppid", lambda: 321)
    monkeypatch.setattr(native_frontdoor.sys, "platform", "win32")


def test_OpenNativeParentHandle_WhenParentImageMatches_ReturnsInheritableHandle(
    monkeypatch, tmp_path
):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(stable))
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    assert native_frontdoor._open_native_parent_handle(stable) == 112

    assert kernel.open_calls == [(0x00100000 | 0x1000, False, 321)]
    assert kernel.inherit_calls == [(112, 1, 1)]
    assert closed == []


def test_OpenNativeParentHandle_WhenParentImageDiffers_ClosesHandleAndReturnsNone(
    monkeypatch, tmp_path
):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(tmp_path / "other.exe"))
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    assert native_frontdoor._open_native_parent_handle(stable) is None

    assert closed == [112]
    assert kernel.inherit_calls == []


def test_OpenNativeParentHandle_WhenAdvertisedImageConflicts_ClosesHandleAndRaises(
    monkeypatch, tmp_path
):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(stable))
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(tmp_path / "other.exe"))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError, match="environment does not match"):
        native_frontdoor._open_native_parent_handle(stable)

    assert closed == [112]
    assert kernel.inherit_calls == []


def test_NativeParentMatch_WhenEnvironmentIsAbsent_UsesActualImage(tmp_path):
    stable = tmp_path / "tg.exe"

    assert windows_parent_process._native_parent_matches_stable(stable, str(stable), None)


def test_NativeParentMatch_WhenAdvertisedImageConflicts_Raises(tmp_path):
    stable = tmp_path / "tg.exe"

    with pytest.raises(RuntimeError, match="environment does not match"):
        windows_parent_process._native_parent_matches_stable(
            stable, str(stable), str(tmp_path / "other.exe")
        )


def test_NativeParentMatch_WhenActualImageDiffers_ReturnsFalse(tmp_path):
    stable = tmp_path / "tg.exe"

    assert not windows_parent_process._native_parent_matches_stable(
        stable, str(tmp_path / "other.exe"), None
    )


@pytest.mark.parametrize(("query_result", "inherited"), [(False, True), (True, False)])
def test_OpenNativeParentHandle_WhenQueryOrInheritanceFails_ClosesHandle(
    monkeypatch, tmp_path, query_result, inherited
):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(stable), query_result=query_result, inherited=inherited)
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError):
        native_frontdoor._open_native_parent_handle(stable)

    assert closed == [112]


def test_OpenNativeParentHandle_WhenPathComparisonRaises_ClosesHandle(monkeypatch, tmp_path):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(stable))
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)
    monkeypatch.setattr(
        windows_parent_process,
        "_native_parent_matches_stable",
        lambda *_args: (_ for _ in ()).throw(ValueError("comparison failed")),
    )

    with pytest.raises(ValueError, match="comparison failed"):
        native_frontdoor._open_native_parent_handle(stable)

    assert closed == [112]


def _generation_parent_kernel(stable, generation, closed, *, stable_is_older=True):
    kernel = _Kernel(str(generation))
    handles = {321: 112, 654: 113}
    images = {112: str(generation), 113: str(stable)}

    kernel.OpenProcess = _WinFunction(
        lambda access, inherit, pid: (
            kernel.open_calls.append((access, inherit, pid)) or handles[pid]
        )
    )

    def query(handle, _flags, image, image_size):
        image.value = images[handle]
        image_size._obj.value = len(image.value)
        return True

    kernel.QueryFullProcessImageNameW = _WinFunction(query)
    kernel.SetHandleInformation = _WinFunction(
        lambda handle, mask, flags: kernel.inherit_calls.append((handle, mask, flags)) or True
    )
    kernel.CreateToolhelp32Snapshot = _WinFunction(lambda *_args: 999)

    def first(_snapshot, entry):
        entry._obj.th32ProcessID = 321
        entry._obj.th32ParentProcessID = 654
        return True

    kernel.Process32FirstW = _WinFunction(first)
    kernel.Process32NextW = _WinFunction(lambda *_args: False)
    kernel.CloseHandle = _WinFunction(lambda handle: closed.append(handle) or True)

    def process_times(handle, creation, *_args):
        creation._obj.low = (
            10
            if handle == 113 and stable_is_older
            else (30 if handle == 113 else (40 if handle == 1000 else 20))
        )
        return True

    kernel.GetProcessTimes = _WinFunction(process_times)
    return kernel


def _advertise_generation(monkeypatch, generation, advertise):
    if advertise:
        monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(generation))
    else:
        monkeypatch.delenv("TG_NATIVE_TG_BINARY", raising=False)


@pytest.mark.parametrize("advertise_generation", [True, False])
def test_open_native_parent_handle_when_generation_parent_pins_stable_ancestor(
    monkeypatch, tmp_path, advertise_generation
):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    generation = tmp_path / ".tensor-grep" / "runtimes" / ("a" * 32) / "bin" / "tg.exe"
    closed = []
    kernel = _generation_parent_kernel(stable, generation, closed)
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    _advertise_generation(monkeypatch, generation, advertise_generation)
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    assert native_frontdoor._open_native_parent_handle(stable) == 113

    assert kernel.open_calls == [
        (0x00100000 | 0x1000, False, 321),
        (0x00100000 | 0x1000, False, 654),
    ]
    assert kernel.inherit_calls == [(113, 1, 1)]
    assert sorted(closed) == [112, 999]


def test_open_native_parent_handle_when_stable_pid_was_reused_refuses_activation(
    monkeypatch, tmp_path
):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    generation = tmp_path / ".tensor-grep" / "runtimes" / ("a" * 32) / "bin" / "tg.exe"
    closed = []
    kernel = _generation_parent_kernel(stable, generation, closed, stable_is_older=False)
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    _advertise_generation(monkeypatch, generation, False)
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError, match="PID was reused"):
        native_frontdoor._open_native_parent_handle(stable)

    assert kernel.inherit_calls == []
    assert sorted(closed) == [112, 113, 999]


def test_open_native_parent_handle_when_generation_ancestry_is_missing_fails_closed(
    monkeypatch, tmp_path
):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    generation = tmp_path / ".tensor-grep" / "runtimes" / ("a" * 32) / "bin" / "tg.exe"
    closed = []
    kernel = _generation_parent_kernel(stable, generation, closed)
    kernel.Process32FirstW = _WinFunction(lambda *_args: False)
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    _advertise_generation(monkeypatch, generation, False)
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError, match="absent from the Windows process snapshot"):
        native_frontdoor._open_native_parent_handle(stable)

    assert sorted(closed) == [112, 999]


def test_open_native_parent_handle_when_python_launcher_is_between_tg_and_python(
    monkeypatch, tmp_path
):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    sidecar = tmp_path / ".tensor-grep" / ".venv" / "Scripts" / "python.exe"
    closed = []
    kernel = _Kernel(str(sidecar))
    handles = {321: 112, 654: 113}
    images = {112: str(sidecar), 113: str(stable)}
    kernel.OpenProcess = _WinFunction(
        lambda access, inherit, pid: (
            kernel.open_calls.append((access, inherit, pid)) or handles[pid]
        )
    )

    def query(handle, _flags, image, image_size):
        image.value = images[handle]
        image_size._obj.value = len(image.value)
        return True

    kernel.QueryFullProcessImageNameW = _WinFunction(query)
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setattr(
        windows_parent_process, "_windows_process_parent_pid", lambda _kernel, _pid: 654
    )
    monkeypatch.setattr(
        windows_parent_process,
        "_windows_process_creation_time",
        lambda _kernel, handle: {112: 20, 113: 10, 1000: 30}[handle],
    )
    monkeypatch.setenv("TG_SIDECAR_PYTHON", str(sidecar))
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    assert native_frontdoor._open_native_parent_handle(stable) == 113
    assert kernel.open_calls == [
        (0x00100000 | 0x1000, False, 321),
        (0x00100000 | 0x1000, False, 654),
    ]
    assert kernel.inherit_calls == [(113, 1, 1)]
    assert closed == [112]


def test_open_native_parent_handle_when_python_launcher_precedes_managed_tg(monkeypatch, tmp_path):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    generation = tmp_path / ".tensor-grep" / "runtimes" / ("a" * 32) / "bin" / "tg.exe"
    sidecar = (
        tmp_path / ".tensor-grep" / "runtimes" / ("a" * 32) / ".venv" / "Scripts" / "python.exe"
    )
    closed = []
    kernel = _Kernel(str(sidecar))
    handles = {321: 112, 654: 113, 987: 114}
    images = {112: str(sidecar), 113: str(generation), 114: str(stable)}
    parent_pids = {321: 654, 654: 987}
    kernel.OpenProcess = _WinFunction(
        lambda access, inherit, pid: (
            kernel.open_calls.append((access, inherit, pid)) or handles[pid]
        )
    )

    def query(handle, _flags, image, image_size):
        image.value = images[handle]
        image_size._obj.value = len(image.value)
        return True

    kernel.QueryFullProcessImageNameW = _WinFunction(query)
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setattr(
        windows_parent_process,
        "_windows_process_parent_pid",
        lambda _kernel, pid: parent_pids[pid],
    )
    monkeypatch.setattr(
        windows_parent_process,
        "_windows_process_creation_time",
        lambda _kernel, handle: {112: 30, 113: 20, 114: 10, 1000: 40}[handle],
    )
    monkeypatch.setenv("TG_SIDECAR_PYTHON", str(sidecar))
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(generation))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    assert native_frontdoor._open_native_parent_handle(stable) == 114
    assert kernel.open_calls == [
        (0x00100000 | 0x1000, False, 321),
        (0x00100000 | 0x1000, False, 654),
        (0x00100000 | 0x1000, False, 987),
    ]
    assert kernel.inherit_calls == [(114, 1, 1)]
    assert sorted(closed) == [112, 113]


def test_open_native_parent_handle_when_direct_parent_pid_was_reused_refuses_activation(
    monkeypatch, tmp_path
):
    stable = tmp_path / "tg.exe"
    kernel = _Kernel(str(stable))
    closed = []
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setattr(
        windows_parent_process,
        "_windows_process_creation_time",
        lambda _kernel, handle: {112: 30, 1000: 20}[handle],
    )
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError, match="PID was reused before Python startup"):
        native_frontdoor._open_native_parent_handle(stable)

    assert kernel.inherit_calls == []
    assert closed == [112]


def test_open_native_parent_handle_when_sidecar_exits_during_ancestry_lookup_refuses(
    monkeypatch, tmp_path
):
    stable = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    sidecar = tmp_path / ".tensor-grep" / ".venv" / "Scripts" / "python.exe"
    kernel = _Kernel(str(sidecar))
    closed = []
    wait_results = iter([0x00000102, 0x00000000])
    kernel.WaitForSingleObject = _WinFunction(lambda _handle, _timeout: next(wait_results))
    from tensor_grep.cli import session_daemon_winsec as winsec

    _install_kernel(monkeypatch, kernel)
    monkeypatch.setattr(
        windows_parent_process, "_windows_process_parent_pid", lambda _kernel, _pid: 654
    )
    monkeypatch.setenv("TG_SIDECAR_PYTHON", str(sidecar))
    monkeypatch.setenv("TG_NATIVE_TG_BINARY", str(stable))
    monkeypatch.setattr(winsec, "close_handle", closed.append)

    with pytest.raises(RuntimeError, match="exited during ancestry verification"):
        native_frontdoor._open_native_parent_handle(stable)

    assert kernel.inherit_calls == []
    assert kernel.open_calls == [(0x00100000 | 0x1000, False, 321)]
    assert closed == [112]


@pytest.mark.skipif(os.name != "nt", reason="requires Windows Toolhelp process APIs")
def test_WindowsProcessParentPid_WhenGenerationIsNested_ReturnsOsReportedParent():
    worker = (
        "import ctypes, sys; "
        "from tensor_grep.cli.windows_parent_process import _windows_process_parent_pid; "
        "print(_windows_process_parent_pid(ctypes.WinDLL('kernel32'), int(sys.argv[1])))"
    )
    generation = (
        "import os, subprocess, sys; "
        "print('GEN_PARENT=' + str(os.getppid()), flush=True); "
        "subprocess.run([sys.executable, '-c', sys.argv[1], str(os.getpid())], check=True)"
    )
    stable = (
        "import subprocess, sys; "
        "subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2]], check=True)"
    )

    result = subprocess.run(
        [sys.executable, "-c", stable, generation, worker],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    lines = result.stdout.splitlines()
    assert lines[0].removeprefix("GEN_PARENT=") == lines[1], result.stdout


def test_OpenNativeParentHandle_WhenRunningInChildIdentifiesAndInheritsRealParentHandle(
    tmp_path,
):
    source_root = Path(__file__).resolve().parents[2] / "src"
    child = "\n".join((
        "import ctypes, os, sys",
        "from ctypes import wintypes",
        f"sys.path.insert(0, {str(source_root)!r})",
        "from pathlib import Path",
        "from tensor_grep.cli import native_frontdoor",
        "from tensor_grep.cli import session_daemon_winsec as winsec",
        "handle = native_frontdoor._open_native_parent_handle(Path(sys.argv[1]))",
        "assert handle is not None",
        "kernel = ctypes.WinDLL('kernel32', use_last_error=True)",
        "kernel.GetProcessId.argtypes = [wintypes.HANDLE]",
        "kernel.GetProcessId.restype = wintypes.DWORD",
        "kernel.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]",
        "kernel.GetHandleInformation.restype = wintypes.BOOL",
        "flags = wintypes.DWORD()",
        "assert kernel.GetProcessId(handle) == os.getppid()",
        "assert kernel.GetHandleInformation(handle, ctypes.byref(flags))",
        "assert flags.value & 1",
        "winsec.close_handle(handle)",
    ))
    environment = os.environ.copy()
    environment["TG_NATIVE_TG_BINARY"] = sys.executable

    result = subprocess.run(
        [sys.executable, "-c", child, sys.executable],
        cwd=source_root.parent,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert result.returncode == 0
