from __future__ import annotations

import ctypes
import sys
from collections.abc import Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

import pytest

from tensor_grep.cli import session_daemon_winsec as winsec

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows handle APIs")

_EXACT512 = b"x" * 512
_EXACT8192 = b"x" * 8192


class _FakeFunction:
    def __init__(self, name: str, result: Any, events: list[str]) -> None:
        self.name = name
        self.result = result
        self.events = events
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        self.events.append(self.name)
        self.calls.append(args)
        return self.result


class _FakeKernel32:
    def __init__(self, create_result: Any, info_result: int) -> None:
        self.events: list[str] = []
        self.CreateFileW = _FakeFunction("CreateFileW", create_result, self.events)
        self.GetFileInformationByHandle = _FakeFunction(
            "GetFileInformationByHandle", info_result, self.events
        )
        self.CloseHandle = _FakeFunction("CloseHandle", 1, self.events)


class _FakeWinDLL:
    def __init__(self, kernel32: _FakeKernel32) -> None:
        self.kernel32 = kernel32
        self.calls: list[str] = []

    def __call__(self, name: str, **_kwargs: object) -> _FakeKernel32:
        self.calls.append(name)
        return self.kernel32


class _FakeLastError:
    def __init__(self, code: int, events: list[str]) -> None:
        self.code = code
        self.events = events

    def __call__(self) -> int:
        self.events.append("GetLastError")
        return self.code


def _install_fake_win32(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_result: Any,
    info_result: int = 0,
    error_code: int = 2,
) -> tuple[_FakeKernel32, _FakeWinDLL, _FakeLastError]:
    kernel32 = _FakeKernel32(create_result, info_result)
    loader = _FakeWinDLL(kernel32)
    last_error = _FakeLastError(error_code, kernel32.events)
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", last_error, raising=False)
    return kernel32, loader, last_error


def _open_recorded(path: Path, handles: list[Any]) -> Any | None:
    handle = winsec.open_no_follow(str(path))
    if handle is not None:
        handles.append(handle)
    return handle


@pytest.fixture
def _opened_handles() -> Iterator[list[Any]]:
    handles: list[Any] = []
    yield handles
    for handle in handles:
        winsec.close_handle(handle)


class _ReadFileRecorder:
    def __init__(self, function: Any, requested: list[int], returned: list[int]) -> None:
        self.function = function
        self.requested = requested
        self.returned = returned

    @property
    def argtypes(self) -> Any:
        return self.function.argtypes

    @argtypes.setter
    def argtypes(self, value: Any) -> None:
        self.function.argtypes = value

    @property
    def restype(self) -> Any:
        return self.function.restype

    @restype.setter
    def restype(self, value: Any) -> None:
        self.function.restype = value

    def __call__(self, *args: Any) -> Any:
        self.requested.append(int(args[2]))
        result = self.function(*args)
        count = ctypes.cast(args[3], ctypes.POINTER(wintypes.DWORD)).contents.value
        self.returned.append(int(count))
        return result


class _RecordingKernel32:
    def __init__(self, kernel32: Any, requested: list[int], returned: list[int]) -> None:
        self.kernel32 = kernel32
        self.ReadFile = _ReadFileRecorder(kernel32.ReadFile, requested, returned)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.kernel32, name)


class _RecordingWinDLL:
    def __init__(self, original: Any, requested: list[int], returned: list[int]) -> None:
        self.original = original
        self.requested = requested
        self.returned = returned

    def __call__(self, name: str, **kwargs: Any) -> Any:
        kernel32 = self.original(name, **kwargs)
        if name == "kernel32":
            return _RecordingKernel32(kernel32, self.requested, self.returned)
        return kernel32


def _record_readfile_calls(
    monkeypatch: pytest.MonkeyPatch, requested: list[int], returned: list[int]
) -> None:
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        _RecordingWinDLL(ctypes.WinDLL, requested, returned),
    )


def test_OpenNoFollow_DefaultMissingFile_RemainsRefused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32, _loader, _last_error = _install_fake_win32(
        monkeypatch, create_result=invalid_handle, error_code=2
    )

    result = winsec.open_no_follow(r"C:\fixture\missing.json")

    assert result is None
    assert kernel32.CreateFileW.calls[0][2] == 1


