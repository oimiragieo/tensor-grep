"""Controls for the shared primitive, including refusal after a successful publication."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from tensor_grep.io import confined


def test_publication_overwrite_and_no_clobber(tmp_path: Path) -> None:
    path = tmp_path / "receipt"
    confined.publish_confined(tmp_path, path, b"first", only_if_missing=True)
    assert confined.read_confined(tmp_path, path, 32) == b"first"
    with pytest.raises(FileExistsError):
        confined.publish_confined(tmp_path, path, b"wrong", only_if_missing=True)
    assert path.read_bytes() == b"first"
    confined.publish_confined(tmp_path, path, b"second")
    assert path.read_bytes() == b"second"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_refuses_changed_original_identity(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "receipt"
    confined.publish_confined(tmp_path, path, b"original")
    identity = confined._file_identity
    observations = 0

    def changed(target: Path) -> tuple[int, int, int, int]:
        nonlocal observations
        actual = identity(target)
        observations += 1
        return actual if observations == 1 else (*actual[:3], actual[3] + 1)

    monkeypatch.setattr(confined, "_file_identity", changed)
    with pytest.raises(OSError, match="changed before publication"):
        confined.publish_confined(tmp_path, path, b"wrong")
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_no_clobber_refuses_a_racing_destination(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "receipt"
    if os.name == "nt":
        from tensor_grep.io import windows_publication

        rename = windows_publication.rename_open

        def raced(descriptor, destination, **kwargs):
            path.write_bytes(b"racing writer")
            return rename(descriptor, destination, **kwargs)

        monkeypatch.setattr(windows_publication, "rename_open", raced)
    else:
        link = os.link

        def raced(source, destination, **kwargs):
            path.write_bytes(b"racing writer")
            return link(source, destination, **kwargs)

        monkeypatch.setattr(confined.os, "link", raced)
    with pytest.raises(FileExistsError):
        confined.publish_confined(tmp_path, path, b"wrong")
    assert path.read_bytes() == b"racing writer"
    assert not list(tmp_path.glob(".*.tmp"))


def test_publication_parent_swap_cannot_publish_outside(tmp_path: Path, monkeypatch) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Probe symlink privilege before arranging the deterministic swap during fsync.
    probe = tmp_path / "probe"
    try:
        probe.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    probe.unlink()
    fsync = os.fsync
    attempted = False

    def swap(descriptor: int) -> None:
        nonlocal attempted
        fsync(descriptor)
        if not attempted:
            attempted = True
            parent.rename(tmp_path / "old-parent")
            parent.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(confined.os, "fsync", swap)
    with pytest.raises(OSError) as refusal:
        confined.publish_confined(tmp_path, parent / "receipt", b"wrong")
    # Windows denies the rename through held directory handles; POSIX detects the swap.
    assert attempted
    if os.name == "nt":
        assert refusal.value.winerror in (5, 32)
    else:
        assert "parent changed" in str(refusal.value)
    assert not list(outside.iterdir())
    assert not list(tmp_path.rglob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="actual Windows handle publication")
def test_windows_temporary_cannot_be_substituted_or_written_before_publication(
    tmp_path: Path, monkeypatch
) -> None:
    from tensor_grep.io import windows_publication

    path = tmp_path / "receipt"
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"unauthorized")
    rename = windows_publication.rename_open
    attempts = []

    def attack(descriptor: int, destination: Path, *, replace: bool) -> None:
        temporary = next(tmp_path.glob(".receipt.*.tmp"))
        for index, operation in enumerate([
            lambda: temporary.unlink(),
            lambda: os.replace(replacement, temporary),
            lambda: temporary.write_bytes(b"unauthorized"),
        ]):
            with pytest.raises(OSError) as refusal:
                operation()
            if index < 2:
                assert refusal.value.winerror in (5, 32)
            else:
                # CRT file opening reports the denied share mode as EACCES without winerror.
                assert (
                    isinstance(refusal.value, PermissionError)
                    and refusal.value.errno == errno.EACCES
                )
            attempts.append(refusal.value.errno)
        rename(descriptor, destination, replace=replace)

    monkeypatch.setattr(windows_publication, "rename_open", attack)
    confined.publish_confined(tmp_path, path, b"authorized")
    assert len(attempts) == 3
    assert path.read_bytes() == b"authorized"
    assert replacement.read_bytes() == b"unauthorized"
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="actual Windows handle publication")
def test_windows_failure_discards_only_the_held_temporary(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.io import windows_publication

    def fail(descriptor: int, destination: Path, *, replace: bool) -> None:
        temporary = next(tmp_path.glob(".receipt.*.tmp"))
        with pytest.raises(OSError) as refusal:
            temporary.unlink()
        assert refusal.value.winerror in (5, 32)
        raise OSError("injected rename refusal")

    monkeypatch.setattr(windows_publication, "rename_open", fail)
    with pytest.raises(OSError, match="injected rename refusal"):
        confined.publish_confined(tmp_path, tmp_path / "receipt", b"authorized")
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="actual Windows handle publication")
def test_windows_descriptor_exhaustion_cleans_up_the_raw_owned_handle(
    tmp_path: Path, monkeypatch
) -> None:
    import msvcrt

    from tensor_grep.io import windows_publication

    confined.publish_confined(tmp_path, tmp_path / "positive", b"authorized")

    def exhausted(*args):
        raise OSError(errno.EMFILE, "injected descriptor exhaustion")

    monkeypatch.setattr(msvcrt, "open_osfhandle", exhausted)
    with pytest.raises(OSError) as refusal:
        windows_publication.open_temporary(tmp_path / "failure.tmp")
    assert refusal.value.errno == errno.EMFILE
    assert [path.name for path in tmp_path.iterdir()] == ["positive"]
    assert (tmp_path / "positive").read_bytes() == b"authorized"


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX publication verification")
def test_posix_source_substitution_refuses_success_and_preserves_unknown_temp(
    tmp_path: Path, monkeypatch
) -> None:
    link = os.link
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"unauthorized")

    def attack(source, destination, **kwargs):
        # Substitute only after the source's descriptor has been written/fsynced.
        os.replace(replacement, tmp_path / source)
        return link(source, destination, **kwargs)

    monkeypatch.setattr(confined.os, "link", attack)
    with pytest.raises(OSError, match="temporary was substituted"):
        confined.publish_confined(tmp_path, tmp_path / "receipt", b"authorized")
    # Detection is postpublication, not source CAS: foreign bytes may already be visible.
    assert (tmp_path / "receipt").read_bytes() == b"unauthorized"
    unknown = list(tmp_path.glob(".receipt.*.tmp"))
    assert len(unknown) == 1 and unknown[0].read_bytes() == b"unauthorized"


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX publication verification")
def test_posix_same_inode_source_mutation_is_detected(tmp_path: Path, monkeypatch) -> None:
    link = os.link

    def attack(source, destination, **kwargs):
        (tmp_path / source).write_bytes(b"unauthorized")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(confined.os, "link", attack)
    with pytest.raises(OSError, match="publication bytes changed"):
        confined.publish_confined(tmp_path, tmp_path / "receipt", b"authorized")
    assert not list(tmp_path.glob(".*.tmp"))


def test_final_destination_swap_is_a_documented_residual_without_in_place_write(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "receipt"
    path.write_bytes(b"original")
    outside = tmp_path / "outside"
    outside.write_bytes(b"raced inode")
    attempts = []

    def swap_destination() -> None:
        path.unlink()
        path.hardlink_to(outside)
        attempts.append(True)

    if os.name == "nt":
        from tensor_grep.io import windows_publication

        rename = windows_publication.rename_open

        def raced(descriptor, destination, **kwargs):
            swap_destination()
            return rename(descriptor, destination, **kwargs)

        monkeypatch.setattr(windows_publication, "rename_open", raced)
    else:
        replace = os.replace

        def raced(source, destination, **kwargs):
            swap_destination()
            return replace(source, destination, **kwargs)

        monkeypatch.setattr(confined.os, "replace", raced)
    # Existing-target publication is reader-atomic replacement, not conditional inode CAS.
    confined.publish_confined(root, path, b"authorized")
    assert attempts == [True]
    assert path.read_bytes() == b"authorized"
    assert outside.read_bytes() == b"raced inode"


@pytest.mark.skipif(os.name == "nt", reason="actual POSIX directory descriptors")
@pytest.mark.parametrize("operation", ["publish", "prepare"])
def test_opened_parent_swap_and_restore_is_refused_before_writes(
    tmp_path: Path, monkeypatch, operation: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    confined.prepare_directory(root, root / "cache")
    confined.publish_confined(root, root / "positive", b"authorized")
    open_descriptor = os.open
    attempts = []

    def swapped(path, flags, *args, **kwargs):
        if Path(path) != root or kwargs.get("dir_fd") is not None:
            return open_descriptor(path, flags, *args, **kwargs)
        saved = tmp_path / "saved"
        root.rename(saved)
        outside.rename(root)
        try:
            descriptor = open_descriptor(path, flags, *args, **kwargs)
        finally:
            root.rename(outside)
            saved.rename(root)
        # Path-based validation sees exactly the original verified parents again.
        attempts.append(descriptor)
        return descriptor

    monkeypatch.setattr(confined.os, "open", swapped)
    with pytest.raises(OSError, match="opened parent identity differs"):
        if operation == "publish":
            confined.publish_confined(root, root / "receipt", b"wrong")
        else:
            confined.prepare_directory(root, root / "new-cache")
    assert len(attempts) == 1
    assert not list(outside.iterdir())
    assert not (root / "receipt").exists() and not (root / "new-cache").exists()
    assert (root / "positive").read_bytes() == b"authorized"
    with pytest.raises(OSError) as closed:
        os.fstat(attempts[0])
    assert closed.value.errno == errno.EBADF


def test_publication_refuses_leaf_and_parent_links(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"untouched")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    with pytest.raises(OSError, match="linked cache"):
        confined.publish_confined(tmp_path, link, b"wrong")
    outside = tmp_path / "outside"
    outside.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="link/junction parent"):
        confined.publish_confined(tmp_path, parent / "receipt", b"wrong")
    assert target.read_bytes() == b"untouched"
    assert not list(outside.iterdir())
