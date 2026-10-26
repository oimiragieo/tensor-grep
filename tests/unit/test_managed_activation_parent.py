import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import native_frontdoor

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

    assert native_frontdoor._native_parent_matches_stable(stable, str(stable), None)


def test_NativeParentMatch_WhenAdvertisedImageConflicts_Raises(tmp_path):
    stable = tmp_path / "tg.exe"

    with pytest.raises(RuntimeError, match="environment does not match"):
        native_frontdoor._native_parent_matches_stable(
            stable, str(stable), str(tmp_path / "other.exe")
        )


def test_NativeParentMatch_WhenActualImageDiffers_ReturnsFalse(tmp_path):
    stable = tmp_path / "tg.exe"

    assert not native_frontdoor._native_parent_matches_stable(
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
        native_frontdoor,
        "_native_parent_matches_stable",
        lambda *_args: (_ for _ in ()).throw(ValueError("comparison failed")),
    )

    with pytest.raises(ValueError, match="comparison failed"):
        native_frontdoor._open_native_parent_handle(stable)

    assert closed == [112]


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
