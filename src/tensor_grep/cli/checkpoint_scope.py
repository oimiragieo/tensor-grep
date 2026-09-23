"""Scoped-checkpoint (`tg checkpoint create --paths`) path confinement helpers.

Split out of ``checkpoint_store.py`` (file-size ratchet), mirroring ``checkpoint_retention.py``.
"""

from __future__ import annotations

from pathlib import Path


def resolved_rel_within(root_resolved: Path, target: Path) -> str | None:
    """POSIX path of ``target`` (symlinks resolved) relative to the root, or None if it escapes."""
    resolved = target.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return None
    return resolved.relative_to(root_resolved).as_posix()


def matches_scoped_paths(rel_entry: str, scoped_list: list[str]) -> bool:
    """Return True if rel_entry matches or is contained within any of scoped_list."""
    norm_entry = Path(rel_entry).as_posix()
    for s in scoped_list:
        norm_s = Path(s).as_posix()
        if norm_s in (".", ""):
            return True
        if norm_entry == norm_s or norm_entry.startswith(norm_s.rstrip("/") + "/"):
            return True
    return False


def scope_violation(
    root_resolved: Path, target: Path, rel: str, scoped: list[str], what: str
) -> str | None:
    """Scoped undo pre-flight. ``target`` must resolve inside the root AND inside ``scoped``, so an
    in-scope symlink/junction cannot redirect a restore or delete onto an unselected file.
    Returns the refusal message, or None when the target is confined."""
    resolved_rel = resolved_rel_within(root_resolved, target)
    if resolved_rel is None:
        return f"{what} {rel!r} escapes checkpoint root: {target.resolve()}"
    if not matches_scoped_paths(resolved_rel, scoped):
        return (
            f"{what} {rel!r} resolves to {resolved_rel!r}, which escapes the scoped paths: {scoped}"
        )
    return None
