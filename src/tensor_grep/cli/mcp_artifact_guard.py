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
from tensor_grep.cli.mcp_path_errors import PathConfinementError

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
    root: Path | None = None,
) -> tuple[Path, WriteAuthorization]:
    """Confine like ``_confine_write_path``, then refuse any target that is not a ``.json`` file
    outside ``.git``, or that already exists and is not a prior tg artifact (``kind`` /
    ``routing_reason`` match). Existing targets are validated by a FULL ``json.loads`` (no
    prefix shortcut: a prefix check accepts duplicate or malformed documents).

    Returns the resolved path AND a ``WriteAuthorization`` that the caller must make binding
    around the write (``_index_lock.write_authorizations``): an ABSENT target is then published
    no-clobber, and an approved existing artifact is re-identified immediately before the
    replace. Without it the approval would be lost before the write (check-then-write race).

    Residual filesystem race: a window
    remains between the writer's final identity re-check (run before EACH replace attempt) and
    ``os.replace``. Windows has no handle-relative conditional replace, and an attacker who can
    rename or replace files in the user's workspace can overwrite the target directly without tg.
    This guard defends against an agent being tricked by path naming, not against a concurrent
    filesystem adversary."""
    # Imported here, not at module level: mcp_server imports mcp_audit_tools, which imports this
    # module, so a module-level import would make a cold import of this module fail.
    from tensor_grep.cli.mcp_server import _confine_write_path, _mcp_root

    resolved = _confine_write_path(candidate, anchor, label=label)
    # Forbidden-component rules are decided relative to the TRUSTED MCP ROOT, never the caller's
    # scan anchor: a scan root of `<root>/.git` would otherwise make `.git` invisible (the
    # artifact would look like a plain `new.json`). Rules: (1) the target must be a `.json` file;
    # (2) no path component of the target below the MCP root may be `.git` -- this covers the
    # scan anchor itself AND the artifact's own path.
    trusted_root = (root if root is not None else _mcp_root()).expanduser().resolve()
    try:
        rel_parts = resolved.relative_to(trusted_root).parts
    except ValueError:
        raise ArtifactWriteRefused(label) from None
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
    ancestor = None
    if parent_id is None:
        existing = parent
        while not existing.exists() and existing.parent != existing:
            existing = existing.parent
        if existing.is_dir():
            ancestor = (str(existing), dir_identity(existing))
    return resolved, WriteAuthorization(str(resolved), identity, parent_id, label, ancestor)
