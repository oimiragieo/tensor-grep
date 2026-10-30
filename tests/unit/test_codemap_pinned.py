"""Pinned codemap directory identity and bounded-read controls."""

import os
import sys
from pathlib import Path

import pytest

from tensor_grep.cli.codemap_pinned import PinnedDirectory
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
