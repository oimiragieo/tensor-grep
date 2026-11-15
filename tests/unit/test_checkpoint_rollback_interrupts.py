"""Rollback must restore working bytes before propagating catchable interrupts."""

from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store, checkpoint_undo_io


def test_tg011_second_interrupt_finishes_checkpoint_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    created = checkpoint_store.create_checkpoint(str(root))
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")

    commit_publish = checkpoint_store.publish_restored_file
    rollback_publish = checkpoint_undo_io.publish_restored_file
    commit_calls = 0
    rollback_calls = 0

    def fail_second_commit(*args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise OSError("controlled commit failure")
        return commit_publish(*args, **kwargs)

    def interrupt_first_rollback(*args, **kwargs):
        nonlocal rollback_calls
        rollback_calls += 1
        if rollback_calls <= 2:
            raise KeyboardInterrupt("controlled rollback interrupt")
        return rollback_publish(*args, **kwargs)

    monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail_second_commit)
    monkeypatch.setattr(checkpoint_undo_io, "publish_restored_file", interrupt_first_rollback)

    with pytest.raises(KeyboardInterrupt, match="controlled rollback interrupt") as caught:
        checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert isinstance(caught.value.__cause__, OSError)
    assert commit_calls == 2
    assert rollback_calls == 3
    assert first.read_bytes() == b"working-a\n"
    assert second.read_bytes() == b"working-b\n"


def test_tg011_interrupt_after_checkpoint_rollback_publication_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    created = checkpoint_store.create_checkpoint(str(root))
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")

    commit_publish = checkpoint_store.publish_restored_file
    rollback_publish = checkpoint_undo_io.publish_restored_file
    rollback_calls = 0

    def fail_second_commit(root_arg, path, *args, **kwargs):
        if Path(path) == second:
            raise OSError("controlled commit failure")
        return commit_publish(root_arg, path, *args, **kwargs)

    def interrupt_after_rollback_publication(*args, **kwargs):
        nonlocal rollback_calls
        rollback_publish(*args, **kwargs)
        rollback_calls += 1
        raise KeyboardInterrupt("controlled post-publication interrupt")

    monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail_second_commit)
    monkeypatch.setattr(
        checkpoint_undo_io, "publish_restored_file", interrupt_after_rollback_publication
    )

    with pytest.raises(KeyboardInterrupt, match="controlled post-publication interrupt"):
        checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert rollback_calls == 1
    assert first.read_bytes() == b"working-a\n"
    assert second.read_bytes() == b"working-b\n"


def test_tg011_second_interrupt_finishes_removed_file_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    created = checkpoint_store.create_checkpoint(str(root))
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")
    extra = root / "extra.txt"
    extra.write_bytes(b"working-extra\n")

    commit_publish = checkpoint_store.publish_restored_file
    rollback_publish = checkpoint_undo_io.publish_restored_file
    commit_calls = 0
    extra_rollback_calls = 0

    def fail_second_commit(*args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise OSError("controlled commit failure")
        return commit_publish(*args, **kwargs)

    def interrupt_extra_rollback(root_arg, path, *args, **kwargs):
        nonlocal extra_rollback_calls
        if Path(path) == extra:
            extra_rollback_calls += 1
            if extra_rollback_calls <= 2:
                raise KeyboardInterrupt("controlled removal rollback interrupt")
        return rollback_publish(root_arg, path, *args, **kwargs)

    monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail_second_commit)
    monkeypatch.setattr(checkpoint_undo_io, "publish_restored_file", interrupt_extra_rollback)

    with pytest.raises(KeyboardInterrupt, match="controlled removal rollback interrupt"):
        checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert extra_rollback_calls == 3
    assert first.read_bytes() == b"working-a\n"
    assert second.read_bytes() == b"working-b\n"
    assert extra.read_bytes() == b"working-extra\n"


def test_tg011_interrupt_after_removed_file_publication_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = root / "a.txt"
    second = root / "b.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    created = checkpoint_store.create_checkpoint(str(root))
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")
    extra = root / "extra.txt"
    extra.write_bytes(b"working-extra\n")

    commit_publish = checkpoint_store.publish_restored_file
    rollback_publish = checkpoint_undo_io.publish_restored_file
    rollback_calls = 0

    def fail_second_commit(root_arg, path, *args, **kwargs):
        if Path(path) == second:
            raise OSError("controlled commit failure")
        return commit_publish(root_arg, path, *args, **kwargs)

    def interrupt_after_extra_publication(root_arg, path, *args, **kwargs):
        nonlocal rollback_calls
        result = rollback_publish(root_arg, path, *args, **kwargs)
        if Path(path) == extra:
            rollback_calls += 1
            raise KeyboardInterrupt("controlled post-publication removal interrupt")
        return result

    monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail_second_commit)
    monkeypatch.setattr(
        checkpoint_undo_io, "publish_restored_file", interrupt_after_extra_publication
    )

    with pytest.raises(KeyboardInterrupt, match="controlled post-publication removal interrupt"):
        checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))

    assert rollback_calls == 1
    assert first.read_bytes() == b"working-a\n"
    assert second.read_bytes() == b"working-b\n"
    assert extra.read_bytes() == b"working-extra\n"
