"""Controls for the shared primitive, including refusal after a successful publication."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tensor_grep.io import confined


def test_publication_overwrite_and_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "receipt"
    confined.publish_confined(tmp_path, path, b"first", only_if_missing=True)
    assert confined.read_confined(tmp_path, path, 32) == b"first"
    with pytest.raises(FileExistsError):
        confined.publish_confined(tmp_path, path, b"wrong", only_if_missing=True)
    assert path.read_bytes() == b"first"
    confined.publish_confined(tmp_path, path, b"second")
    assert path.read_bytes() == b"second"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_refuses_changed_original_identity(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "receipt"
    confined.publish_confined(tmp_path, path, b"original")
    identity = confined._file_identity
    observations = 0

    def changed(target: Path) -> tuple[int, int, int, int]:
        nonlocal observations
        actual = identity(target)
        observations += 1
        return actual if observations == 1 else (*actual[:3], actual[3] + 1)

    monkeypatch.setattr(confined, "_file_identity", changed)
    with pytest.raises(OSError, match="changed before publication"):
        confined.publish_confined(tmp_path, path, b"wrong")
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_no_clobber_refuses_a_racing_destination(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "receipt"
    link = os.link

    def raced(source, destination, **kwargs):
        path.write_bytes(b"racing writer")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(confined.os, "link", raced)
    with pytest.raises(FileExistsError):
        confined.publish_confined(tmp_path, path, b"wrong")
    assert path.read_bytes() == b"racing writer"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_parent_swap_cannot_publish_outside(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Probe symlink privilege before arranging the deterministic swap during fsync.
    probe = tmp_path / "probe"
    try:
        probe.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    probe.unlink()
    fsync = os.fsync
    attempted = False

    def swap(descriptor: int) -> None:
        nonlocal attempted
        fsync(descriptor)
        if not attempted:
            attempted = True
            parent.rename(tmp_path / "old-parent")
            parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(confined.os, "fsync", swap)
    with pytest.raises(OSError) as refusal:
        confined.publish_confined(tmp_path, parent / "receipt", b"wrong")
    # Windows denies the rename through held directory handles; POSIX detects the swap.
    assert attempted
    if os.name == "nt":
        assert refusal.value.winerror in (5, 32)
    else:
        assert "parent changed" in str(refusal.value)
    assert not list(outside.iterdir())
    assert not list(tmp_path.rglob(".*.tmp"))


def test_publication_refuses_leaf_and_parent_links(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"untouched")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(OSError, match="linked cache"):
        confined.publish_confined(tmp_path, link, b"wrong")
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="link/junction parent"):
        confined.publish_confined(tmp_path, parent / "receipt", b"wrong")
    assert target.read_bytes() == b"untouched"
    assert not list(outside.iterdir())
