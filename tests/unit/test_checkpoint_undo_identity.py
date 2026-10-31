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
