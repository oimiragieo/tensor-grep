from __future__ import annotations

import os
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec
from tensor_grep.cli.managed_runtime_reader import ManagedRuntimePaths, read_managed_runtime
from tensor_grep.cli.managed_runtime_selection import ManagedRuntimeSelection, encode_selection
from tensor_grep.io import windows_publication

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="managed runtime handles are Windows-only"
)

_FIRST = ManagedRuntimeSelection("0123456789abcdef0123456789abcdef", "1.2.3")
_SECOND = ManagedRuntimeSelection("fedcba9876543210fedcba9876543210", "1.2.4")
_REFUSAL = "managed runtime selection could not be trusted"


def _write_selection(path: Path, selection: ManagedRuntimeSelection) -> None:
    sid = winsec.current_user_sid()
    assert sid is not None
    handle = winsec.create_new_restricted(str(path), sid)
    assert handle is not None
    try:
        assert winsec.write_all(handle, encode_selection(selection))
    finally:
        winsec.close_handle(handle)


def _publish_selection(path: Path, selection: ManagedRuntimeSelection) -> None:
    descriptor = windows_publication.open_temporary(path.with_name(".runtime-selection.next"))
    try:
        payload = encode_selection(selection)
        assert os.write(descriptor, payload) == len(payload)
        os.fsync(descriptor)
        windows_publication.replace_open(descriptor, path)
    finally:
        os.close(descriptor)


