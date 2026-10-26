from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tensor_grep.cli import managed_runtime_reader as reader
from tensor_grep.cli.managed_runtime_reader import ManagedRuntimePaths
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection
from tests.unit.managed_runtime_win32_fixture import (
    _DRIVE,
    _DRIVE_HANDLE,
    _LEAF_HANDLE,
    _LEAF_PATH,
    _PARENT,
    _PARENT_HANDLE,
    _ROOT_HANDLE,
    _ROOT_PATH,
    _install_reader_boundary,
)

_USER = "S-1-5-21-111-222-333-1001"
_FOREIGN = "S-1-5-21-111-222-333-1999"
_EVERYONE = "S-1-1-0"
_FULL = 0x1F01FF
_BAD_OWNER = (_FOREIGN, [(_FOREIGN, _FULL)])
_BROAD_WRITE = (_USER, [(_USER, _FULL), (_EVERYONE, _FULL)])
_NULL_DACL = (_USER, [("NULL-DACL", _FULL)])
_UNKNOWN_ACE = (_USER, [("UNPARSED-ACE-TYPE-5", _FULL)])
_ROOT = Path("C:\\managed\\tensor-grep")
_DRIVE_CALL = (_DRIVE, True, 3, False)
_PARENT_CALL = (_PARENT, True, 3, False)
_ROOT_CALL = (_ROOT_PATH, True, 3, False)
_LEAF_CALL = (_LEAF_PATH, False, 5, True)
_CALLS_ALL = [_DRIVE_CALL, _PARENT_CALL, _ROOT_CALL, _LEAF_CALL]
_CALLS_THROUGH_PARENT = [_DRIVE_CALL, _PARENT_CALL]
_CALLS_THROUGH_ROOT = [_DRIVE_CALL, _PARENT_CALL, _ROOT_CALL]
_CLOSED_ALL = (_LEAF_HANDLE, _ROOT_HANDLE, _PARENT_HANDLE, _DRIVE_HANDLE)
_CLOSED_ROOT = (_ROOT_HANDLE, _PARENT_HANDLE, _DRIVE_HANDLE)
_CLOSED_PARENT = (_PARENT_HANDLE, _DRIVE_HANDLE)
_CLOSED_DRIVE = (_DRIVE_HANDLE,)
_FIRST = ManagedRuntimeSelection("0123456789abcdef0123456789abcdef", "1.2.3")
_EXPECTED_RESULT = ManagedRuntimePaths(
    _FIRST,
    Path("C:\\managed\\tensor-grep\\runtimes\\0123456789abcdef0123456789abcdef\\bin\\tg.exe"),
    Path(
        "C:\\managed\\tensor-grep\\runtimes\\0123456789abcdef0123456789abcdef"
        "\\.venv\\Scripts\\python.exe"
    ),
)


@pytest.fixture(autouse=True)
def _windows_fake_paths_only(request: pytest.FixtureRequest) -> None:
    if request.node.name != "test_ReadManagedRuntime_NonWindowsPlatform_RaisesFixedOSError":
        if sys.platform != "win32":
            pytest.skip("fake reader paths use native Windows Path semantics")


def test_ReadManagedRuntime_ValidProtectedChain_ReturnsLiteralPairAndClosesReverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)

    result = reader.read_managed_runtime(trusted_root=_ROOT)

    assert result == _EXPECTED_RESULT
    assert boundary.open_calls == _CALLS_ALL
    assert boundary.read_calls == [(_LEAF_HANDLE, 512)]
    assert tuple(boundary.closed) == _CLOSED_ALL


def test_ReadManagedRuntime_MissingSelectorUnderValidRoot_ReturnsNoneAndClosesChain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.missing_leaf = True

    result = reader.read_managed_runtime(trusted_root=_ROOT)

    assert result is None
    assert boundary.open_calls == _CALLS_ALL
    assert boundary.read_calls == []
    assert tuple(boundary.closed) == (_ROOT_HANDLE, _PARENT_HANDLE, _DRIVE_HANDLE)


