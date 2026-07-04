"""Regression tests for the atomic undo fix (H2).

Guards against the data-loss bug where ``tg checkpoint undo`` would partially
restore a working tree and then abort when it encountered a missing snapshot
file, leaving a half-clobbered tree while reporting ok=false.

These tests import only ``checkpoint_store`` (no ``cli.main`` / rust_core) so
they run standalone and in CI without any compiled extension present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store
from tensor_grep.cli.checkpoint_store import CheckpointCorruptError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(root: Path, files: dict[str, str]) -> None:
    """Write ``files`` dict (relpath -> content) under ``root``."""
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_undo_cleanup_does_not_remove_or_follow_a_directory_symlink(tmp_path: Path) -> None:
    """Round-7 fresh-eyes: the post-undo empty-dir cleanup sweep must skip symlinks.

    ``root.rglob('*')`` yields a directory symlink, ``is_dir()`` follows it, and ``rmdir()`` would
    delete the user's symlink (or act through it on some platforms) -- the symlink-follow deletion
    class. The symlink is created BEFORE the checkpoint so undo's extra-file removal does not touch
    it and it reaches the cleanup sweep.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _make_project(root, {"src/alpha.py": "alpha\n"})

    external = tmp_path / "external"
    (external / "keep").mkdir(parents=True)
    link = root / "linkdir"
    try:
        link.symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported / not privileged on this platform")

    created = checkpoint_store.create_checkpoint(str(root))
    (root / "src" / "alpha.py").write_text("alpha-MUTATED\n", encoding="utf-8")  # something to undo
    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert link.is_symlink(), "the cleanup sweep removed the directory symlink"
    assert (external / "keep").is_dir(), (
        "the cleanup sweep acted THROUGH the symlink into its target"
    )


# ---------------------------------------------------------------------------
# Test: corrupt snapshot aborts BEFORE touching any working-tree file
# ---------------------------------------------------------------------------


def test_undo_with_missing_snapshot_file_leaves_tree_intact(tmp_path: Path) -> None:
    """Deleting a snapshot blob must prevent any working-tree mutation.

    Regression for H2: previously undo would delete extra-files and overwrite
    some targets before hitting the missing blob, leaving a partial tree while
    reporting ok=false / 'checkpoint_not_found'.
    """
    root = tmp_path / "repo"
    root.mkdir()

    # Initial state: two files.
    _make_project(
        root,
        {
            "src/alpha.py": "alpha-original\n",
            "src/beta.py": "beta-original\n",
        },
    )

    # Create checkpoint capturing both files.
    created = checkpoint_store.create_checkpoint(str(root))
    ckpt_id = created.checkpoint_id

    # Mutate the working tree so there is something to restore.
    (root / "src" / "alpha.py").write_text("alpha-MUTATED\n", encoding="utf-8")
    (root / "src" / "beta.py").write_text("beta-MUTATED\n", encoding="utf-8")
    # Add a new file not in the snapshot (should be removed on successful undo).
    (root / "src" / "extra.py").write_text("extra\n", encoding="utf-8")

    # Corrupt the checkpoint by deleting the alpha.py snapshot blob.
    snapshot_dir = checkpoint_store._snapshot_path(root, ckpt_id)
    alpha_blob = snapshot_dir / "src" / "alpha.py"
    assert alpha_blob.exists(), "pre-condition: snapshot blob must exist before we corrupt it"
    alpha_blob.unlink()

    # undo_checkpoint MUST raise CheckpointCorruptError.
    with pytest.raises(CheckpointCorruptError) as exc_info:
        checkpoint_store.undo_checkpoint(ckpt_id, str(root))

    err = exc_info.value
    # Error message must not contain raw OS error text such as "[WinError 2]" or
    # "[Errno 2]" — only a clean, human-readable description.
    assert "[WinError" not in str(err), f"Raw WinError leaked into message: {err}"
    assert "[Errno" not in str(err), f"Raw errno leaked into message: {err}"
    # The missing_files attribute must list the corrupted entry.
    assert "src/alpha.py" in err.missing_files, (
        f"Expected 'src/alpha.py' in missing_files, got {err.missing_files}"
    )

    # --- Tree must be completely unchanged ---
    assert (root / "src" / "alpha.py").read_text(encoding="utf-8") == "alpha-MUTATED\n", (
        "alpha.py was modified despite corrupt snapshot — atomicity violated"
    )
    assert (root / "src" / "beta.py").read_text(encoding="utf-8") == "beta-MUTATED\n", (
        "beta.py was modified despite corrupt snapshot — atomicity violated"
    )
    assert (root / "src" / "extra.py").exists(), (
        "extra.py was deleted despite corrupt snapshot — atomicity violated"
    )


