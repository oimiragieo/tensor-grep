from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store, checkpoint_undo_io


def _project(tmp_path: Path, *, extra: bool = False):
    root = tmp_path / "project"
    root.mkdir()
    first, second, added = root / "a.txt", root / "b.txt", root / "extra.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    checkpoint_id = checkpoint_store.create_checkpoint(str(root)).checkpoint_id
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")
    if extra:
        added.write_bytes(b"working-extra\n")
    return root, checkpoint_id, first, second, added


def _fail_second_commit(monkeypatch: pytest.MonkeyPatch, second: Path) -> None:
    def fail(root, path, *args, _original=checkpoint_store.publish_restored_file, **kwargs):
        if Path(path) == second:
            raise OSError("controlled commit failure")
        return _original(root, path, *args, **kwargs)

    monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail)


def _assert_working(first: Path, second: Path, extra: Path | None = None) -> None:
    assert [path.read_bytes() for path in (first, second)] == [b"working-a\n", b"working-b\n"]
    if extra is not None:
        assert extra.read_bytes() == b"working-extra\n"


@pytest.mark.parametrize("after,new", [(False, False), (False, True), (True, False), (True, True)])
def test_tg011_rollback_interrupt_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: bool, new: bool
) -> None:
    root, checkpoint_id, first, second, extra = _project(tmp_path, extra=new)
    _fail_second_commit(monkeypatch, second)
    original = checkpoint_undo_io.publish_restored_file
    calls = 0
    message = "post-publication interrupt" if after else "rollback interrupt"

    def interrupt(root_arg, path, *args, **kwargs):
        nonlocal calls
        target = not new or Path(path) == extra
        if after:
            result = original(root_arg, path, *args, **kwargs)
            if target:
                calls += 1
                raise KeyboardInterrupt(message)
            return result
        if target:
            calls += 1
            if calls <= 2:
                raise KeyboardInterrupt(message)
        return original(root_arg, path, *args, **kwargs)

    monkeypatch.setattr(checkpoint_undo_io, "publish_restored_file", interrupt)
    with pytest.raises(KeyboardInterrupt, match=message):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))
    _assert_working(first, second, extra if new else None)


@pytest.mark.parametrize("rollback", [False, True])
def test_tg011_publication_window_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rollback: bool
) -> None:
    root, checkpoint_id, first, second, _ = _project(tmp_path)
    if rollback:
        _fail_second_commit(monkeypatch, second)
    original = checkpoint_undo_io.atomic_write_bytes_confined
    calls = 0
    message = f"{'rollback' if rollback else 'commit'} publication window"

    def interrupt(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1 + rollback:
            kwargs["after_publish"] = lambda: (_ for _ in ()).throw(KeyboardInterrupt(message))
        return original(*args, **kwargs)

    monkeypatch.setattr(checkpoint_undo_io, "atomic_write_bytes_confined", interrupt)
    with pytest.raises(KeyboardInterrupt, match=message):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))
    _assert_working(first, second)


def test_tg011_unlink_retry_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, checkpoint_id, first, second, _ = _project(tmp_path)
    first.unlink()
    _fail_second_commit(monkeypatch, second)

    def interrupt(*args, _original=checkpoint_undo_io.unlink_with_pinned_parent, **kwargs):
        _original(*args, **kwargs)
        raise KeyboardInterrupt("rollback unlink window")

    monkeypatch.setattr(checkpoint_undo_io, "unlink_with_pinned_parent", interrupt)
    with pytest.raises(KeyboardInterrupt, match="rollback unlink window"):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))
    assert (first.exists(), second.read_bytes()) == (False, b"working-b\n")


def test_tg011_phase_replay_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, checkpoint_id, first, second, extra = _project(tmp_path, extra=True)
    _fail_second_commit(monkeypatch, second)
    calls = 0

    def interrupt(*args, _original=checkpoint_store.rollback_overwrites, **kwargs):
        nonlocal calls
        result = _original(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt("rollback phase boundary")
        return result

    monkeypatch.setattr(checkpoint_store, "rollback_overwrites", interrupt)
    with pytest.raises(KeyboardInterrupt, match="rollback phase boundary"):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))
    _assert_working(first, second, extra)
