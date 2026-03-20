from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_CHECKPOINT_VERSION = 1
_CHECKPOINT_DIRNAME = ".tensor-grep"
_CHECKPOINTS_SUBDIR = "checkpoints"
_INDEX_FILE = "index.json"
_SNAPSHOT_SUBDIR = "snapshot"
_METADATA_FILE = "metadata.json"
_NON_GIT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tensor-grep",
}


@dataclass
class CheckpointRecord:
    version: int
    checkpoint_id: str
    mode: str
    root: str
    created_at: str
    file_count: int


@dataclass
class CheckpointCreateResult:
    checkpoint_id: str
    mode: str
    root: str
    created_at: str
    file_count: int


@dataclass
class CheckpointUndoResult:
    checkpoint_id: str
    mode: str
    root: str
    restored_files: int
    removed_paths: int


def _detect_checkpoint_root(path: Path) -> tuple[Path, str]:
    resolved = path.expanduser().resolve()
    probe_root = resolved if resolved.is_dir() else resolved.parent
    try:
        completed = subprocess.run(
            ["git", "-C", str(probe_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return resolved if resolved.is_dir() else resolved.parent, "filesystem-snapshot"

    git_root = Path(completed.stdout.strip())
    return git_root, "git-worktree-snapshot"


def _checkpoint_storage_dir(root: Path) -> Path:
    return root / _CHECKPOINT_DIRNAME / _CHECKPOINTS_SUBDIR


def _index_path(root: Path) -> Path:
    return _checkpoint_storage_dir(root) / _INDEX_FILE


def _load_index(root: Path) -> list[CheckpointRecord]:
    index_path = _index_path(root)
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [CheckpointRecord(**entry) for entry in payload]


def _write_index(root: Path, records: list[CheckpointRecord]) -> None:
    index_path = _index_path(root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps([asdict(record) for record in records], indent=2),
        encoding="utf-8",
    )


def _git_snapshot_entries(root: Path) -> dict[str, bool]:
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=False,
        check=True,
    ).stdout.split(b"\x00")
    untracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=False,
        check=True,
    ).stdout.split(b"\x00")

    entries: dict[str, bool] = {}
    for raw in [*tracked, *untracked]:
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape")
        entries[rel] = (root / rel).exists()
    return dict(sorted(entries.items()))


def _filesystem_snapshot_entries(root: Path) -> dict[str, bool]:
    entries: dict[str, bool] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            if path.name in _NON_GIT_IGNORED_DIRS:
                continue
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _NON_GIT_IGNORED_DIRS for part in relative.parts):
            continue
        entries[relative.as_posix()] = True
    return entries


def _snapshot_entries(root: Path, mode: str) -> dict[str, bool]:
    if mode == "git-worktree-snapshot":
        return _git_snapshot_entries(root)
    return _filesystem_snapshot_entries(root)


def _checkpoint_dir(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_storage_dir(root) / checkpoint_id


def _snapshot_path(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_dir(root, checkpoint_id) / _SNAPSHOT_SUBDIR


def _metadata_path(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_dir(root, checkpoint_id) / _METADATA_FILE


def _write_checkpoint_metadata(root: Path, result: CheckpointCreateResult, entries: dict[str, bool]) -> None:
    payload: dict[str, Any] = {
        "version": _CHECKPOINT_VERSION,
        "checkpoint_id": result.checkpoint_id,
        "mode": result.mode,
        "root": result.root,
        "created_at": result.created_at,
        "file_count": result.file_count,
        "entries": entries,
    }
    _metadata_path(root, result.checkpoint_id).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def create_checkpoint(path: str = ".") -> CheckpointCreateResult:
    root, mode = _detect_checkpoint_root(Path(path))
    created_at = datetime.now(UTC).isoformat()
    checkpoint_id = f"ckpt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    entries = _snapshot_entries(root, mode)

    snapshot_dir = _snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, exists in entries.items():
        if not exists:
            continue
        source = root / rel_path
        destination = snapshot_dir / rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    result = CheckpointCreateResult(
        checkpoint_id=checkpoint_id,
        mode=mode,
        root=str(root),
        created_at=created_at,
        file_count=len(entries),
    )
    _write_checkpoint_metadata(root, result, entries)

    records = _load_index(root)
    records.insert(
        0,
        CheckpointRecord(
            version=_CHECKPOINT_VERSION,
            checkpoint_id=checkpoint_id,
            mode=mode,
            root=str(root),
            created_at=created_at,
            file_count=len(entries),
        ),
    )
    _write_index(root, records)
    return result


def list_checkpoints(path: str = ".") -> list[CheckpointRecord]:
    root, _mode = _detect_checkpoint_root(Path(path))
    return _load_index(root)


def undo_checkpoint(checkpoint_id: str, path: str = ".") -> CheckpointUndoResult:
    root, mode = _detect_checkpoint_root(Path(path))
    metadata_path = _metadata_path(root, checkpoint_id)
    if not metadata_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    entries: dict[str, bool] = metadata["entries"]
    snapshot_dir = _snapshot_path(root, checkpoint_id)

    current_entries = _filesystem_snapshot_entries(root)
    expected_paths = set(entries.keys())
    removed_paths = 0

    for rel_path in sorted(set(current_entries) - expected_paths, reverse=True):
        current_path = root / Path(rel_path)
        if current_path.exists():
            current_path.unlink()
            removed_paths += 1

    restored_files = 0
    for rel_path, exists in entries.items():
        target = root / Path(rel_path)
        if exists:
            source = snapshot_dir / Path(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored_files += 1
        elif target.exists():
            target.unlink()
            removed_paths += 1

    for directory in sorted(root.rglob("*"), reverse=True):
        if not directory.is_dir():
            continue
        if directory == _checkpoint_storage_dir(root).parent:
            continue
        try:
            relative = directory.relative_to(root)
        except ValueError:
            continue
        if any(part in {".git", _CHECKPOINT_DIRNAME} for part in relative.parts):
            continue
        if not any(directory.iterdir()):
            directory.rmdir()

    return CheckpointUndoResult(
        checkpoint_id=checkpoint_id,
        mode=mode,
        root=str(root),
        restored_files=restored_files,
        removed_paths=removed_paths,
    )
