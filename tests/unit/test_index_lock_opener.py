from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock as il


@pytest.mark.parametrize("content", [b"", b"persistent lock\n"])
def test_read_only_opener_preserves_file_and_releases_contention(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / "lock"
    path.write_bytes(content)
    before = path.stat()
    registered = il._HELD_LOCK_FDS.copy()
    opened: list[int] = []
    verified: list[int] = []

    def open_fd() -> int:
        assert il._REGISTRY_LOCK.locked()
        fd = os.open(path, os.O_RDONLY)
        opened.append(fd)
        return fd

    def identity_matches(fd: int) -> bool:
        assert il._REGISTRY_LOCK.locked()
        assert fd not in il._HELD_LOCK_FDS
        verified.append(fd)
        return il._lock_identity_matches(fd, path)

    fd = il._try_os_file_lock_with_opener(open_fd, identity_matches)
    assert fd is not None
    try:
        assert il._HELD_LOCK_FDS == registered | {fd}
        assert il._try_os_file_lock_with_opener(open_fd, identity_matches) is None
        assert verified == [fd]  # Contention returns before identity verification.
        with pytest.raises(OSError) as caught:
            os.fstat(opened[-1])
        assert caught.value.errno == errno.EBADF
    finally:
        il.release_os_file_lock(fd)
    assert il._HELD_LOCK_FDS == registered
    fd = il._try_os_file_lock_with_opener(open_fd, identity_matches)
    assert fd is not None
    il.release_os_file_lock(fd)
    assert il._HELD_LOCK_FDS == registered
    assert list(tmp_path.iterdir()) == [path]
    after = path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    assert path.read_bytes() == content


def test_read_only_opener_does_not_create_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "missing"
    registered = il._HELD_LOCK_FDS.copy()
    with pytest.raises(FileNotFoundError):
        il._try_os_file_lock_with_opener(
            lambda: os.open(path, os.O_RDONLY),
            lambda _fd: pytest.fail("identity check ran after opening failed"),
        )
    assert list(tmp_path.iterdir()) == []
    assert il._HELD_LOCK_FDS == registered
    assert not il._REGISTRY_LOCK.locked()


@pytest.mark.parametrize("stage", ["open", "identity"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_callback_exception_releases_resources(
    tmp_path: Path, stage: str, error_type: type[BaseException]
) -> None:
    path = tmp_path / "lock"
    path.write_bytes(b"")
    registered = il._HELD_LOCK_FDS.copy()
    opened: list[int] = []
    error = error_type("callback failed")

    def open_fd() -> int:
        assert il._REGISTRY_LOCK.locked()
        if stage == "open":
            raise error
        fd = os.open(path, os.O_RDONLY)
        opened.append(fd)
        return fd

    def identity_matches(_fd: int) -> bool:
        assert stage == "identity"
        assert il._REGISTRY_LOCK.locked()
        raise error

    with pytest.raises(error_type) as caught:
        il._try_os_file_lock_with_opener(open_fd, identity_matches)
    assert caught.value is error
    assert il._HELD_LOCK_FDS == registered
    assert not il._REGISTRY_LOCK.locked()
    assert len(opened) == (stage == "identity")
    for fd in opened:
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
    fd = il.try_os_file_lock(path)
    assert fd is not None
    il.release_os_file_lock(fd)
    assert il._HELD_LOCK_FDS == registered
