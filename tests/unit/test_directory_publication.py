"""Actual no-clobber publication plus intended ABI/error refusal controls."""

from __future__ import annotations

import ctypes
import errno
from pathlib import Path

import pytest

from tensor_grep.io import directory_publication as publication


def test_actual_publication_preserves_existing_empty_directory(tmp_path: Path) -> None:
    source, destination = tmp_path / "stage", tmp_path / "final"
    source.mkdir()
    (source / "asset").write_bytes(b"verified")
    publication.publish_directory_no_replace(source, destination)
    assert not source.exists() and (destination / "asset").read_bytes() == b"verified"
    source.mkdir()
    (source / "asset").write_bytes(b"replacement")
    empty = tmp_path / "racing-empty"
    empty.mkdir()
    identity = empty.stat().st_dev, empty.stat().st_ino
    with pytest.raises(OSError):
        publication.publish_directory_no_replace(source, empty)
    assert (empty.stat().st_dev, empty.stat().st_ino) == identity
    assert not list(empty.iterdir()) and (source / "asset").read_bytes() == b"replacement"


@pytest.mark.parametrize("which", ["source", "destination"])
def test_embedded_nul_refused_before_native_calls(tmp_path: Path, monkeypatch, which: str) -> None:
    calls = []
    monkeypatch.setattr(publication, "_posix_rename", lambda *args: calls.append(args))
    monkeypatch.setattr(publication.os, "rename", lambda *args: calls.append(args))
    source, destination = tmp_path / "stage", tmp_path / "final"
    if which == "source":
        source = Path(str(source) + "\x00ignored")
    else:
        destination = Path(str(destination) + "\x00ignored")
    with pytest.raises(ValueError, match="embedded NUL"):
        publication.publish_directory_no_replace(source, destination)
    assert calls == []


def test_missing_native_primitive_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(publication.sys, "platform", "linux")
    monkeypatch.setattr(publication.ctypes, "CDLL", lambda *args, **kwargs: object())
    with pytest.raises(OSError, match="unavailable: renameat2") as failure:
        publication.publish_directory_no_replace(tmp_path / "stage", tmp_path / "final")
    assert failure.value.errno == errno.ENOTSUP


def test_unsupported_platform_does_not_fallback_to_rename(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(publication.sys, "platform", "unsupported")
    calls = []
    monkeypatch.setattr(publication.os, "rename", lambda *args: calls.append(args))
    with pytest.raises(OSError, match="publication unsupported"):
        publication.publish_directory_no_replace(tmp_path / "stage", tmp_path / "final")
    assert calls == []


@pytest.mark.parametrize("platform,flags", [("linux", 1), ("darwin", 4)])
@pytest.mark.parametrize("code", [errno.EEXIST, errno.ENOSYS, errno.EINVAL, errno.EIO])
def test_native_arguments_and_execution_errors_are_preserved(
    tmp_path: Path, monkeypatch, platform: str, flags: int, code: int
) -> None:
    calls = []

    def refusal(*args):
        calls.append(args)
        ctypes.set_errno(code)
        return -1

    monkeypatch.setattr(publication.sys, "platform", platform)
    monkeypatch.setattr(publication, "_posix_rename", lambda *args: refusal)
    source, destination = tmp_path / "--stage", tmp_path / "--final"
    with pytest.raises(OSError) as failure:
        publication.publish_directory_no_replace(source, destination)
    assert failure.value.errno == code
    paths = (bytes(source), bytes(destination))
    assert calls == [
        (-100, paths[0], -100, paths[1], flags) if platform == "linux" else (*paths, flags)
    ]