def test_undo_with_missing_snapshot_file_error_is_checkpoint_corrupt_not_file_not_found(
    tmp_path: Path,
) -> None:
    """CheckpointCorruptError must be distinct from FileNotFoundError.

    Agents check the error code to decide whether to retry; conflating
    "checkpoint record missing" with "snapshot blob missing" causes agents
    to believe their edits are intact when the tree is actually half-clobbered.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _make_project(root, {"mod.py": "v1\n"})

    created = checkpoint_store.create_checkpoint(str(root))
    ckpt_id = created.checkpoint_id

    (root / "mod.py").write_text("v2\n", encoding="utf-8")

    # Remove the snapshot blob to simulate corruption.
    blob = checkpoint_store._snapshot_path(root, ckpt_id) / "mod.py"
    blob.unlink()

    # Must raise CheckpointCorruptError, NOT FileNotFoundError.
    with pytest.raises(CheckpointCorruptError):
        checkpoint_store.undo_checkpoint(ckpt_id, str(root))

    # Must NOT raise plain FileNotFoundError for an existing checkpoint record.
    # (If it did, the caller couldn't distinguish "record missing" from "blob corrupt".)
    try:
        checkpoint_store.undo_checkpoint(ckpt_id, str(root))
    except CheckpointCorruptError:
        pass  # expected
    except FileNotFoundError as exc:
        pytest.fail(f"undo raised FileNotFoundError instead of CheckpointCorruptError: {exc}")


def test_undo_succeeds_when_snapshot_intact(tmp_path: Path) -> None:
    """Baseline: a normal create→mutate→undo roundtrip still works after the fix."""
    root = tmp_path / "repo"
    root.mkdir()
    _make_project(
        root,
        {
            "pkg/a.py": "original-a\n",
            "pkg/b.py": "original-b\n",
        },
    )

    created = checkpoint_store.create_checkpoint(str(root))
    ckpt_id = created.checkpoint_id

    (root / "pkg" / "a.py").write_text("modified-a\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text("modified-b\n", encoding="utf-8")
    (root / "pkg" / "c.py").write_text("new-c\n", encoding="utf-8")

    result = checkpoint_store.undo_checkpoint(ckpt_id, str(root))

    assert result.restored_files == 2
    assert (root / "pkg" / "a.py").read_text(encoding="utf-8") == "original-a\n"
    assert (root / "pkg" / "b.py").read_text(encoding="utf-8") == "original-b\n"
    # c.py was not in the snapshot so it must have been removed.
    assert not (root / "pkg" / "c.py").exists()


def test_undo_missing_metadata_raises_file_not_found(tmp_path: Path) -> None:
    """undo with a non-existent checkpoint id must still raise FileNotFoundError."""
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(FileNotFoundError):
        checkpoint_store.undo_checkpoint("ckpt-does-not-exist", str(root))


def test_checkpoint_corrupt_error_message_contains_count_and_path(tmp_path: Path) -> None:
    """CheckpointCorruptError message must identify the first bad file and total count."""
    root = tmp_path / "repo"
    root.mkdir()
    _make_project(
        root,
        {
            "x.py": "x\n",
            "y.py": "y\n",
        },
    )

    created = checkpoint_store.create_checkpoint(str(root))
    ckpt_id = created.checkpoint_id

    # Remove both blobs to trigger a multi-file corrupt report.
    snap = checkpoint_store._snapshot_path(root, ckpt_id)
    (snap / "x.py").unlink()
    (snap / "y.py").unlink()

    with pytest.raises(CheckpointCorruptError) as exc_info:
        checkpoint_store.undo_checkpoint(ckpt_id, str(root))

    err = exc_info.value
    msg = str(err)
    # Both files should appear in missing_files.
    assert len(err.missing_files) == 2
    # The count must be in the message.
    assert "2 snapshot file" in msg
    # Must name the first missing entry.
    assert err.missing_files[0] in msg
    # No raw OS errors.
    assert "[WinError" not in msg
    assert "[Errno" not in msg


def test_checkpoint_corrupt_error_missing_files_attribute_populated(tmp_path: Path) -> None:
    """CheckpointCorruptError.missing_files must be a non-empty list of rel paths."""
    root = tmp_path / "repo"
    root.mkdir()
    _make_project(root, {"lib.py": "lib\n"})

    created = checkpoint_store.create_checkpoint(str(root))
    ckpt_id = created.checkpoint_id

    (checkpoint_store._snapshot_path(root, ckpt_id) / "lib.py").unlink()

    with pytest.raises(CheckpointCorruptError) as exc_info:
        checkpoint_store.undo_checkpoint(ckpt_id, str(root))

    err = exc_info.value
    assert isinstance(err.missing_files, list)
    assert len(err.missing_files) >= 1
    assert all(isinstance(f, str) for f in err.missing_files)
