from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tensor_grep.cli.subprocess_policy import configured_git_timeout_seconds, run_subprocess

_CHECKPOINT_VERSION = 1
_CHECKPOINT_DIRNAME = ".tensor-grep"
_CHECKPOINTS_SUBDIR = "checkpoints"
_INDEX_FILE = "index.json"
_SNAPSHOT_SUBDIR = "snapshot"
_METADATA_FILE = "metadata.json"
_DISCOVERY_CACHE_FILE = "checkpoint-discovery-cache.json"
_DISCOVERY_CACHE_VERSION = 2
_DISCOVERY_MAX_DEPTH = 6
_DISCOVERY_MAX_DIRECTORIES = 10_000
_DISCOVERY_CACHE_TTL_SECONDS = 300.0
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
    "artifacts",
    "build",
    "dist",
    "site",
    "target",
}
_DISCOVERY_IGNORED_DIRS = _NON_GIT_IGNORED_DIRS - {"artifacts"}


def _is_generated_discovery_dir(path: Path) -> bool:
    return path.name in _DISCOVERY_IGNORED_DIRS or path.name.startswith(".tmp")


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2))
        handle.flush()
        # fsync the data before the rename so a crash can never publish a truncated
        # index/metadata that would leave the agent's rollback safety net unable to
        # find the checkpoint while edits stay applied (audit I5).
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
    # Best-effort durability of the rename itself; directory fsync is a no-op or
    # unsupported on Windows, so failures here are non-fatal.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _resolve_within_root(root: Path, root_resolved: Path, rel_path: str) -> Path:
    """Resolve a checkpoint metadata entry key under ``root`` and assert containment.

    Checkpoint metadata is read from disk and the undo path is reachable from the MCP
    ``tg_checkpoint_undo`` tool, the CLI, and policy rollback, so the entry keys are
    attacker-influenceable. Refuse absolute paths, ``..`` traversal, and symlink
    escapes before any unlink/copy, so a tampered manifest can never write to or delete
    a file outside the checkpoint root (audit S1).
    """
    candidate = Path(rel_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"Refusing checkpoint entry outside root: {rel_path!r}")
    resolved = (root / candidate).resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"Refusing checkpoint entry outside root: {rel_path!r}")
    return resolved


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
    undo_argv: list[str]
    undo_command: str


@dataclass
class CheckpointScopeResult:
    root: str
    mode: str
    checkpoint_count: int
    checkpoints: list[CheckpointRecord]


@dataclass
class CheckpointDiscoveryResult:
    scopes: list[CheckpointScopeResult]
    truncated: bool = False


@dataclass
class CheckpointUndoResult:
    checkpoint_id: str
    mode: str
    root: str
    restored_files: int
    removed_paths: int


@dataclass
class CheckpointLatestResult:
    checkpoint_id: str
    root: str
    mode: str


@dataclass(frozen=True)
class _CheckpointScope:
    root: Path
    mode: str
    original_path: Path
    target_relative: Path | None = None

    @property
    def scope_kind(self) -> str:
        return "file" if self.target_relative is not None else "tree"


