"""MCP artifact-write guard: refuse overwriting files that are not prior tensor-grep artifacts.

``_confine_write_path`` only proves containment under the MCP root. For tools that WRITE an
artifact (ruleset-scan baseline/suppressions, review bundle) containment is not enough: any
in-root file (source, ``.git/config``) could be overwritten by naming it. This module adds the
second gate. It lives outside ``mcp_server.py`` because that file is size-ratcheted.
"""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli._index_lock import WriteAuthorization, dir_identity, file_identity
from tensor_grep.cli.mcp_server import PathConfinementError, _confine_write_path

# Full-parse cap, far above any real baseline/bundle so reruns keep working while one probe's
# memory stays bounded. An existing target larger than this is refused (fail closed).
_MCP_ARTIFACT_PROBE_MAX_BYTES = 256 * 1024 * 1024


class ArtifactWriteRefused(PathConfinementError):
    """Target is not a new ``.json`` file nor an existing tensor-grep artifact."""

    def __init__(self, label: str):
        # The message must NOT contain "must stay within" (confinement ratchet: an in-root
        # value never produces that phrase).
        ValueError.__init__(
            self, f"{label} must be a new .json file or an existing tensor-grep artifact (refused)"
        )


def _authorize_artifact_write_path(
    candidate: str,
    anchor: Path,
    *,
    label: str,
    allowed_kinds: frozenset[str] = frozenset(),
    allowed_routing_reasons: frozenset[str] = frozenset(),
) -> tuple[Path, WriteAuthorization]:
    """Confine like ``_confine_write_path``, then refuse any target that is not a ``.json`` file
    outside ``.git``, or that already exists and is not a prior tg artifact (``kind`` /
    ``routing_reason`` match). Existing targets are validated by a FULL ``json.loads`` (no
    prefix shortcut: a prefix check accepts duplicate or malformed documents).

    Returns the resolved path AND a ``WriteAuthorization`` that the caller must make binding
    around the write (``_index_lock.write_authorizations``): an ABSENT target is then published
    no-clobber, and an approved existing artifact is re-identified immediately before the
    replace. Without it the approval would be lost before the write (check-then-write race)."""
    resolved = _confine_write_path(candidate, anchor, label=label)
    rel_parts = resolved.relative_to(anchor.expanduser().resolve()).parts
    if resolved.suffix.lower() != ".json" or any(p.casefold() == ".git" for p in rel_parts):
        raise ArtifactWriteRefused(label)
    parent = resolved.parent
    parent_id = dir_identity(parent) if parent.is_dir() else None
    identity = None
    if resolved.exists():
        try:
            if not resolved.is_file():
                raise ArtifactWriteRefused(label)
            identity = file_identity(resolved)
            if identity[2] > _MCP_ARTIFACT_PROBE_MAX_BYTES:
                raise ArtifactWriteRefused(label)
            doc = json.loads(resolved.read_text(encoding="utf-8"))
            if file_identity(resolved) != identity:  # changed while being probed
                raise ArtifactWriteRefused(label)
        except ArtifactWriteRefused:
            raise
        except (OSError, ValueError, RecursionError, MemoryError):
            raise ArtifactWriteRefused(label) from None
        kind = doc.get("kind") if isinstance(doc, dict) else None
        reason = doc.get("routing_reason") if isinstance(doc, dict) else None
        # isinstance(str) BEFORE membership: {"kind": []} would raise TypeError (unhashable).
        if not (
            (isinstance(kind, str) and kind in allowed_kinds)
            or (isinstance(reason, str) and reason in allowed_routing_reasons)
        ):
            raise ArtifactWriteRefused(label)
    return resolved, WriteAuthorization(str(resolved), identity, parent_id, label)
