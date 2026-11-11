"""Checkpoint creation must not adopt a same-parent directory replacement."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store
from tensor_grep.io import checkpoint_directory
from tests.helpers.checkpoint_creation import requires_checkpoint_creation


@pytest.mark.skipif(os.name != "nt", reason="Windows path anchors")
def test_checkpoint_owner_supports_drive_and_unc_ancestor_chains() -> None:
    drive_chain = checkpoint_directory.CheckpointDirectoryOwner._ancestor_chain(
        Path("C:\\repo\\src")
    )
    unc_chain = checkpoint_directory.CheckpointDirectoryOwner._ancestor_chain(
        Path("\\\\server\\share\\repo\\src")
    )

    assert drive_chain[0] == Path("C:\\")
    assert unc_chain[0] == Path("\\\\server\\share\\")
    assert drive_chain[-1] == Path("C:\\repo\\src")
    assert unc_chain[-1] == Path("\\\\server\\share\\repo\\src")


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path normalization")
def test_checkpoint_owner_key_normalizes_extended_drive_and_unc_paths(tmp_path: Path) -> None:
    extended_drive = Path("\\\\?\\" + str(tmp_path.absolute()))
    extended_unc = Path("\\\\?\\UNC\\server\\share\\repo")

    assert checkpoint_directory.CheckpointDirectoryOwner._key(extended_drive) == (
        checkpoint_directory.CheckpointDirectoryOwner._key(tmp_path)
    )
    assert checkpoint_directory.CheckpointDirectoryOwner._key(extended_unc) == (
        checkpoint_directory.CheckpointDirectoryOwner._key(Path("\\\\server\\share\\repo"))
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle aliases")
@requires_checkpoint_creation
def test_checkpoint_owner_accepts_only_same_directory_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    owner = checkpoint_directory.CheckpointDirectoryOwner(root)
    original_key = checkpoint_directory.CheckpointDirectoryOwner._key
    alias = Path(str(root).upper())
    alias_spelling = str(alias)

    def alias_key(path: Path) -> str:
        key = original_key(path)
        return f"alias:{key}" if str(path) == alias_spelling else key

    monkeypatch.setattr(
        checkpoint_directory.CheckpointDirectoryOwner, "_key", staticmethod(alias_key)
    )
    try:
        owner.assert_pinned(alias)
        assert owner.identity(alias) == owner.identity(root)
        with pytest.raises(OSError, match="outside the pinned input path chain"):
            owner.assert_pinned(outside)
    finally:
        owner.close()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles deny rename while pinned")
def test_create_checkpoint_refuses_same_parent_replacement_before_identity_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"checkpoint payload\n")
    storage = root / ".tensor-grep" / "checkpoints"
    storage.mkdir(parents=True)
    replacement = storage / "replacement"
    replacement.mkdir()
    replacement_sentinel = replacement / "sentinel.bin"
    replacement_sentinel.write_bytes(b"replacement sentinel\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_sentinel = outside / "sentinel.bin"
    outside_sentinel.write_bytes(b"outside sentinel\n")

    requested, finished = threading.Event(), threading.Event()
    swapped: list[bool] = []
    checkpoint_leaf: list[Path] = []
    parked = storage / "parked-original"

    def replace_leaf() -> None:
        assert requested.wait(10), "checkpoint creation did not reach identity capture"
        try:
            checkpoint_leaf[0].rename(parked)
            replacement.rename(checkpoint_leaf[0])
            swapped.append(True)
        except OSError:
            swapped.append(False)
        finally:
            finished.set()

    worker = threading.Thread(target=replace_leaf)
    real_lstat = Path.lstat
    attempted = False

    def attempt_replacement(path: Path) -> None:
        nonlocal attempted
        if not attempted and path.parent == storage and path.name.startswith("ckpt-"):
            attempted = True
            checkpoint_leaf.append(path)
            requested.set()
            assert finished.wait(10), "same-parent replacement did not finish"
            if swapped == [False]:
                raise OSError("same-parent checkpoint replacement was blocked by ownership handle")

    def synchronized_lstat(path: Path) -> os.stat_result:
        result = real_lstat(path)
        attempt_replacement(path)
        return result

    original_verify = checkpoint_directory.CheckpointDirectoryOwner.verify_created_directory

    def synchronized_verify(
        owner: checkpoint_directory.CheckpointDirectoryOwner,
        path: Path,
        expected: tuple[int, int],
    ) -> None:
        attempt_replacement(path)
        original_verify(owner, path, expected)

    monkeypatch.setattr(Path, "lstat", synchronized_lstat)
    monkeypatch.setattr(
        checkpoint_directory.CheckpointDirectoryOwner,
        "verify_created_directory",
        synchronized_verify,
    )
    worker.start()
    try:
        with pytest.raises(OSError, match="same-parent checkpoint replacement"):
            checkpoint_store.create_checkpoint(str(root))
    finally:
        worker.join(10)

    assert not worker.is_alive()
    assert swapped == [False], "the opened checkpoint directory must not be replaceable"
    assert replacement_sentinel.read_bytes() == b"replacement sentinel\n"
    assert outside_sentinel.read_bytes() == b"outside sentinel\n"
    assert not (replacement / "metadata.json").exists()
    assert not (replacement / "snapshot").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles deny rename while pinned")
def test_checkpoint_create_pins_nested_source_parent_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    nested = root / "pkg"
    nested.mkdir(parents=True)
    (nested / "secret.py").write_bytes(b"in-repo source\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.py"
    secret_bytes = b"outside secret\n"
    secret.write_bytes(secret_bytes)
    sentinel = outside / "sentinel.bin"
    sentinel_bytes = b"outside sentinel\n"
    sentinel.write_bytes(sentinel_bytes)
    parked = root / "parked-pkg"
    requested, finished = threading.Event(), threading.Event()
    swapped: list[bool] = []
    blocked_errors: list[OSError] = []
    swap_errors: list[BaseException] = []

    def replace_parent() -> None:
        try:
            assert requested.wait(10), "checkpoint copy did not reach the source-parent swap point"
            try:
                nested.rename(parked)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 32:
                    blocked_errors.append(exc)
                    swapped.append(False)
                    return
                raise
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(nested), str(outside)],
                capture_output=True,
                timeout=10,
            )
            if result.returncode:
                raise AssertionError("junction replacement control failed")
            swapped.append(True)
        except BaseException as exc:
            swap_errors.append(exc)
            if parked.exists() and not nested.exists():
                try:
                    parked.rename(nested)
                except OSError as restore_error:
                    swap_errors.append(restore_error)
        finally:
            finished.set()

    original_open = checkpoint_directory.CheckpointDirectoryOwner._open_windows_source

    def synchronized_open(
        owner: checkpoint_directory.CheckpointDirectoryOwner,
        source: Path,
        initial: os.stat_result,
        rel_path: str,
        *,
        symlink: bool,
    ) -> int:
        if rel_path == "pkg/secret.py":
            requested.set()
            assert finished.wait(10), "source-parent replacement did not finish"
            if swap_errors:
                raise OSError("source-parent replacement control failed") from swap_errors[0]
            if blocked_errors:
                raise OSError("source-parent replacement was blocked by ownership handle") from (
                    blocked_errors[0]
                )
        return original_open(owner, source, initial, rel_path, symlink=symlink)

    monkeypatch.setattr(
        checkpoint_directory.CheckpointDirectoryOwner, "_open_windows_source", synchronized_open
    )
    worker = threading.Thread(target=replace_parent)
    worker.start()
    create_error: OSError | None = None
    try:
        checkpoint_store.create_checkpoint(str(root))
    except OSError as exc:
        create_error = exc
    finally:
        worker.join(10)

    assert not worker.is_alive()
    assert not swap_errors, f"junction replacement control failed: {swap_errors}"
    assert sentinel.read_bytes() == sentinel_bytes
    assert secret.read_bytes() == secret_bytes
    storage = root / ".tensor-grep" / "checkpoints"
    copied_secret = list(storage.glob("ckpt-*/snapshot/pkg/secret.py"))
    if copied_secret:
        assert copied_secret[0].read_bytes() == secret_bytes
    assert not copied_secret, "outside secret was copied through the replaced source parent"
    assert swapped == [False], "the nested source parent must remain pinned through copy"
    assert len(blocked_errors) == 1 and blocked_errors[0].winerror == 32
    assert create_error is not None and "source-parent replacement was blocked" in str(create_error)


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="Windows source leaf identity")
def test_checkpoint_rejects_leaf_symlink_swap_before_opening_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tracked.py"
    source.write_bytes(b"original in-repo source\n")
    outside = tmp_path / "outside-secret.py"
    outside_bytes = b"outside secret must never be copied\n"
    outside.write_bytes(outside_bytes)
    parked = root / "tracked.original.py"
    requested, finished = threading.Event(), threading.Event()
    swap_errors: list[BaseException] = []

    def replace_leaf() -> None:
        try:
            assert requested.wait(10), "checkpoint copy did not reach the leaf-open boundary"
            source.rename(parked)
            source.symlink_to(outside)
        except BaseException as exc:
            swap_errors.append(exc)
        finally:
            finished.set()

    original_open = checkpoint_directory.CheckpointDirectoryOwner._open_windows_source

    def synchronized_open(
        owner: checkpoint_directory.CheckpointDirectoryOwner,
        path: Path,
        initial: os.stat_result,
        relative_path: str,
        *,
        symlink: bool,
    ) -> int:
        if relative_path == "tracked.py":
            requested.set()
            assert finished.wait(10), "source leaf replacement did not finish"
            if swap_errors:
                raise OSError("source leaf replacement failed") from swap_errors[0]
        return original_open(owner, path, initial, relative_path, symlink=symlink)

    monkeypatch.setattr(
        checkpoint_directory.CheckpointDirectoryOwner, "_open_windows_source", synchronized_open
    )
    worker = threading.Thread(target=replace_leaf)
    worker.start()
    try:
        with pytest.raises(OSError):
            checkpoint_store.create_checkpoint(str(root))
    finally:
        worker.join(10)
        if source.is_symlink():
            source.unlink()
        if parked.exists():
            parked.rename(source)

    assert not worker.is_alive()
    assert not swap_errors, f"source leaf replacement failed: {swap_errors}"
    assert outside.read_bytes() == outside_bytes
    storage = root / ".tensor-grep" / "checkpoints"
    assert not list(storage.glob("ckpt-*/snapshot/tracked.py"))


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles deny rename while pinned")
def test_checkpoint_owner_pins_root_ancestors_before_source_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    root = parent / "repo"
    root.mkdir(parents=True)
    (root / "tracked.py").write_bytes(b"original root\n")
    replacement_parent = tmp_path / "replacement-parent"
    replacement_root = replacement_parent / "repo"
    replacement_root.mkdir(parents=True)
    sentinel = replacement_root / "sentinel.bin"
    sentinel.write_bytes(b"replacement root sentinel\n")
    parked_parent = tmp_path / "parked-parent"
    requested, finished = threading.Event(), threading.Event()
    swapped: list[bool] = []

    def replace_ancestor() -> None:
        assert requested.wait(10), "source enumeration did not reach the synchronized swap point"
        try:
            parent.rename(parked_parent)
            replacement_parent.rename(parent)
            swapped.append(True)
        except OSError:
            swapped.append(False)
        finally:
            finished.set()

    original_snapshot_entries = checkpoint_store._snapshot_entries

    def synchronized_snapshot_entries(scope):
        requested.set()
        assert finished.wait(10), "root ancestor replacement did not finish"
        if swapped == [False]:
            raise OSError("root ancestor replacement was blocked by ownership handles")
        return original_snapshot_entries(scope)

    monkeypatch.setattr(checkpoint_store, "_snapshot_entries", synchronized_snapshot_entries)
    worker = threading.Thread(target=replace_ancestor)
    worker.start()
    try:
        with pytest.raises(OSError, match="root ancestor replacement"):
            checkpoint_store.create_checkpoint(str(root))
    finally:
        worker.join(10)

    assert not worker.is_alive()
    assert swapped == [False]
    assert sentinel.read_bytes() == b"replacement root sentinel\n"
    assert replacement_parent.is_dir()
    assert not (replacement_root / ".tensor-grep").exists()
    assert not (root / ".tensor-grep").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles deny rename while pinned")
def test_checkpoint_owner_pins_root_ancestors_during_scope_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    root = parent / "repo"
    root.mkdir(parents=True)
    (root / "tracked.py").write_bytes(b"original root\n")
    replacement_parent = tmp_path / "replacement-parent"
    replacement_root = replacement_parent / "repo"
    replacement_root.mkdir(parents=True)
    sentinel = replacement_root / "sentinel.bin"
    sentinel.write_bytes(b"replacement root sentinel\n")
    parked_parent = tmp_path / "parked-parent"
    requested, finished = threading.Event(), threading.Event()
    swapped: list[bool] = []

    def replace_ancestor() -> None:
        assert requested.wait(10), "scope discovery did not reach the synchronized swap point"
        try:
            parent.rename(parked_parent)
            replacement_parent.rename(parent)
            swapped.append(True)
        except OSError:
            swapped.append(False)
        finally:
            finished.set()

    original_detect_scope = checkpoint_store._detect_checkpoint_scope

    def synchronized_detect_scope(path: Path):
        scope = original_detect_scope(path)
        requested.set()
        assert finished.wait(10), "root ancestor replacement did not finish"
        if swapped == [False]:
            raise OSError("root ancestor replacement was blocked during scope discovery")
        return scope

    monkeypatch.setattr(checkpoint_store, "_detect_checkpoint_scope", synchronized_detect_scope)
    worker = threading.Thread(target=replace_ancestor)
    worker.start()
    try:
        with pytest.raises(OSError, match="blocked during scope discovery"):
            checkpoint_store.create_checkpoint(str(root))
    finally:
        worker.join(10)

    assert not worker.is_alive()
    assert swapped == [False]
    assert sentinel.read_bytes() == b"replacement root sentinel\n"
    assert replacement_parent.is_dir()
    assert not (replacement_root / ".tensor-grep").exists()
    assert not (root / ".tensor-grep").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
def test_checkpoint_create_refuses_scope_outside_pinned_input_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"payload\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_scope = checkpoint_store._CheckpointScope(
        root=outside,
        mode="filesystem-snapshot",
        original_path=root,
    )
    monkeypatch.setattr(checkpoint_store, "_detect_checkpoint_scope", lambda _path: outside_scope)

    with pytest.raises(OSError, match="outside the pinned input path chain"):
        checkpoint_store.create_checkpoint(str(root))

    assert not (root / ".tensor-grep").exists()
    assert not (outside / ".tensor-grep").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX capability refusal")
def test_checkpoint_creation_refuses_without_posix_at_support_before_state_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"payload\n")
    monkeypatch.setattr(checkpoint_directory.os, "supports_dir_fd", set())

    with pytest.raises(OSError, match="POSIX \\*at operations"):
        checkpoint_store.create_checkpoint(str(root))

    assert not (root / ".tensor-grep").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-descriptor replacement control")
@requires_checkpoint_creation
def test_checkpoint_snapshot_uses_pinned_source_after_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    nested = root / "pkg"
    nested.mkdir(parents=True)
    source = nested / "secret.py"
    source.write_bytes(b"original in-repo source\n")
    source.chmod(0o640)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_secret = outside / "secret.py"
    outside_secret.write_bytes(b"outside secret\n")
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside sentinel\n")
    parked = root / "parked-pkg"
    requested, swapped = threading.Event(), threading.Event()
    swap_errors: list[BaseException] = []
    synced_directories: list[int] = []
    original_fsync = os.fsync

    def observe_directory_sync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            synced_directories.append(descriptor)
        original_fsync(descriptor)

    monkeypatch.setattr(checkpoint_directory.os, "fsync", observe_directory_sync)
    original_pin = checkpoint_directory.CheckpointDirectoryOwner.pin_source_parent

    def replace_parent() -> None:
        try:
            assert requested.wait(10), "checkpoint did not pin the source parent"
            nested.rename(parked)
            nested.symlink_to(outside, target_is_directory=True)
        except BaseException as exc:
            swap_errors.append(exc)
        finally:
            swapped.set()

    def synchronized_pin(
        owner: checkpoint_directory.CheckpointDirectoryOwner, root_path: Path, relative: str
    ) -> None:
        original_pin(owner, root_path, relative)
        if relative == "pkg/secret.py":
            requested.set()
            assert swapped.wait(10), "source-parent replacement did not finish"

    monkeypatch.setattr(
        checkpoint_directory.CheckpointDirectoryOwner, "pin_source_parent", synchronized_pin
    )
    worker = threading.Thread(target=replace_parent)
    worker.start()
    try:
        created = checkpoint_store.create_checkpoint(str(root))
    finally:
        worker.join(10)

    assert not worker.is_alive()
    assert not swap_errors, f"source-parent replacement failed: {swap_errors}"
    copied = checkpoint_store._snapshot_path(root, created.checkpoint_id) / "pkg" / "secret.py"
    assert copied.read_bytes() == b"original in-repo source\n"
    assert copied.stat().st_mode & 0o777 == 0o640
    assert synced_directories, "checkpoint publication must sync directory entries where supported"
    assert outside_secret.read_bytes() == b"outside secret\n"
    assert sentinel.read_bytes() == b"outside sentinel\n"


@pytest.mark.skipif(os.name != "nt", reason="CreateDirectory2W is a Windows API")
def test_missing_createdirectory2_refuses_before_state_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"payload\n")
    monkeypatch.setattr(checkpoint_directory, "_create_directory2", lambda _kernel=None: None)

    with pytest.raises(OSError, match="CreateDirectory2W"):
        checkpoint_store.create_checkpoint(str(root))

    assert not (root / ".tensor-grep").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="junction creation requires Windows cmd")
def test_checkpoint_create_refuses_storage_junction_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"payload\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside sentinel\n")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / ".tensor-grep"), str(outside)],
        capture_output=True,
    )
    if result.returncode:
        pytest.skip("junction creation is unavailable in this environment")

    with pytest.raises(OSError, match="reparse point"):
        checkpoint_store.create_checkpoint(str(root))

    assert sentinel.read_bytes() == b"outside sentinel\n"
    assert not (outside / "checkpoints").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
@pytest.mark.skipif(os.name != "nt", reason="junction creation requires Windows cmd")
def test_checkpoint_create_refuses_root_junction_after_path_confinement(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.py").write_bytes(b"payload\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.bin"
    sentinel.write_bytes(b"outside sentinel\n")

    # MCP confinement returns a resolved absolute path. Simulate a directory swap between that
    # check and checkpoint acquisition: resolving again before owner pinning would follow this
    # junction and incorrectly authorize writes at its destination.
    confined_path = root.resolve()
    parked_root = tmp_path / "repo-parked"
    root.rename(parked_root)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root), str(outside)], capture_output=True
    )
    if result.returncode:
        parked_root.rename(root)
        pytest.skip("junction creation is unavailable in this environment")

    try:
        with pytest.raises(OSError, match="reparse point"):
            checkpoint_store.create_checkpoint(str(confined_path))
    finally:
        root.rmdir()
        parked_root.rename(root)

    assert sentinel.read_bytes() == b"outside sentinel\n"
    assert not (outside / ".tensor-grep").exists()


@pytest.mark.checkpoint_creation
@requires_checkpoint_creation
def test_checkpoint_create_list_read_and_undo_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    tracked = root / "tracked.py"
    tracked.write_bytes(b"before\n")

    created = checkpoint_store.create_checkpoint(str(root))
    assert created.checkpoint_id in {
        record.checkpoint_id for record in checkpoint_store.list_checkpoints(str(root))
    }
    metadata = checkpoint_store.load_checkpoint_metadata(created.checkpoint_id, str(root))
    assert metadata["checkpoint_id"] == created.checkpoint_id
    assert (
        checkpoint_store
        ._snapshot_path(
            root,
            created.checkpoint_id,
        )
        .joinpath("tracked.py")
        .read_bytes()
        == b"before\n"
    )

    tracked.write_bytes(b"after\n")
    checkpoint_store.undo_checkpoint(created.checkpoint_id, str(root))
    assert tracked.read_bytes() == b"before\n"
