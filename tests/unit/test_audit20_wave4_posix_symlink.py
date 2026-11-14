"""POSIX control for repository-walk link confinement."""

import sys
import time
from itertools import islice
from pathlib import Path

import pytest

from tensor_grep.cli import repo_map as rm


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink control")
@pytest.mark.parametrize("walker", ["bucket", "capped"])
def test_repo_walk_preserves_posix_symlink_boundary(tmp_path: Path, walker: str) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    ordinary = root / "ordinary.py"
    marker = outside / "outside_marker.py"
    ordinary.write_text("ordinary = 1\n", encoding="utf-8")
    marker.write_text("outside = 1\n", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"BLOCKED_SETUP: symlink creation refused: {exc}")

    files = (
        list(islice(rm._iter_repo_bucket_files(root), 8))
        if walker == "bucket"
        else rm._iter_repo_files(root, max_files=8, deadline_monotonic=time.monotonic() + 3)
    )

    assert ordinary in files
    assert marker not in files
    assert files == [ordinary]