def _detect_checkpoint_scope(path: Path) -> _CheckpointScope:
    resolved = path.expanduser().resolve()
    if resolved.is_file() or (not resolved.exists() and resolved.suffix):
        return _CheckpointScope(
            root=resolved.parent,
            mode="filesystem-snapshot",
            original_path=resolved,
            target_relative=Path(resolved.name),
        )

    probe_root = resolved if resolved.is_dir() else resolved.parent
    try:
        completed = run_subprocess(
            ["git", "-C", str(probe_root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout_seconds=configured_git_timeout_seconds(),
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return _CheckpointScope(
            root=resolved if resolved.is_dir() else resolved.parent,
            mode="filesystem-snapshot",
            original_path=resolved,
        )

    git_root = Path(completed.stdout.strip())
    if resolved == git_root:
        return _CheckpointScope(
            root=git_root,
            mode="git-worktree-snapshot",
            original_path=resolved,
        )
    return _CheckpointScope(
        root=resolved if resolved.is_dir() else resolved.parent,
        mode="filesystem-snapshot",
        original_path=resolved,
    )


def _detect_checkpoint_root(path: Path) -> tuple[Path, str]:
    scope = _detect_checkpoint_scope(path)
    return scope.root, scope.mode


def _checkpoint_storage_dir(root: Path) -> Path:
    return root / _CHECKPOINT_DIRNAME / _CHECKPOINTS_SUBDIR


def _display_command(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _undo_argv(scope: _CheckpointScope, checkpoint_id: str) -> list[str]:
    undo_path = scope.original_path if scope.scope_kind == "file" else scope.root
    return ["tg", "checkpoint", "undo", checkpoint_id, str(undo_path)]


def _index_path(root: Path) -> Path:
    return _checkpoint_storage_dir(root) / _INDEX_FILE


def _discovery_cache_path(search_root: Path) -> Path:
    return search_root / _CHECKPOINT_DIRNAME / _DISCOVERY_CACHE_FILE


def _discovery_cache_key(*, full: bool, max_depth: int) -> str:
    return f"{'full' if full else 'bounded'}:{max_depth}"


def _fingerprint_index_path(index_path: Path) -> dict[str, Any] | None:
    try:
        stat = index_path.stat()
    except OSError:
        return None
    return {
        "path": str(index_path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def _read_cached_checkpoint_index_paths(
    search_root: Path,
    *,
    full: bool,
    max_depth: int,
) -> tuple[set[Path], bool] | None:
    cache_path = _discovery_cache_path(search_root)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != _DISCOVERY_CACHE_VERSION:
        return None
    entries_by_key = payload.get("entries")
    if not isinstance(entries_by_key, dict):
        return None
    entry = entries_by_key.get(_discovery_cache_key(full=full, max_depth=max_depth))
    if not isinstance(entry, dict):
        return None
    created_at = entry.get("created_at_epoch_s")
    if not isinstance(created_at, (int, float)):
        return None
    if time.time() - float(created_at) > _DISCOVERY_CACHE_TTL_SECONDS:
        return None
    fingerprints = entry.get("index_paths")
    if not isinstance(fingerprints, list):
        return None

    index_paths: set[Path] = set()
    for fingerprint in fingerprints:
        if not isinstance(fingerprint, dict):
            return None
        raw_path = fingerprint.get("path")
        if not isinstance(raw_path, str):
            return None
        index_path = Path(raw_path)
        current = _fingerprint_index_path(index_path)
        if current is None:
            return None
        if current.get("mtime_ns") != fingerprint.get("mtime_ns") or current.get(
            "size"
        ) != fingerprint.get("size"):
            return None
        index_paths.add(index_path)
    return index_paths, bool(entry.get("truncated", False))


def _write_cached_checkpoint_index_paths(
    search_root: Path,
    index_paths: set[Path],
    *,
    full: bool,
    max_depth: int,
    truncated: bool = False,
) -> None:
    cache_path = _discovery_cache_path(search_root)
    payload: dict[str, Any] = {"version": _DISCOVERY_CACHE_VERSION, "entries": {}}
    try:
        existing = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(existing, dict) and existing.get("version") == _DISCOVERY_CACHE_VERSION:
            payload = existing
            payload.setdefault("entries", {})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        payload["entries"] = entries
    entries[_discovery_cache_key(full=full, max_depth=max_depth)] = {
        "created_at_epoch_s": time.time(),
        "index_paths": [
            fingerprint
            for index_path in sorted(index_paths)
            if (fingerprint := _fingerprint_index_path(index_path)) is not None
        ],
        "truncated": bool(truncated),
    }
    _write_json_atomic(cache_path, payload)


def _valid_cached_checkpoint_index_paths_from_entry(entry: Any) -> set[Path]:
    if not isinstance(entry, dict):
        return set()
    fingerprints = entry.get("index_paths")
    if not isinstance(fingerprints, list):
        return set()

    index_paths: set[Path] = set()
    for fingerprint in fingerprints:
        if not isinstance(fingerprint, dict):
            continue
        raw_path = fingerprint.get("path")
        if not isinstance(raw_path, str):
            continue
        index_path = Path(raw_path)
        if _fingerprint_index_path(index_path) is not None:
            index_paths.add(index_path)
    return index_paths


def _checkpoint_discovery_home_boundary() -> Path | None:
    try:
        return Path.home().expanduser().resolve()
    except OSError:
        return None


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _bounded_discovery_cache_roots_for_checkpoint(root: Path) -> list[Path]:
    root = root.expanduser().resolve()
    cache_roots = [root]
    home_boundary = _checkpoint_discovery_home_boundary()
    root_is_under_home = home_boundary is not None and _path_is_relative_to(root, home_boundary)
    for candidate in root.parents:
        if candidate.parent == candidate:
            break
        if root_is_under_home and home_boundary is not None and candidate == home_boundary.parent:
            break
        try:
            distance = len(root.relative_to(candidate).parts)
        except ValueError:
            continue
        if distance > _DISCOVERY_MAX_DEPTH:
            break
        cache_roots.append(candidate)
    return cache_roots


def _checkpoint_index_has_records(index_path: Path) -> bool:
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, list) and bool(payload)


def _refresh_bounded_discovery_caches_for_root(root: Path) -> None:
    index_path = _index_path(root)
    key = _discovery_cache_key(full=False, max_depth=_DISCOVERY_MAX_DEPTH)
    for search_root in _bounded_discovery_cache_roots_for_checkpoint(root):
        cache_path = _discovery_cache_path(search_root)
        payload: dict[str, Any] = {"version": _DISCOVERY_CACHE_VERSION, "entries": {}}
        try:
            existing = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("version") == _DISCOVERY_CACHE_VERSION:
                payload = existing
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass

        entries = payload.get("entries")
        if not isinstance(entries, dict):
            entries = {}
            payload["entries"] = entries

        index_paths = {
            current
            for current in _valid_cached_checkpoint_index_paths_from_entry(entries.get(key))
            if _checkpoint_index_has_records(current)
        }
        if _checkpoint_index_has_records(index_path):
            index_paths.add(index_path)
        else:
            index_paths.discard(index_path)
        previous_entry = entries.get(key)
        truncated = (
            bool(previous_entry.get("truncated", False))
            if isinstance(previous_entry, dict)
            else False
        )
        entries[key] = {
            "created_at_epoch_s": time.time(),
            "index_paths": [
                fingerprint
                for cached_index_path in sorted(index_paths)
                if (fingerprint := _fingerprint_index_path(cached_index_path)) is not None
            ],
            "truncated": truncated,
        }
        for entry_key in list(entries):
            if isinstance(entry_key, str) and entry_key.startswith("full:"):
                del entries[entry_key]
        try:
            _write_json_atomic(cache_path, payload)
        except OSError:
            continue


def _prime_bounded_discovery_caches_for_root(root: Path) -> None:
    _refresh_bounded_discovery_caches_for_root(root)


def _load_index(root: Path) -> list[CheckpointRecord]:
    index_path = _index_path(root)
    if not index_path.exists():
        return []
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return [CheckpointRecord(**entry) for entry in payload]


def _rebuild_index_from_checkpoint_metadata(root: Path) -> Path | None:
    storage_dir = _checkpoint_storage_dir(root)
    if not storage_dir.exists():
        return None
    records: list[CheckpointRecord] = []
    for metadata_path in sorted(storage_dir.glob(f"*/{_METADATA_FILE}")):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("active", True) is False:
            continue
        checkpoint_id = str(payload.get("checkpoint_id") or metadata_path.parent.name)
        created_at = str(payload.get("created_at") or "")
        if not checkpoint_id or not created_at:
            continue
        records.append(
            CheckpointRecord(
                version=int(payload.get("version") or _CHECKPOINT_VERSION),
                checkpoint_id=checkpoint_id,
                mode=str(payload.get("mode") or "filesystem-snapshot"),
                root=str(Path(str(payload.get("root") or root)).expanduser().resolve()),
                created_at=created_at,
                file_count=int(payload.get("file_count") or 0),
            )
        )
    records.sort(key=lambda record: record.created_at, reverse=True)
    if not records:
        return None
    _write_index(root, records)
    return _index_path(root)


def _write_index(root: Path, records: list[CheckpointRecord]) -> None:
    index_path = _index_path(root)
    _write_json_atomic(index_path, [asdict(record) for record in records])


def _git_snapshot_entries(root: Path) -> dict[str, bool]:
    git_timeout = configured_git_timeout_seconds()
    tracked = run_subprocess(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=False,
        check=True,
        timeout_seconds=git_timeout,
    ).stdout.split(b"\x00")
    untracked = run_subprocess(
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        text=False,
        check=True,
        timeout_seconds=git_timeout,
    ).stdout.split(b"\x00")

    entries: dict[str, bool] = {}
    for raw in [*tracked, *untracked]:
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape")
        if _CHECKPOINT_DIRNAME in Path(rel).parts:
            continue
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


def _snapshot_entries(scope: _CheckpointScope) -> dict[str, bool]:
    if scope.target_relative is not None:
        return {scope.target_relative.as_posix(): (scope.root / scope.target_relative).exists()}
    if scope.mode == "git-worktree-snapshot":
        return _git_snapshot_entries(scope.root)
    return _filesystem_snapshot_entries(scope.root)


def _checkpoint_dir(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_storage_dir(root) / checkpoint_id


def _snapshot_path(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_dir(root, checkpoint_id) / _SNAPSHOT_SUBDIR


def _metadata_path(root: Path, checkpoint_id: str) -> Path:
    return _checkpoint_dir(root, checkpoint_id) / _METADATA_FILE


def _write_checkpoint_metadata(
    root: Path,
    result: CheckpointCreateResult,
    entries: dict[str, bool],
    *,
    scope_kind: str,
    original_path: Path,
) -> None:
    payload: dict[str, Any] = {
        "version": _CHECKPOINT_VERSION,
        "checkpoint_id": result.checkpoint_id,
        "mode": result.mode,
        "root": result.root,
        "scope": scope_kind,
        "original_path": str(original_path),
        "created_at": result.created_at,
        "file_count": result.file_count,
        "entries": entries,
        "active": True,
    }
    _write_json_atomic(_metadata_path(root, result.checkpoint_id), payload)


def create_checkpoint(path: str = ".") -> CheckpointCreateResult:
    scope = _detect_checkpoint_scope(Path(path))
    root = scope.root
    mode = scope.mode
    created_at = datetime.now(UTC).isoformat()
    checkpoint_id = f"ckpt-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    entries = _snapshot_entries(scope)

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
        undo_argv=_undo_argv(scope, checkpoint_id),
        undo_command=_display_command(_undo_argv(scope, checkpoint_id)),
    )
    _write_checkpoint_metadata(
        root,
        result,
        entries,
        scope_kind=scope.scope_kind,
        original_path=scope.original_path,
    )

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
    _prime_bounded_discovery_caches_for_root(root)
    return result


def list_checkpoints(path: str = ".") -> list[CheckpointRecord]:
    root, _mode = _detect_checkpoint_root(Path(path))
    return _load_index(root)


def describe_checkpoint_scope(path: str = ".") -> CheckpointScopeResult:
    root, mode = _detect_checkpoint_root(Path(path))
    records = _load_index(root)
    return CheckpointScopeResult(
        root=str(root),
        mode=mode,
        checkpoint_count=len(records),
        checkpoints=records,
    )


def _bounded_checkpoint_index_paths(
    search_root: Path,
    *,
    include_generated: bool,
    max_depth: int = _DISCOVERY_MAX_DEPTH,
) -> tuple[set[Path], bool]:
    truncated = False
    index_paths: set[Path] = set()
    stack: list[tuple[Path, int]] = [(search_root, 0)]
    visited_directories = 0
    while stack:
        current, depth = stack.pop()
        visited_directories += 1
        if visited_directories > _DISCOVERY_MAX_DIRECTORIES:
            truncated = True
            break
        index_path = _index_path(current)
        if not index_path.exists():
            rebuilt = _rebuild_index_from_checkpoint_metadata(current)
            if rebuilt is not None:
                index_path = rebuilt
        if index_path.exists():
            index_paths.add(index_path)

        if depth >= max_depth:
            continue

        child_dirs: list[Path] = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if entry.name == _CHECKPOINT_DIRNAME:
                        continue
                    child = Path(entry.path)
                    if not include_generated and _is_generated_discovery_dir(child):
                        continue
                    child_dirs.append(child)
        except OSError:
            continue
        for child in reversed(child_dirs):
            stack.append((child, depth + 1))
    return index_paths, truncated


def _nearby_checkpoint_index_paths(search_root: Path) -> set[Path]:
    candidates: list[Path] = [search_root]
    candidates.extend(parent for parent in search_root.parents if parent != search_root)
    try:
        candidates.extend(
            child
            for child in sorted(search_root.iterdir(), key=lambda candidate: candidate.name)
            if child.is_dir() and not _is_generated_discovery_dir(child)
        )
    except OSError:
        pass

    index_paths: set[Path] = set()
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower() if os.name == "nt" else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        index_path = _index_path(resolved)
        if index_path.exists():
            index_paths.add(index_path)
    return index_paths


def _full_checkpoint_index_paths(search_root: Path) -> set[Path]:
    index_paths: set[Path] = set()
    own_index = _index_path(search_root)
    if not own_index.exists():
        rebuilt = _rebuild_index_from_checkpoint_metadata(search_root)
        if rebuilt is not None:
            own_index = rebuilt
    if own_index.exists():
        index_paths.add(own_index)
    try:
        index_paths.update(
            candidate
            for candidate in search_root.rglob(_INDEX_FILE)
            if candidate.parent.name == _CHECKPOINTS_SUBDIR
            and candidate.parent.parent.name == _CHECKPOINT_DIRNAME
        )
    except OSError:
        pass
    try:
        for metadata_path in search_root.rglob(_METADATA_FILE):
            if (
                metadata_path.parent.parent.name != _CHECKPOINTS_SUBDIR
                or metadata_path.parent.parent.parent.name != _CHECKPOINT_DIRNAME
            ):
                continue
            root = metadata_path.parent.parent.parent.parent
            rebuilt = _rebuild_index_from_checkpoint_metadata(root)
            if rebuilt is not None:
                index_paths.add(rebuilt)
    except OSError:
        pass
    return index_paths


def _scopes_from_index_paths(index_paths: set[Path]) -> list[CheckpointScopeResult]:
    scopes: list[CheckpointScopeResult] = []
    seen_roots: set[Path] = set()
    for index_path in sorted(index_paths):
        root = index_path.parent.parent.parent
        if root in seen_roots:
            continue
        seen_roots.add(root)
        records = _load_index(root)
        if not records:
            continue
        mode = records[0].mode if records else "filesystem-snapshot"
        scopes.append(
            CheckpointScopeResult(
                root=str(root),
                mode=mode,
                checkpoint_count=len(records),
                checkpoints=records,
            )
        )
    return scopes


def discover_checkpoint_scopes(
    path: str = ".",
    *,
    full: bool = False,
) -> list[CheckpointScopeResult]:
    return discover_checkpoint_scopes_result(path, full=full).scopes


def discover_checkpoint_scopes_result(
    path: str = ".",
    *,
    full: bool = False,
) -> CheckpointDiscoveryResult:
    resolved = Path(path).expanduser().resolve()
    search_root = resolved if resolved.is_dir() else resolved.parent
    max_depth = 2**31 - 1 if full else _DISCOVERY_MAX_DEPTH
    truncated = False
    cached = _read_cached_checkpoint_index_paths(
        search_root,
        full=full,
        max_depth=max_depth,
    )
    if cached is None:
        if full:
            index_paths = _full_checkpoint_index_paths(search_root)
            truncated = False
        else:
            index_paths, truncated = _bounded_checkpoint_index_paths(
                search_root,
                include_generated=False,
            )
        _write_cached_checkpoint_index_paths(
            search_root,
            index_paths,
            full=full,
            max_depth=max_depth,
            truncated=truncated,
        )
    else:
        index_paths, truncated = cached
    return CheckpointDiscoveryResult(
        scopes=_scopes_from_index_paths(index_paths),
        truncated=truncated,
    )


def discover_nearby_checkpoint_scopes(path: str = ".") -> list[CheckpointScopeResult]:
    resolved = Path(path).expanduser().resolve()
    search_root = resolved if resolved.is_dir() else resolved.parent
    scopes = _scopes_from_index_paths(_nearby_checkpoint_index_paths(search_root))
    return [
        scope
        for scope in scopes
        if _path_is_relative_to(Path(scope.root).expanduser().resolve(), search_root)
    ]


def discover_cached_checkpoint_scopes(path: str = ".") -> list[CheckpointScopeResult]:
    resolved = Path(path).expanduser().resolve()
    search_root = resolved if resolved.is_dir() else resolved.parent
    cached = _read_cached_checkpoint_index_paths(
        search_root,
        full=False,
        max_depth=_DISCOVERY_MAX_DEPTH,
    )
    if cached is None:
        return []
    index_paths, _truncated = cached
    return _scopes_from_index_paths(index_paths)


def resolve_latest_checkpoint(path: str = ".") -> CheckpointLatestResult:
    scope = describe_checkpoint_scope(path)
    if scope.checkpoints:
        record = scope.checkpoints[0]
        return CheckpointLatestResult(
            checkpoint_id=record.checkpoint_id,
            root=scope.root,
            mode=scope.mode,
        )

    resolved = Path(path).expanduser().resolve()
    search_root = resolved if resolved.is_dir() else resolved.parent
    discovered = [
        child_scope
        for child_scope in [
            *discover_nearby_checkpoint_scopes(path),
            *discover_cached_checkpoint_scopes(path),
        ]
        if child_scope.checkpoints
        and _path_is_relative_to(Path(child_scope.root).expanduser().resolve(), search_root)
    ]
    deduped: list[CheckpointScopeResult] = []
    seen_roots: set[str] = set()
    for child_scope in discovered:
        key = child_scope.root.lower() if os.name == "nt" else child_scope.root
        if key in seen_roots:
            continue
        seen_roots.add(key)
        deduped.append(child_scope)
    discovered = deduped
    if not discovered:
        raise FileNotFoundError(f"No checkpoints found under {resolved}.")
    if len(discovered) > 1:
        roots = ", ".join(scope.root for scope in discovered[:5])
        suffix = "" if len(discovered) <= 5 else f", ... ({len(discovered)} total)"
        raise ValueError(
            "Multiple checkpoint scopes found under "
            f"{Path(path).expanduser().resolve()}; pass a narrower PATH or explicit checkpoint id. "
            f"Scopes: {roots}{suffix}"
        )

    child_scope = discovered[0]
    record = child_scope.checkpoints[0]
    return CheckpointLatestResult(
        checkpoint_id=record.checkpoint_id,
        root=child_scope.root,
        mode=child_scope.mode,
    )


def load_checkpoint_metadata(checkpoint_id: str, path: str = ".") -> dict[str, Any]:
    root, _mode = _detect_checkpoint_root(Path(path))
    metadata_path = _metadata_path(root, checkpoint_id)
    if not metadata_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint metadata must be a JSON object.")
    return payload


def undo_checkpoint(checkpoint_id: str, path: str = ".") -> CheckpointUndoResult:
    root, mode = _detect_checkpoint_root(Path(path))
    metadata_path = _metadata_path(root, checkpoint_id)
    if not metadata_path.exists():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    entries: dict[str, bool] = metadata["entries"]
    snapshot_dir = _snapshot_path(root, checkpoint_id)
    root_resolved = root.resolve()

    # Validate every metadata entry stays inside the checkpoint root BEFORE mutating
    # anything. Failing fast here means a tampered manifest cannot overwrite or delete
    # files outside the repo, and a single bad entry cannot leave a partially-restored
    # tree (audit S1).
    resolved_targets = {
        rel_path: _resolve_within_root(root, root_resolved, rel_path) for rel_path in entries
    }

    scope_kind = str(metadata.get("scope", "tree"))
    if scope_kind == "file":
        current_entries: dict[str, bool] = {}
    elif mode == "git-worktree-snapshot":
        current_entries = _git_snapshot_entries(root)
    else:
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
        target = resolved_targets[rel_path]
        if exists:
            source = snapshot_dir / Path(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored_files += 1
        elif target.exists():
            target.unlink()
            removed_paths += 1

    if scope_kind != "file" and mode != "git-worktree-snapshot":
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

    _refresh_bounded_discovery_caches_for_root(root)

    return CheckpointUndoResult(
        checkpoint_id=checkpoint_id,
        mode=mode,
        root=str(root),
        restored_files=restored_files,
        removed_paths=removed_paths,
    )
