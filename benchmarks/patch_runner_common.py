from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

_IGNORED_DIFF_PARTS = {
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
}
_IGNORED_DIFF_NAMES = {
    "AGENTS.md",
    "candidate.patch",
    ".coverage",
}


@contextlib.contextmanager
def isolated_repo_pair(repo_root: Path) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(
        prefix="tg_patch_runner_", ignore_cleanup_errors=True
    ) as tmp_dir:
        root = Path(tmp_dir)
        before_root = root / "a"
        work_root = root / "b"
        shutil.copytree(repo_root, before_root)
        shutil.copytree(repo_root, work_root)
        yield before_root, work_root


def derive_patch_from_repo_changes(before_root: Path, work_root: Path) -> str:
    root = before_root.parent
    relative_paths = {
        path.relative_to(before_root).as_posix()
        for path in before_root.rglob("*")
        if path.is_file()
    } | {path.relative_to(work_root).as_posix() for path in work_root.rglob("*") if path.is_file()}
    patch_chunks: list[str] = []
    for relative_path in sorted(relative_paths):
        relative = Path(relative_path)
        if relative.name in _IGNORED_DIFF_NAMES:
            continue
        if any(part in _IGNORED_DIFF_PARTS for part in relative.parts):
            continue
        before_path = before_root / relative
        work_path = work_root / relative
        before_bytes = before_path.read_bytes() if before_path.exists() else None
        work_bytes = work_path.read_bytes() if work_path.exists() else None
        if before_bytes == work_bytes:
            continue
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--no-index",
                "--binary",
                "--",
                f"a/{relative_path}",
                f"b/{relative_path}",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode not in (0, 1):
            continue
        patch_text = completed.stdout.strip()
        if not patch_text:
            continue
        patch_text = patch_text.replace(
            f"diff --git a/a/{relative_path} b/b/{relative_path}",
            f"diff --git a/{relative_path} b/{relative_path}",
        )
        patch_text = patch_text.replace(f"--- a/a/{relative_path}", f"--- a/{relative_path}")
        patch_text = patch_text.replace(f"+++ b/b/{relative_path}", f"+++ b/{relative_path}")
        patch_chunks.append(patch_text)
    return "\n".join(patch_chunks).strip()


def is_probably_patch_text(patch_text: str) -> bool:
    stripped = patch_text.strip()
    if not stripped:
        return False
    return stripped.startswith("diff --git ") or stripped.startswith("--- ")


def normalize_model_patch_text(patch_text: str) -> str:
    normalized = patch_text.replace("\r", "")
    if not is_probably_patch_text(normalized):
        return normalized.strip()
    normalized = normalized.rstrip("\n")
    if not normalized.endswith("\n "):
        normalized = normalized + "\n "
    return normalized + "\n"
