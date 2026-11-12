"""Owner-anchored checkpoint snapshot publication."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from tensor_grep.cli.checkpoint_store import CheckpointCreateResult, _CheckpointScope


def create_checkpoint_with_owner(
    scope: _CheckpointScope,
    paths: list[str] | None,
    normalized_label: str | None,
    owner: Any,
) -> CheckpointCreateResult:
    # Import the store lazily so it remains the single source of checkpoint contracts and
    # test seams while avoiding a module import cycle during normal store initialization.
    from tensor_grep.cli import checkpoint_store
    from tensor_grep.cli.checkpoint_retention import (
        _configured_checkpoint_max_file_bytes,
        _configured_checkpoint_max_total_bytes,
    )
    from tensor_grep.io.checkpoint_directory import _SnapshotCopyLimitExceededError

    store: Any = checkpoint_store

    root = scope.root
    mode = scope.mode
    root_resolved = root.resolve()
    storage_dir = store._checkpoint_storage_dir(root)
    owner.pin_existing_storage(root, storage_dir)

    scoped_paths: list[str] | None = None
    if paths is not None:
        if scope.scope_kind == "file":
            raise ValueError("Cannot specify both a single-file path and --paths")
        scoped_rel_paths: set[str] = set()
        for path in paths:
            candidate = Path(path) if Path(path).is_absolute() else root / path
            relative = store.resolved_rel_within(root_resolved, candidate)
            if relative is None:
                raise ValueError(f"Path outside checkpoint root: {path!r}")
            store._resolve_within_root(root, root_resolved, relative)
            scoped_rel_paths.add(relative)
        scoped_paths = sorted(scoped_rel_paths)

    created_at = store.datetime.now(store.UTC).isoformat()
    checkpoint_id = (
        f"ckpt-{store.datetime.now(store.UTC).strftime('%Y%m%d%H%M%S')}-{store.uuid4().hex[:8]}"
    )
    entries, skipped_nested_repos = store._snapshot_entries(scope)

    if scoped_paths is not None:
        entries = {
            key: value
            for key, value in entries.items()
            if store.matches_scoped_paths(key, scoped_paths)
        }

    # Check budgets before creating any snapshot directories.
    store._check_checkpoint_disk_budget(root, entries)
    owner.set_snapshot_byte_limits(
        _configured_checkpoint_max_file_bytes(), _configured_checkpoint_max_total_bytes()
    )

    try:
        owner.ensure_storage(root, storage_dir)
        checkpoint_dir = storage_dir / checkpoint_id
        owner.create_directory(checkpoint_dir)
        snapshot_dir = checkpoint_dir / store._SNAPSHOT_SUBDIR
        owner.create_directory(snapshot_dir)

        for relative_path, exists in entries.items():
            if not exists:
                continue
            try:
                owner.copy_snapshot_file(root, snapshot_dir, relative_path)
            except _SnapshotCopyLimitExceededError as exc:
                raise store.CheckpointBudgetExceededError(
                    f"Checkpoint refused: {relative_path!r} {exc.strerror} (raise "
                    f"{store._CHECKPOINT_MAX_FILE_BYTES_ENV} or "
                    f"{store._CHECKPOINT_MAX_TOTAL_BYTES_ENV} to allow a larger snapshot)."
                ) from exc
            except OSError as exc:
                if "refuses a reparse point" in str(exc) or "is not a real directory" in str(exc):
                    store._resolve_parent_within_root(root, root_resolved, relative_path)
                raise

        result = store.CheckpointCreateResult(
            checkpoint_id=checkpoint_id,
            mode=mode,
            root=str(root),
            created_at=created_at,
            file_count=len(entries),
            undo_argv=store._undo_argv(scope, checkpoint_id),
            undo_command=store._display_command(store._undo_argv(scope, checkpoint_id)),
            skipped_nested_repos=skipped_nested_repos,
            scoped_paths=scoped_paths,
            label=normalized_label,
        )
        store._write_checkpoint_metadata(
            root,
            result,
            entries,
            scope_kind=scope.scope_kind,
            original_path=scope.original_path,
            scoped_paths=scoped_paths,
            owner=owner,
        )

        # Keep metadata and index publication in the same failure boundary as snapshot copying.
        with store.index_lock(store._index_path(root)):
            records = store._load_index(root)
            records.insert(
                0,
                store.CheckpointRecord(
                    version=store._CHECKPOINT_VERSION,
                    checkpoint_id=checkpoint_id,
                    mode=mode,
                    root=str(root),
                    created_at=created_at,
                    file_count=len(entries),
                    label=normalized_label,
                ),
            )
            records, dropped_dirs = store._select_retained_checkpoints(root, records)
            store._write_index(root, records, owner=owner)
    except BaseException as exc:
        if owner.created_paths:
            retained = owner.created_paths[0]
            reason = (
                f"checkpoint directory tree retained under {retained}; inspect "
                f"{store._index_path(root)} to determine whether the checkpoint was published"
            )
            if hasattr(exc, "add_note"):
                exc.add_note(reason)
            store._LOGGER.warning(reason)
        raise

    for directory in dropped_dirs:
        shutil.rmtree(directory, ignore_errors=True)
    store._prime_bounded_discovery_caches_for_root(root)
    return cast("CheckpointCreateResult", result)
