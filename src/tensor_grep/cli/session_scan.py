from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli.repo_map import build_repo_map


def build_session_repo_map(
    path: str | Path,
    *,
    max_repo_files: int | None,
    deadline_monotonic: float | None,
) -> dict[str, Any]:
    return build_repo_map(
        path,
        max_repo_files=max_repo_files,
        deadline_monotonic=deadline_monotonic,
        exclude_codemap_dirs=True,
    )
