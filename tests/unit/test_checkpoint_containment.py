"""Path-containment regression tests for the checkpoint rollback safety net.

These import ONLY ``checkpoint_store`` (no ``cli.main`` / rust_core), so they run
standalone and in CI. They guard against the S1 audit finding: ``undo_checkpoint``
restored ``metadata["entries"]`` keys by joining them to the checkpoint root with no
containment check, so a tampered metadata file with an absolute or ``..`` entry could
overwrite or delete files anywhere the process can write (arbitrary file write/delete,
reachable from the MCP ``tg_checkpoint_undo`` tool, CLI ``tg checkpoint undo``, and
policy rollback).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store


def _write_metadata(root: Path, checkpoint_id: str, entries: dict[str, bool]) -> None:
    meta_path = checkpoint_store._metadata_path(root, checkpoint_id)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": checkpoint_store._CHECKPOINT_VERSION,
        "checkpoint_id": checkpoint_id,
        "mode": "filesystem-snapshot",
        "root": str(root),
        "scope": "tree",
        "original_path": str(root),
        "created_at": "2026-01-01T00:00:00+00:00",
        "file_count": len(entries),
        "entries": entries,
        "active": True,
    }
    meta_path.write_text(json.dumps(payload), encoding="utf-8")


def test_undo_restores_in_root_file_roundtrip(tmp_path: Path) -> None:
    """Baseline: a normal create→modify→undo restores the original content."""
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "pkg" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("original\n", encoding="utf-8")

    created = checkpoint_store.create_checkpoint(str(root))
    target.write_text("MUTATED\n", encoding="utf-8")

    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.parametrize(
    "evil_rel",
    [
        "../escape_relative.txt",
        "../../escape_two_levels.txt",
        "pkg/../../escape_via_subdir.txt",
    ],
)
def test_undo_refuses_parent_traversal_entries(tmp_path: Path, evil_rel: str) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = (root / evil_rel).resolve()
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("DO NOT TOUCH\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-relative"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    # A snapshot blob the attacker would want copied over `outside`.
    (snapshot_dir / "payload").write_text("ATTACKER CONTENT\n", encoding="utf-8")
    _write_metadata(root, checkpoint_id, {evil_rel: True})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    # The out-of-root file must be untouched.
    assert outside.read_text(encoding="utf-8") == "DO NOT TOUCH\n"


def test_undo_refuses_absolute_entries(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET\n", encoding="utf-8")

    checkpoint_id = "ckpt-evil-absolute"
    snapshot_dir = checkpoint_store._snapshot_path(root, checkpoint_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    # Absolute entry marked as deleted → undo would unlink the victim outright.
    _write_metadata(root, checkpoint_id, {str(victim): False})

    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))

    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "SECRET\n"


@pytest.mark.parametrize(
    "evil_id",
    ["../escape", "../../escape", "sub/../../escape", ".."],
)
def test_checkpoint_dir_refuses_traversal_id(tmp_path: Path, evil_id: str) -> None:
    """Audit HIGH: checkpoint_id itself (not just entry keys) was joined to the store
    with no validation, so an absolute/`..` id escaped the checkpoint store."""
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError):
        checkpoint_store._checkpoint_dir(root, evil_id)


def test_load_metadata_refuses_traversal_to_external_file(tmp_path: Path) -> None:
    """Audit HIGH: an unvalidated checkpoint_id let load_checkpoint_metadata read a
    metadata.json OUTSIDE the checkpoint store (arbitrary metadata disclosure)."""
    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external_store"
    external.mkdir()
    (external / "metadata.json").write_text(json.dumps({"secret": "leak"}), encoding="utf-8")

    storage = checkpoint_store._checkpoint_storage_dir(root)
    evil_id = os.path.relpath(external, storage)  # ..(/..)+/external_store
    with pytest.raises(ValueError):
        checkpoint_store.load_checkpoint_metadata(evil_id, str(root))


def test_undo_refuses_traversal_checkpoint_id(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError):
        checkpoint_store.undo_checkpoint("../../evil-ckpt", str(root))


def test_create_checkpoint_does_not_disclose_symlink_target(tmp_path: Path) -> None:
    """Audit HIGH: create_checkpoint followed symlinks, copying the CONTENT of a file OUTSIDE
    the checkpoint root into the snapshot (out-of-root disclosure, and it could re-materialize
    into the tree on undo). Symlinks must not be followed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("in-repo content\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-OUT-OF-ROOT\n", encoding="utf-8")
    try:
        (repo / "link.txt").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires privilege on this platform")

    created = checkpoint_store.create_checkpoint(str(repo))
    snapshot = checkpoint_store._snapshot_path(repo, created.checkpoint_id)

    # No snapshotted regular file may contain the out-of-root secret content.
    for path in snapshot.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert "SECRET-OUT-OF-ROOT" not in path.read_text(encoding="utf-8", errors="ignore")