def test_OpenNoFollow_OptedMissingFile_RaisesFileNotFoundError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32, _loader, _last_error = _install_fake_win32(
        monkeypatch, create_result=invalid_handle, error_code=2
    )

    with pytest.raises(FileNotFoundError):
        winsec.open_no_follow(r"C:\fixture\missing.json", raise_missing=True)

    assert kernel32.events == ["CreateFileW", "GetLastError"]


@pytest.mark.parametrize("error_code", [3, 5, 32], ids=["missing-parent", "access", "sharing"])
def test_OpenNoFollow_OptedNonFileNotFoundCreateError_RemainsRefused(
    error_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid_handle = ctypes.c_void_p(-1).value
    kernel32, _loader, _last_error = _install_fake_win32(
        monkeypatch, create_result=invalid_handle, error_code=error_code
    )

    result = winsec.open_no_follow(r"C:\fixture\path.json", raise_missing=True)

    assert result is None
    assert kernel32.events == ["CreateFileW", "GetLastError"]


def test_OpenNoFollow_QueryFailure_IsNotClassifiedAsMissing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32, _loader, _last_error = _install_fake_win32(
        monkeypatch, create_result=1234, info_result=0, error_code=2
    )

    result = winsec.open_no_follow(r"C:\fixture\unqueryable.json", raise_missing=True)

    assert result is None
    assert kernel32.events == ["CreateFileW", "GetFileInformationByHandle", "CloseHandle"]


@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(True, id="boolean"),
        pytest.param(cast(int, 1.5), id="float"),
        pytest.param(8193, id="over-default"),
    ],
)
def test_ReadAll_InvalidLimit_RefusesBeforeWinApi(
    limit: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel32, loader, _last_error = _install_fake_win32(
        monkeypatch, create_result=1234, info_result=1
    )

    with pytest.raises(ValueError):
        winsec.read_all(object(), limit=cast(int, limit))

    assert loader.calls == []
    assert kernel32.events == []


def test_ReadAll_DefaultLimit_Accepts8192ByteFile(
    tmp_path: Path, _opened_handles: list[Any]
) -> None:
    payload = _EXACT8192
    path = tmp_path / "boundary.bin"
    path.write_bytes(payload)
    handle = _open_recorded(path, _opened_handles)

    assert handle is not None
    assert winsec.read_all(handle) == payload


def test_ReadAll_DefaultLimit_Rejects8193ByteFile(
    tmp_path: Path, _opened_handles: list[Any]
) -> None:
    path = tmp_path / "oversized.bin"
    path.write_bytes(b"x" * 8193)
    handle = _open_recorded(path, _opened_handles)

    assert handle is not None
    assert winsec.read_all(handle) is None


def test_ReadAll_Limit512_AcceptsExactBoundaryAndBoundsReads(
    tmp_path: Path, _opened_handles: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _EXACT512
    path = tmp_path / "boundary.bin"
    path.write_bytes(payload)
    handle = _open_recorded(path, _opened_handles)
    assert handle is not None
    requested: list[int] = []
    returned: list[int] = []

    with monkeypatch.context() as patch:
        _record_readfile_calls(patch, requested, returned)
        result = winsec.read_all(handle, limit=512)

    assert result == payload
    assert requested == [513, 1]
    assert returned == [512, 0]
    assert max(requested) <= 513
    assert sum(returned) <= 513


def test_ReadAll_Limit512_Rejects513BytesAfterBoundedProbe(
    tmp_path: Path, _opened_handles: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "oversized.bin"
    path.write_bytes(b"x" * 513)
    handle = _open_recorded(path, _opened_handles)
    assert handle is not None
    requested: list[int] = []
    returned: list[int] = []

    with monkeypatch.context() as patch:
        _record_readfile_calls(patch, requested, returned)
        result = winsec.read_all(handle, limit=512)

    assert result is None
    assert requested == [513]
    assert returned == [513]
    assert max(requested) <= 513
    assert sum(returned) <= 513
