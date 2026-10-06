"""Normalize optional labels stored with checkpoint records."""

from __future__ import annotations

from typing import Any

from tensor_grep.cli._index_lock import record_from_entry


def normalize_checkpoint_label(label: object) -> str | None:
    """Trim and validate a caller-supplied checkpoint label."""
    if label is None:
        return None
    if not isinstance(label, str):
        raise ValueError("Checkpoint label must be text.")
    normalized = label.strip()
    if not normalized or len(normalized) > 120 or not normalized.isprintable():
        raise ValueError("Checkpoint label must contain 1 to 120 printable characters.")
    return normalized


def stored_checkpoint_label(label: object) -> str | None:
    """Return valid stored annotation text, treating malformed values as absent."""
    try:
        return normalize_checkpoint_label(label)
    except ValueError:
        return None


def record_from_entry_with_label(record_cls: Any, entry: dict[str, Any]) -> Any:
    """Build an index record and discard malformed optional annotation values."""
    record = record_from_entry(record_cls, entry)
    record.label = stored_checkpoint_label(record.label)
    return record


def records_from_entries_with_labels(record_cls: Any, entries: list[dict[str, Any]]) -> list[Any]:
    """Load checkpoint index records while preserving old and malformed-label semantics."""
    return [record_from_entry_with_label(record_cls, entry) for entry in entries]