@pytest.mark.parametrize(
    ("path", "calls", "closed"),
    [
        pytest.param(
            _PARENT, [_DRIVE_CALL, _PARENT_CALL], _CLOSED_DRIVE, id="ancestor-open-denied"
        ),
        pytest.param(_ROOT_PATH, _CALLS_THROUGH_ROOT, _CLOSED_PARENT, id="root-open-denied"),
        pytest.param(_LEAF_PATH, _CALLS_ALL, _CLOSED_ROOT, id="leaf-open-denied"),
    ],
)
def test_ReadManagedRuntime_OpenRefusal_ClosesPriorHandles(
    path: str,
    calls: list[tuple[str, bool, int, bool]],
    closed: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.refused_paths.add(path)

    with pytest.raises(OSError) as error:
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert path not in str(error.value)
    assert boundary.open_calls == calls
    assert boundary.read_calls == []
    assert tuple(boundary.closed) == closed


@pytest.mark.parametrize(
    "invalid_root",
    [
        pytest.param(Path("relative-root"), id="relative"),
        pytest.param(Path(r"\\server\share\managed"), id="unc"),
        pytest.param(Path(r"\\?\C:\managed"), id="device"),
        pytest.param(Path(r"C:\base\..\managed"), id="parent-component"),
        pytest.param(Path("C:\\"), id="drive-root-is-not-managed-root"),
    ],
)
def test_ReadManagedRuntime_InvalidRoot_RefusesBeforeOpening(
    invalid_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _install_reader_boundary(monkeypatch)

    with pytest.raises(OSError) as error:
        reader.read_managed_runtime(trusted_root=invalid_root)

    assert str(invalid_root) not in str(error.value)
    assert boundary.open_calls == []


def test_ReadManagedRuntime_OverdeepRoot_RefusesBeforeOpening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    deep_root = Path("C:\\" + "\\".join(["component"] * 65))

    with pytest.raises(OSError):
        reader.read_managed_runtime(trusted_root=deep_root)

    assert boundary.open_calls == []


def test_ReadManagedRuntime_NonWindowsPlatform_RaisesFixedOSError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader.sys, "platform", "linux")

    with pytest.raises(OSError) as error:
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert str(error.value) == "managed runtime selection is supported only on Windows"


@pytest.mark.parametrize(
    ("handle", "final_path", "calls", "closed"),
    [
        pytest.param(_DRIVE_HANDLE, None, [_DRIVE_CALL], _CLOSED_DRIVE, id="missing-drive-final"),
        pytest.param(
            _DRIVE_HANDLE,
            r"\\?\UNC\server\share",
            [_DRIVE_CALL],
            _CLOSED_DRIVE,
            id="unc-drive-final",
        ),
        pytest.param(
            _DRIVE_HANDLE,
            "\\\\?\\D:\\",
            [_DRIVE_CALL],
            _CLOSED_DRIVE,
            id="different-drive-final",
        ),
        pytest.param(
            _PARENT_HANDLE,
            None,
            _CALLS_THROUGH_PARENT,
            _CLOSED_PARENT,
            id="missing-ancestor-final",
        ),
        pytest.param(
            _PARENT_HANDLE,
            r"\\?\C:\elsewhere\managed",
            _CALLS_THROUGH_PARENT,
            _CLOSED_PARENT,
            id="wrong-ancestor-final",
        ),
        pytest.param(
            _ROOT_HANDLE,
            None,
            _CALLS_THROUGH_ROOT,
            _CLOSED_ROOT,
            id="missing-root-final",
        ),
        pytest.param(
            _ROOT_HANDLE,
            r"\\?\C:\managed\sibling",
            _CALLS_THROUGH_ROOT,
            _CLOSED_ROOT,
            id="wrong-root-final",
        ),
        pytest.param(
            _LEAF_HANDLE,
            r"\\?\C:\managed\tensor-grep\other.json",
            _CALLS_ALL,
            _CLOSED_ALL,
            id="wrong-leaf-name",
        ),
        pytest.param(_LEAF_HANDLE, None, _CALLS_ALL, _CLOSED_ALL, id="missing-leaf-final"),
        pytest.param(
            _LEAF_HANDLE,
            r"\\?\C:\sibling\runtime-selection.json",
            _CALLS_ALL,
            _CLOSED_ALL,
            id="sibling-leaf-final",
        ),
    ],
)
def test_ReadManagedRuntime_FinalPathRefusal_ClosesHandles(
    handle: str,
    final_path: str | None,
    calls: list[tuple[str, bool, int, bool]],
    closed: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.final_paths[handle] = final_path

    with pytest.raises(OSError):
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert boundary.open_calls == calls
    assert boundary.read_calls == []
    assert tuple(boundary.closed) == closed


@pytest.mark.parametrize(
    ("handle", "security", "calls", "closed"),
    [
        pytest.param(
            _PARENT_HANDLE, _BAD_OWNER, _CALLS_THROUGH_PARENT, _CLOSED_PARENT, id="ancestor-owner"
        ),
        pytest.param(
            _PARENT_HANDLE,
            _BROAD_WRITE,
            _CALLS_THROUGH_PARENT,
            _CLOSED_PARENT,
            id="ancestor-write-grant",
        ),
        pytest.param(
            _PARENT_HANDLE, None, _CALLS_THROUGH_PARENT, _CLOSED_PARENT, id="ancestor-query"
        ),
        pytest.param(_ROOT_HANDLE, _BAD_OWNER, _CALLS_THROUGH_ROOT, _CLOSED_ROOT, id="root-owner"),
        pytest.param(
            _ROOT_HANDLE,
            _BROAD_WRITE,
            _CALLS_THROUGH_ROOT,
            _CLOSED_ROOT,
            id="root-write",
        ),
        pytest.param(_ROOT_HANDLE, None, _CALLS_THROUGH_ROOT, _CLOSED_ROOT, id="root-query"),
        pytest.param(_LEAF_HANDLE, _BAD_OWNER, _CALLS_ALL, _CLOSED_ALL, id="leaf-owner"),
        pytest.param(_LEAF_HANDLE, _BROAD_WRITE, _CALLS_ALL, _CLOSED_ALL, id="leaf-write-grant"),
        pytest.param(_LEAF_HANDLE, None, _CALLS_ALL, _CLOSED_ALL, id="leaf-query"),
        pytest.param(_LEAF_HANDLE, _NULL_DACL, _CALLS_ALL, _CLOSED_ALL, id="leaf-null-dacl"),
        pytest.param(_LEAF_HANDLE, _UNKNOWN_ACE, _CALLS_ALL, _CLOSED_ALL, id="leaf-unknown-ace"),
    ],
)
def test_ReadManagedRuntime_TrustQueryOrPolicyFailure_RefusesAndCloses(
    handle: str,
    security: tuple[str, list[tuple[str, int]]] | None,
    calls: list[tuple[str, bool, int, bool]],
    closed: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.security[handle] = security

    with pytest.raises(OSError):
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert boundary.open_calls == calls
    assert boundary.read_calls == []
    assert tuple(boundary.closed) == closed


def test_ReadManagedRuntime_HardLinkedLeaf_RefusesAndClosesChain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.links = 2

    with pytest.raises(OSError):
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert tuple(boundary.closed) == _CLOSED_ALL


@pytest.mark.parametrize(
    ("final_failures", "entry_failures", "closed", "sensitive"),
    [
        pytest.param((_PARENT_HANDLE,), (), _CLOSED_PARENT, "private path detail", id="final-path"),
        pytest.param(
            (), (_PARENT_HANDLE,), _CLOSED_PARENT, "private security detail", id="security"
        ),
    ],
)
def test_ReadManagedRuntime_ApiException_IsSanitizedAndCloses(
    final_failures: tuple[str, ...],
    entry_failures: tuple[str, ...],
    closed: tuple[str, ...],
    sensitive: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.raise_final_for = final_failures
    boundary.raise_entries_for = entry_failures

    with pytest.raises(OSError) as error:
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert sensitive not in str(error.value)
    assert boundary.open_calls == _CALLS_THROUGH_PARENT
    assert boundary.read_calls == []
    assert tuple(boundary.closed) == closed


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param(b"\xff", "selection record must be valid UTF-8", id="invalid-utf8"),
        pytest.param(b" " * 513, "selection record exceeds 512 bytes", id="oversize"),
        pytest.param(
            b'{"schema_version":1,"generation":"0123456789abcdef0123456789abcdef",'
            b'"generation":"0123456789abcdef0123456789abcdef","version":"1.2.3"}',
            "selection record has duplicate keys",
            id="duplicate-key",
        ),
    ],
)
def test_ReadManagedRuntime_InvalidRecord_PropagatesDecoderValueErrorAndCloses(
    payload: bytes, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.payload = payload

    with pytest.raises(ValueError) as error:
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert str(error.value) == message
    assert boundary.read_calls == [(_LEAF_HANDLE, 512)]
    assert tuple(boundary.closed) == _CLOSED_ALL


def test_ReadManagedRuntime_ReadApiFailure_RefusesAndClosesChain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = _install_reader_boundary(monkeypatch)
    boundary.payload = None

    with pytest.raises(OSError):
        reader.read_managed_runtime(trusted_root=_ROOT)

    assert boundary.read_calls == [(_LEAF_HANDLE, 512)]
    assert tuple(boundary.closed) == _CLOSED_ALL