def _snapshot(root: Path) -> dict[Path, bytes | None]:
    return {
        path.relative_to(root): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


def _protected_root(trusted_base_dir: Path, tmp_path: Path) -> Path:
    root = trusted_base_dir / f"managed-runtime-reader-{tmp_path.name}"
    assert trust._ensure_secret_dir(root)
    assert trust._parent_trusted(root)
    return root


@pytest.fixture
def _runtime_root(trusted_base_dir: Path, tmp_path: Path) -> Path:
    return _protected_root(trusted_base_dir, tmp_path)


def _assert_first_pair(root: Path, result: ManagedRuntimePaths) -> None:
    assert result.selection == ManagedRuntimeSelection("0123456789abcdef0123456789abcdef", "1.2.3")
    assert result.native.relative_to(root).as_posix() == (
        "runtimes/0123456789abcdef0123456789abcdef/bin/tg.exe"
    )
    assert result.python.relative_to(root).as_posix() == (
        "runtimes/0123456789abcdef0123456789abcdef/.venv/Scripts/python.exe"
    )


def _assert_second_pair(root: Path, result: ManagedRuntimePaths) -> None:
    assert result.selection == ManagedRuntimeSelection("fedcba9876543210fedcba9876543210", "1.2.4")
    assert result.native.relative_to(root).as_posix() == (
        "runtimes/fedcba9876543210fedcba9876543210/bin/tg.exe"
    )
    assert result.python.relative_to(root).as_posix() == (
        "runtimes/fedcba9876543210fedcba9876543210/.venv/Scripts/python.exe"
    )


def _capture_reader_result(
    future: Future[ManagedRuntimePaths | None],
) -> tuple[ManagedRuntimePaths | None, OSError | None]:
    try:
        return future.result(timeout=5), None
    except OSError as error:
        return None, error


def _is_first_pair(root: Path, result: ManagedRuntimePaths | None) -> bool:
    return (
        result is not None
        and result.selection == ManagedRuntimeSelection("0123456789abcdef0123456789abcdef", "1.2.3")
        and result.native.relative_to(root).as_posix()
        == "runtimes/0123456789abcdef0123456789abcdef/bin/tg.exe"
        and result.python.relative_to(root).as_posix()
        == "runtimes/0123456789abcdef0123456789abcdef/.venv/Scripts/python.exe"
    )


def _is_first_pair_or_refusal(
    root: Path, result: ManagedRuntimePaths | None, error: OSError | None
) -> bool:
    return _is_first_pair(root, result) or (
        result is None and type(error) is OSError and str(error) == _REFUSAL
    )


class _FinalPathGate:
    def __init__(self, target: Path, entered: Event, release: Event) -> None:
        self.target = str(target)
        self.entered = entered
        self.release = release
        self.observed_release = False
        self.target_handle: Any = None
        self.real_open: Callable[..., Any] = winsec.open_no_follow
        self.real_final_path: Callable[..., str | None] = winsec.final_path

    def open_no_follow(
        self,
        path: str,
        *,
        directory: bool = False,
        share: int = 1,
        raise_missing: bool = False,
    ) -> Any:
        handle = self.real_open(path, directory=directory, share=share, raise_missing=raise_missing)
        if path == self.target:
            self.target_handle = handle
        return handle

    def final_path(self, handle: Any) -> str | None:
        if handle == self.target_handle:
            self.entered.set()
            self.observed_release = self.release.wait(timeout=5)
            if not self.observed_release:
                raise OSError("bounded test gate expired")
        return self.real_final_path(handle)


def _read_through_gate(
    handle: Any,
    *,
    limit: int,
    real_read: Callable[..., bytes | None],
    entered: Event,
    release: Event,
) -> bytes | None:
    entered.set()
    if not release.wait(timeout=5):
        return None
    return real_read(handle, limit=limit)


def _rename_while_held(root: Path, moved: Path) -> OSError | None:
    try:
        root.rename(moved)
    except OSError as error:
        return error
    moved.rename(root)
    return None


def _rename_round_trip(root: Path, moved: Path) -> bool:
    renamed = root.rename(moved)
    try:
        return renamed == moved
    finally:
        moved.rename(root)


def test_ReadManagedRuntime_ProtectedRootRead_ReturnsExactPairWithoutMutation(
    _runtime_root: Path,
) -> None:
    _write_selection(_runtime_root / "runtime-selection.json", _FIRST)
    before = _snapshot(_runtime_root)

    result = read_managed_runtime(trusted_root=_runtime_root)

    assert result is not None
    _assert_first_pair(_runtime_root, result)
    assert _snapshot(_runtime_root) == before


def test_ReadManagedRuntime_ProtectedRootWithoutSelector_ReturnsNoneWithoutMutation(
    _runtime_root: Path,
) -> None:
    before = _snapshot(_runtime_root)

    result = read_managed_runtime(trusted_root=_runtime_root)

    assert result is None
    assert _snapshot(_runtime_root) == before


def test_ReadManagedRuntime_SelectorReplaceAfterReadGate_ReturnsOldThenNewPair(
    _runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = _runtime_root / "runtime-selection.json"
    _write_selection(selector, _FIRST)
    entered = Event()
    release = Event()
    real_read = winsec.read_all

    with monkeypatch.context() as patch:
        patch.setattr(
            winsec,
            "read_all",
            partial(_read_through_gate, real_read=real_read, entered=entered, release=release),
        )
        with ThreadPoolExecutor(max_workers=1) as workers:
            first_call = workers.submit(read_managed_runtime, trusted_root=_runtime_root)
            assert entered.wait(timeout=5)
            _publish_selection(selector, _SECOND)
            release.set()
            first = first_call.result(timeout=5)

    second = read_managed_runtime(trusted_root=_runtime_root)

    assert first is not None
    assert second is not None
    _assert_first_pair(_runtime_root, first)
    _assert_second_pair(_runtime_root, second)


def test_ReadManagedRuntime_HeldRoot_BlocksRenameUntilClosed(
    _runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_selection(_runtime_root / "runtime-selection.json", _FIRST)
    moved = _runtime_root.with_name(_runtime_root.name + "-held")
    entered = Event()
    release = Event()
    gate = _FinalPathGate(_runtime_root, entered, release)

    with monkeypatch.context() as patch:
        patch.setattr(winsec, "open_no_follow", gate.open_no_follow)
        patch.setattr(winsec, "final_path", gate.final_path)
        with ThreadPoolExecutor(max_workers=1) as workers:
            read_call = workers.submit(read_managed_runtime, trusted_root=_runtime_root)
            assert entered.wait(timeout=5)
            blocked = _rename_while_held(_runtime_root, moved)
            release.set()
            result = read_call.result(timeout=5)

    assert gate.observed_release is True
    assert isinstance(blocked, OSError)
    assert getattr(blocked, "winerror", None) == 32
    assert result is not None
    _assert_first_pair(_runtime_root, result)
    assert _rename_round_trip(_runtime_root, moved) is True
    assert _runtime_root.is_dir()


def test_ReadManagedRuntime_SelectorReplaceBeforeFinalPath_ReturnsOldPairOrRefuses(
    _runtime_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selector = _runtime_root / "runtime-selection.json"
    _write_selection(selector, _FIRST)
    entered = Event()
    release = Event()
    gate = _FinalPathGate(selector, entered, release)

    with monkeypatch.context() as patch:
        patch.setattr(winsec, "open_no_follow", gate.open_no_follow)
        patch.setattr(winsec, "final_path", gate.final_path)
        with ThreadPoolExecutor(max_workers=1) as workers:
            read_call = workers.submit(read_managed_runtime, trusted_root=_runtime_root)
            assert entered.wait(timeout=5)
            _publish_selection(selector, _SECOND)
            release.set()
            result, error = _capture_reader_result(read_call)

    assert gate.observed_release is True
    assert _is_first_pair_or_refusal(_runtime_root, result, error)
