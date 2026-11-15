import os

import pytest

from tensor_grep.cli import checkpoint_store, checkpoint_undo_io
from tensor_grep.io import confined


@pytest.mark.parametrize("case", ["repeat", "commit", "replace", "unlink", "phase", "cleanup"])
def test_tg011_interrupts(tmp_path, monkeypatch, case):
    if case == "cleanup" and os.name == "nt":
        pytest.skip("POSIX owned-temp cleanup")
    (root := tmp_path / "project").mkdir()
    first, second, extra = root / "a.txt", root / "b.txt", root / "extra.txt"
    first.write_bytes(b"snapshot-a\n")
    second.write_bytes(b"snapshot-b\n")
    checkpoint_id = checkpoint_store.create_checkpoint(str(root)).checkpoint_id
    first.write_bytes(b"working-a\n")
    second.write_bytes(b"working-b\n")
    has_extra = case in {"phase", "cleanup"}
    if has_extra:
        extra.write_bytes(b"working-extra\n")

    def fail_second(
        root_arg, path, *args, _commit=checkpoint_store.publish_restored_file, **kwargs
    ):
        if path == second:
            raise OSError("controlled commit failure")
        return _commit(root_arg, path, *args, **kwargs)

    if case != "commit":
        monkeypatch.setattr(checkpoint_store, "publish_restored_file", fail_second)
    calls = 0

    if case == "repeat":

        def interrupt(
            root_arg, path, *args, _publish=checkpoint_undo_io.publish_restored_file, **kwargs
        ):
            nonlocal calls
            if path == first:
                calls += 1
                if calls <= 2:
                    raise KeyboardInterrupt("rollback interrupt")
            return _publish(root_arg, path, *args, **kwargs)

        monkeypatch.setattr(checkpoint_undo_io, "publish_restored_file", interrupt)
    elif case in {"commit", "replace"}:

        def interrupt(*args, _atomic=checkpoint_undo_io.atomic_write_bytes_confined, **kwargs):
            nonlocal calls
            calls += 1
            if calls == (1 if case == "commit" else 2):
                published = kwargs["after_publish"]

                def after_publish():
                    published()
                    if case == "replace":
                        replacement = first.with_suffix(".replacement")
                        replacement.write_bytes(b"working-a\n")
                        os.replace(replacement, first)
                    raise KeyboardInterrupt("publication window")

                kwargs["after_publish"] = after_publish
            return _atomic(*args, **kwargs)

        monkeypatch.setattr(checkpoint_undo_io, "atomic_write_bytes_confined", interrupt)
    elif case == "unlink":
        first.unlink()

        def interrupt(*args, _unlink=checkpoint_undo_io.unlink_with_pinned_parent, **kwargs):
            _unlink(*args, **kwargs)
            checkpoint_undo_io.close_published_identity(args[3])
            first.write_bytes(b"unrelated\n")
            raise KeyboardInterrupt("unlink window")

        monkeypatch.setattr(checkpoint_undo_io, "unlink_with_pinned_parent", interrupt)
    elif case == "phase":

        def interrupt(*args, _rollback=checkpoint_store.rollback_overwrites, **kwargs):
            nonlocal calls
            result = _rollback(*args, **kwargs)
            calls += 1
            if calls == 1:
                raise KeyboardInterrupt("phase boundary")
            return result

        monkeypatch.setattr(checkpoint_store, "rollback_overwrites", interrupt)
    else:

        def interrupt(path, *args, _unlink=os.unlink, **kwargs):
            nonlocal calls
            if calls == 0 and str(path).endswith(".tmp"):
                calls += 1
                raise KeyboardInterrupt("owned-temp cleanup")
            return _unlink(path, *args, **kwargs)

        monkeypatch.setattr(confined.os, "unlink", interrupt)

    unsafe = checkpoint_store.CheckpointUndoUnsafeError
    expected = unsafe if case in {"replace", "unlink"} else KeyboardInterrupt
    with pytest.raises(expected):
        checkpoint_store.undo_checkpoint(checkpoint_id, str(root))
    assert first.read_bytes() == (b"unrelated\n" if case == "unlink" else b"working-a\n")
    assert not has_extra or extra.read_bytes() == b"working-extra\n"
    assert case != "cleanup" or not list(root.glob(".*.tmp"))
