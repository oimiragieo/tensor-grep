from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def isolated_repo_pair(repo_root: Path) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="tg_patch_runner_") as tmp_dir:
        root = Path(tmp_dir)
        before_root = root / "a"
        work_root = root / "b"
        shutil.copytree(repo_root, before_root)
        shutil.copytree(repo_root, work_root)
        yield before_root, work_root


def derive_patch_from_repo_changes(before_root: Path, work_root: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--no-index", "--binary", "--", "a", "b"],
        cwd=before_root.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode not in (0, 1):
        return ""
    patch_text = completed.stdout.strip()
    for relative_path in {
        path.relative_to(before_root).as_posix()
        for path in before_root.rglob("*")
        if path.is_file()
    } | {
        path.relative_to(work_root).as_posix()
        for path in work_root.rglob("*")
        if path.is_file()
    }:
        patch_text = patch_text.replace(
            f"diff --git a/a/{relative_path} b/b/{relative_path}",
            f"diff --git a/{relative_path} b/{relative_path}",
        )
        patch_text = patch_text.replace(f"--- a/a/{relative_path}", f"--- a/{relative_path}")
        patch_text = patch_text.replace(f"+++ b/b/{relative_path}", f"+++ b/{relative_path}")
    return patch_text


def is_probably_patch_text(patch_text: str) -> bool:
    stripped = patch_text.strip()
    if not stripped:
        return False
    return stripped.startswith("diff --git ") or stripped.startswith("--- ")
