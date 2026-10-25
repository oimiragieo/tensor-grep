from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from tensor_grep.cli import session_daemon_winsec as winsec
from tensor_grep.io import windows_publication


@pytest.fixture
def _temporary_payload(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, BinaryIO]:
    path = tmp_path / ".selection.tmp"
    descriptor = windows_publication.open_temporary(path)
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    # The temporary handle keeps WRITE and DELETE access until this stream is closed.
    request.addfinalizer(stream.close)
    assert stream.write(b"new-pair") == 8
    os.fsync(stream.fileno())
    return path, stream


@pytest.fixture
def _held_old_target(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, Any]:
    path = tmp_path / "runtime-selection.json"
    path.write_bytes(b"old-pair")
    handle = winsec.open_no_follow(str(path), share=5)
    assert handle is not None
    request.addfinalizer(lambda: winsec.close_handle(handle))
    return path, handle


class _FailRenameEx:
    def __init__(self, original: Callable[..., None]) -> None:
        self.original = original
        self.classes: list[int] = []

    def __call__(self, descriptor: int, kind: int, buffer: Any, size: int) -> None:
        self.classes.append(kind)
        if kind == 22:
            raise OSError("injected FileRenameInfoEx refusal")
        self.original(descriptor, kind, buffer, size)


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows handle publication")
def test_ReplaceOpen_ExistingTargetWithHeldReader_PublishesNewPairAtomically(
    _held_old_target: tuple[Path, Any], _temporary_payload: tuple[Path, BinaryIO]
) -> None:
    path, held_reader = _held_old_target
    _, stream = _temporary_payload

    windows_publication.replace_open(stream.fileno(), path)
    stream.close()

    assert winsec.read_all(held_reader) == b"old-pair"
    assert path.read_bytes() == b"new-pair"


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows handle publication")
def test_ReplaceOpen_AbsentTarget_PublishesHeldTemporary(
    tmp_path: Path, _temporary_payload: tuple[Path, BinaryIO]
) -> None:
    source, stream = _temporary_payload
    destination = tmp_path / "runtime-selection.json"

    windows_publication.replace_open(stream.fileno(), destination)
    stream.close()

    assert destination.read_bytes() == b"new-pair"
    assert not source.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows handle publication")
def test_RenameOpen_ExistingTargetWithHeldReader_KeepsLegacyRefusal(
    _held_old_target: tuple[Path, Any], _temporary_payload: tuple[Path, BinaryIO]
) -> None:
    path, held_reader = _held_old_target
    source, stream = _temporary_payload

    with pytest.raises(OSError) as refusal:
        windows_publication.rename_open(stream.fileno(), path, replace=True)

    assert refusal.value.winerror == 5
    assert winsec.read_all(held_reader) == b"old-pair"
    assert path.read_bytes() == b"old-pair"
    assert os.fstat(stream.fileno()).st_size == 8
    stream.close()
    assert source.read_bytes() == b"new-pair"


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows handle publication")
def test_ReplaceOpen_UnsupportedRenameClass_PreservesTargetAndSource(
    _held_old_target: tuple[Path, Any],
    _temporary_payload: tuple[Path, BinaryIO],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, held_reader = _held_old_target
    source, stream = _temporary_payload
    failure = _FailRenameEx(windows_publication._set_information)
    monkeypatch.setattr(windows_publication, "_set_information", failure)

    with pytest.raises(OSError, match="injected FileRenameInfoEx refusal"):
        windows_publication.replace_open(stream.fileno(), path)

    assert failure.classes == [22]
    assert winsec.read_all(held_reader) == b"old-pair"
    assert path.read_bytes() == b"old-pair"
    assert os.fstat(stream.fileno()).st_size == 8
    stream.close()
    assert source.read_bytes() == b"new-pair"


def test_ReplaceOpen_NonWindows_RefusesPublication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(windows_publication.sys, "platform", "linux")

    with pytest.raises(OSError, match="Windows handle publication is unavailable"):
        windows_publication.replace_open(1, tmp_path / "runtime-selection.json")
