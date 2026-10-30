"""Pinned codemap directory identity and bounded-read controls."""

import os
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.codemap_pinned import PinnedDirectory
from tensor_grep.cli.codemap_publication import LOCK_NAME, Publication
from tensor_grep.cli.diff_impact_git import BlobOverCap


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX openat and symlink semantics")
def test_pinned_directory_reads_original_and_detects_root_swap(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    output = tmp_path / "output"
    output.mkdir()
    (output / "page.md").write_text("good", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.md").write_text("bad", encoding="utf-8")
    parked = tmp_path / "parked"

    with PinnedDirectory(output) as pin:
        assert pin.read("page.md", 4) == b"good"
        with pytest.raises(BlobOverCap):
            pin.read("page.md", 3)
        output.rename(parked)
        output.symlink_to(outside, target_is_directory=True)
        assert pin.read("page.md", 4) == b"good"
        assert pin.unchanged() is False
    output.unlink()
    parked.rename(output)
    (output / "page.md").unlink()
    (output / "page.md").symlink_to(outside / "page.md")
    with PinnedDirectory(output) as pin, pytest.raises(OSError):
        pin.read("page.md", 10)
    os.rename(output, parked)


def test_failed_pin_releases_ancestor_handles(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(OSError):
        with PinnedDirectory(directory / "missing"):
            pass
    moved = tmp_path / "moved"
    directory.rename(moved)
    moved.rename(directory)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle sharing semantics")
def test_windows_pin_reads_and_prevents_directory_rename(tmp_path: Path) -> None:
    output = tmp_path.resolve() / "output"
    output.mkdir()
    (output / "page.md").write_bytes(b"good")
    parked = output.with_name("parked")
    with PinnedDirectory(output) as pin:
        assert pin.read("page.md", 4) == b"good"
        with pytest.raises(BlobOverCap):
            pin.read("page.md", 3)
        with pytest.raises(PermissionError):
            output.rename(parked)
    output.rename(parked)


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX directory entries can be renamed while open"
)
def test_pinned_directory_detects_ancestor_swap(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    ancestor = tmp_path / "ancestor"
    output = ancestor / "output"
    output.mkdir(parents=True)
    (output / "page.md").write_text("good", encoding="utf-8")
    parked = tmp_path / "parked"
    with PinnedDirectory(output) as pin:
        assert pin.unchanged() is True
        ancestor.rename(parked)
        assert pin.read("page.md", 4) == b"good"
        assert pin.unchanged() is False
    parked.rename(ancestor)


def test_pinned_lock_descriptor_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / LOCK_NAME
    path.write_bytes(b"existing lock")
    with PinnedDirectory(tmp_path) as pin:
        fd = pin.open_regular(LOCK_NAME)
        try:
            assert pin.file_unchanged(LOCK_NAME, fd)
            with pytest.raises(OSError):
                os.write(fd, b"changed")
        finally:
            os.close(fd)
    assert path.read_bytes() == b"existing lock"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle ownership")
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_failed_windows_handle_transfer_closes_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException]
) -> None:
    import msvcrt

    path = tmp_path / LOCK_NAME
    path.write_bytes(b"")
    failure = error_type("handle transfer failed")

    def fail(*args: object) -> int:
        raise failure

    with PinnedDirectory(tmp_path) as pin:
        monkeypatch.setattr(msvcrt, "open_osfhandle", fail)
        with pytest.raises(error_type) as caught:
            pin.open_regular(LOCK_NAME)
        assert caught.value is failure
    path.rename(tmp_path / "closed")


@pytest.mark.parametrize("index_exists", [False, True])
def test_publication_state_active_lifecycle(tmp_path: Path, index_exists: bool) -> None:
    (tmp_path / LOCK_NAME).write_bytes(b"existing lock")
    index = tmp_path / "index.md"
    if index_exists:
        index.write_bytes(b"index")
    with PinnedDirectory(tmp_path) as pin:
        fd = pin.open_regular(LOCK_NAME)
        try:
            state = Publication(pin, pin, [(pin, fd)], os.getpid(), index)
            assert state.unchanged()
            state._active = False
            assert not state.unchanged(), "inactive publication state must be refused"
        finally:
            os.close(fd)
