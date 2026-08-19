"""Checkpoint retention and disk-budget helpers, split out of `checkpoint_store.py`
(enterprise file-size campaign, Wave 1) to bring that module under the 1500-line
core limit.

Verified via `scripts/monkeypatch_binding_audit.py --module cli.checkpoint_store`:
none of the functions moved here are directly monkeypatched by tests, and none
of them touch the 3 early-binding hazard symbols that ARE monkeypatched
(`datetime`, `uuid4`, `index_lock`) -- those stay defined in `checkpoint_store.py`
and this module never needs them. The one facade symbol this module DOES call
(`_checkpoint_dir`) is also unpatched, so it is safe to reach via a deferred,
module-qualified import (`from tensor_grep.cli import checkpoint_store`, called
inside the function body) rather than `from checkpoint_store import
_checkpoint_dir` -- this avoids a real import-order-dependent circular-import
failure (the facade imports this module for its re-exports at its own top
level), matching the pattern used for the sibling `ast_workflow_rules.py` split.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from tensor_grep.cli.checkpoint_store import CheckpointRecord


def _configured_checkpoint_max() -> int:
    from tensor_grep.cli.checkpoint_store import _CHECKPOINT_MAX_ENV, _DEFAULT_CHECKPOINT_MAX

    raw = os.environ.get(_CHECKPOINT_MAX_ENV)
    if raw is None:
        return _DEFAULT_CHECKPOINT_MAX
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CHECKPOINT_MAX
    return value if value > 0 else _DEFAULT_CHECKPOINT_MAX


def _select_retained_checkpoints(
    root: Path,
    records: list[CheckpointRecord],
    *,
    max_records: int | None = None,
) -> tuple[list[CheckpointRecord], list[Path]]:
    """Pure selector for bounded on-disk checkpoint retention (round-4 DoS).

    Keep at most ``max_records`` newest records. Returns ``(retained, dirs_to_delete)`` and
    performs NO filesystem mutation -- the caller removes ``dirs_to_delete`` (metadata.json +
    the full snapshot copy for each dropped checkpoint). Doing no I/O here lets
    ``create_checkpoint`` run this selector INSIDE the index lock and defer the slow,
    index-unrelated ``rmtree`` calls until after the lock is released (q10 RMW race fix).

    M8: ``created_at`` is stamped BEFORE the caller acquires ``index_lock``, so under
    concurrent writers the insert (lock-arrival) order does not reliably match creation
    order -- trusting list position for the ``[:limit]`` cut can prune a genuinely newer
    checkpoint (the ``checkpoint undo`` safety net) and keep an older one. Re-sort by
    ``created_at`` (newest first) immediately before slicing.
    """
    from tensor_grep.cli import checkpoint_store

    limit = _configured_checkpoint_max() if max_records is None else max(1, int(max_records))
    if len(records) <= limit:
        return records, []
    ordered = sorted(records, key=lambda record: record.created_at, reverse=True)
    retained = ordered[:limit]
    dirs_to_delete: list[Path] = []
    for dropped in ordered[limit:]:
        try:
            dirs_to_delete.append(checkpoint_store._checkpoint_dir(root, dropped.checkpoint_id))
        except (OSError, ValueError):
            # ValueError: a traversal-shaped id in a tampered index is refused by _checkpoint_dir.
            pass
    return retained, dirs_to_delete


def _prune_checkpoint_records(
    root: Path,
    records: list[CheckpointRecord],
    *,
    max_records: int | None = None,
) -> list[CheckpointRecord]:
    """Bound on-disk checkpoint retention (round-4 DoS).

    Thin wrapper over ``_select_retained_checkpoints`` that removes the dropped checkpoints'
    directories immediately, so any existing caller/test that expects synchronous pruning is
    unchanged. Each dropped checkpoint's entire directory (metadata.json + the full snapshot
    copy) is removed so disk usage stays bounded — an uncapped store grows by ~one full scope
    copy per checkpoint.
    """
    retained, dirs_to_delete = _select_retained_checkpoints(root, records, max_records=max_records)
    for directory in dirs_to_delete:
        shutil.rmtree(directory, ignore_errors=True)
    return retained


def _configured_positive_int(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _configured_checkpoint_max_file_bytes() -> int:
    from tensor_grep.cli.checkpoint_store import (
        _CHECKPOINT_MAX_FILE_BYTES_ENV,
        _DEFAULT_CHECKPOINT_MAX_FILE_BYTES,
    )

    return _configured_positive_int(
        _CHECKPOINT_MAX_FILE_BYTES_ENV, _DEFAULT_CHECKPOINT_MAX_FILE_BYTES
    )


def _configured_checkpoint_max_total_bytes() -> int:
    from tensor_grep.cli.checkpoint_store import (
        _CHECKPOINT_MAX_TOTAL_BYTES_ENV,
        _DEFAULT_CHECKPOINT_MAX_TOTAL_BYTES,
    )

    return _configured_positive_int(
        _CHECKPOINT_MAX_TOTAL_BYTES_ENV, _DEFAULT_CHECKPOINT_MAX_TOTAL_BYTES
    )


def _configured_checkpoint_free_space_margin_bytes() -> int:
    from tensor_grep.cli.checkpoint_store import (
        _CHECKPOINT_FREE_SPACE_MARGIN_BYTES_ENV,
        _DEFAULT_CHECKPOINT_FREE_SPACE_MARGIN_BYTES,
    )

    return _configured_positive_int(
        _CHECKPOINT_FREE_SPACE_MARGIN_BYTES_ENV, _DEFAULT_CHECKPOINT_FREE_SPACE_MARGIN_BYTES
    )


def _check_checkpoint_disk_budget(root: Path, entries: dict[str, bool]) -> None:
    """Pre-flight disk-usage budget for create_checkpoint (audit H4).

    Stats every entry that will be copied (cheap; no copying yet) and refuses BEFORE any
    snapshot directory is created if a single file exceeds the per-file cap, the cumulative
    snapshot size exceeds the total-per-checkpoint cap, or performing the copy would leave
    less than the configured free-space margin on the destination filesystem. All three caps
    are env-configurable (sane defaults) so a repo with legitimately large tracked assets can
    raise the limit instead of being permanently blocked.
    """
    from tensor_grep.cli.checkpoint_store import (
        _CHECKPOINT_FREE_SPACE_MARGIN_BYTES_ENV,
        _CHECKPOINT_MAX_FILE_BYTES_ENV,
        _CHECKPOINT_MAX_TOTAL_BYTES_ENV,
        CheckpointBudgetExceededError,
    )

    max_file_bytes = _configured_checkpoint_max_file_bytes()
    max_total_bytes = _configured_checkpoint_max_total_bytes()
    free_margin_bytes = _configured_checkpoint_free_space_margin_bytes()

    total_bytes = 0
    for rel_path, exists in entries.items():
        if not exists:
            continue
        try:
            size = (root / rel_path).stat().st_size
        except OSError:
            # A vanished/unreadable source is reported by the copy loop itself; the budget
            # pre-flight only needs a best-effort size estimate, not definitive readability.
            continue
        if size > max_file_bytes:
            raise CheckpointBudgetExceededError(
                f"Checkpoint refused: {rel_path!r} is {size} bytes, over the per-file limit "
                f"of {max_file_bytes} bytes (raise {_CHECKPOINT_MAX_FILE_BYTES_ENV} to allow "
                "larger files)."
            )
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise CheckpointBudgetExceededError(
                "Checkpoint refused: snapshot size exceeds the per-checkpoint limit of "
                f"{max_total_bytes} bytes (raise {_CHECKPOINT_MAX_TOTAL_BYTES_ENV} to allow a "
                "larger checkpoint)."
            )

    try:
        free_bytes = shutil.disk_usage(root).free
    except OSError:
        # Cannot introspect free space on this filesystem; do not block the checkpoint on a
        # diagnostic we could not compute -- the per-file/total-bytes caps above still apply.
        return
    required_bytes = total_bytes + free_margin_bytes
    if free_bytes < required_bytes:
        raise CheckpointBudgetExceededError(
            f"Checkpoint refused: only {free_bytes} bytes free, but this checkpoint needs "
            f"{total_bytes} bytes plus a {free_margin_bytes}-byte safety margin (lower "
            f"{_CHECKPOINT_FREE_SPACE_MARGIN_BYTES_ENV} to change the margin)."
        )
