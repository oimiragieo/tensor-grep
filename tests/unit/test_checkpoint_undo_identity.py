"""Undo must leave changed parents and unrelated replacement files untouched."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store


def _directory_alias(alias: Path, destination: Path) -> None:
    if sys.platform != "win32":
        alias.symlink_to(destination, target_is_directory=True)
        return
    command = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
    result = subprocess.run(
        [str(command), "/d", "/c", "mklink", "/J", str(alias), str(destination)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
        check=False,
    )
    if result.returncode:
        pytest.skip("directory junction creation unavailable")


def test_failed_existing_copy_does_not_restore_through_changed_parent(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    target = root / "src/value.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"snapshot")
    checkpoint = checkpoint_store.create_checkpoint(str(root))
    target.write_bytes(b"current user bytes")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / target.name
    canary.write_bytes(b"outside canary")
    displaced = tmp_path / "original-src"
    copy2 = checkpoint_store.shutil.copy2
    failure = OSError("copy failed after parent swap attempt")
    reached = []

    def fail_copy(source, destination, **kwargs):
        if Path(destination) == target:
            reached.append(target)
            try:
                target.parent.rename(displaced)
            except OSError:
                raise failure from None
            _directory_alias(target.parent, outside)
            assert target.parent.resolve() == outside.resolve()
            raise failure
        return copy2(source, destination, **kwargs)

    monkeypatch.setattr(checkpoint_store.shutil, "copy2", fail_copy)
    with pytest.raises(OSError) as caught:
        checkpoint_store.undo_checkpoint(checkpoint.checkpoint_id, str(root))
    assert caught.value is failure
    assert reached == [target]
    assert canary.read_bytes() == b"outside canary", "rollback wrote through a changed parent"
    preserved = displaced / target.name if displaced.exists() else target
    assert preserved.read_bytes() == b"current user bytes"


def test_failed_absent_copy_does_not_delete_unrelated_replacement(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "value.py"
    target.write_bytes(b"snapshot")
    checkpoint = checkpoint_store.create_checkpoint(str(root))
    target.unlink()
    unrelated = tmp_path / "independent.py"
    unrelated.write_bytes(b"independent owner bytes")
    copy2 = checkpoint_store.shutil.copy2
    failure = OSError("copy failed before creating destination")
    reached = []
    replaced = []

    def fail_copy(source, destination, **kwargs):
        if Path(destination) == target:
            reached.append(target)
            try:
                os.replace(unrelated, target)
                replaced.append(True)
            except OSError:
                replaced.append(False)
            raise failure
        return copy2(source, destination, **kwargs)

    monkeypatch.setattr(checkpoint_store.shutil, "copy2", fail_copy)
    with pytest.raises(OSError) as caught:
        checkpoint_store.undo_checkpoint(checkpoint.checkpoint_id, str(root))
    assert caught.value is failure
    assert reached == [target]
    preserved = target if replaced == [True] else unrelated
    assert preserved.exists(), "rollback deleted an unrelated ordinary file"
    assert preserved.read_bytes() == b"independent owner bytes"


@pytest.mark.parametrize("removed", [False, True])
def test_replacement_during_rollback_read_is_not_journaled(tmp_path, monkeypatch, removed):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "value.py"
    target.write_bytes(b"snapshot")
    checkpoint = checkpoint_store.create_checkpoint(str(root))
    if removed:
        target = root / "extra.py"
    target.write_bytes(b"current")
    unrelated = tmp_path / "independent.py"
    unrelated.write_bytes(b"independent owner bytes")
    read = checkpoint_store._bytes_or_abort_undo
    reached = []

    def replace_after_read(path, checkpoint_id):
        data = read(path, checkpoint_id)
        if path == target:
            reached.append(path)
            os.replace(unrelated, target)
        return data

    monkeypatch.setattr(checkpoint_store, "_bytes_or_abort_undo", replace_after_read)
    with pytest.raises(OSError, match="identity changed"):
        checkpoint_store.undo_checkpoint(checkpoint.checkpoint_id, str(root))
    assert reached == [target]
    assert target.read_bytes() == b"independent owner bytes"


@pytest.mark.parametrize("swap_parent", [False, True])
def test_removed_file_rollback_preserves_changed_destination(tmp_path, monkeypatch, swap_parent):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "value.py"
    target.write_bytes(b"snapshot")
    checkpoint = checkpoint_store.create_checkpoint(str(root))
    extra = root / "extra/extra.py"
    extra.parent.mkdir()
    extra.write_bytes(b"removed bytes")
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / extra.name if swap_parent else extra
    if swap_parent:
        canary.write_bytes(b"independent owner bytes")
    copy2 = checkpoint_store.shutil.copy2
    failure = OSError("restore copy failure")
    reached = []

    def fail_copy(source, destination, **kwargs):
        if Path(destination) == target:
            reached.append(target)
            assert not extra.exists()
            if swap_parent:
                extra.parent.rename(tmp_path / "original-extra")
                _directory_alias(extra.parent, outside)
            else:
                extra.write_bytes(b"independent owner bytes")
            raise failure
        return copy2(source, destination, **kwargs)

    monkeypatch.setattr(checkpoint_store.shutil, "copy2", fail_copy)
    with pytest.raises(OSError) as caught:
        checkpoint_store.undo_checkpoint(checkpoint.checkpoint_id, str(root))
    assert caught.value is failure
    assert reached == [target]
    assert canary.read_bytes() == b"independent owner bytes"


def test_confined_claim_receipt_survives_copy2(tmp_path):
    from tensor_grep.cli._index_lock import atomic_write_bytes_confined

    source = tmp_path / "source.py"
    target = tmp_path / "target.py"
    source.write_bytes(b"snapshot")
    receipt = atomic_write_bytes_confined(tmp_path, target, b"", replace=False)
    claimed = target.lstat()
    assert receipt == (claimed.st_dev, claimed.st_ino)
    checkpoint_store.shutil.copy2(source, target, follow_symlinks=False)
    copied = target.lstat()
    assert receipt == (copied.st_dev, copied.st_ino)
    assert target.read_bytes() == b"snapshot"
    assert copied.st_mtime_ns == source.stat().st_mtime_ns
