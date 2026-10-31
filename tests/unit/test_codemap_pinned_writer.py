"""Ordinary publication, callback, and borrowed-descriptor ownership controls."""

import os
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock as locks
from tensor_grep.cli.codemap_pinned import PinnedDirectory
from tensor_grep.io import confined


def _authorization(root: Path, path: Path) -> locks.WriteAuthorization:
    return locks.WriteAuthorization(
        str(path),
        locks.file_identity(path) if path.exists() else None,
        locks.dir_identity(path.parent) if path.parent.exists() else None,
        path.name,
        (str(root), locks.dir_identity(root)) if not path.parent.exists() else None,
    )


def test_pinned_write_round_trip_validates_each_publication_stage(tmp_path: Path) -> None:
    target = tmp_path / "page.md"
    observed: list[bytes | None] = []

    def validate() -> None:
        observed.append(target.read_bytes() if target.exists() else None)

    with PinnedDirectory(tmp_path) as pin:
        pin.write(target.name, b"first", validate=validate)
        pin.write(target.name, b"second", validate=validate)

    assert target.read_bytes() == b"second"
    assert observed == [None, None, b"first", b"first", b"first", b"second"]
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_pinned_write_refusal_preserves_cleanup_and_written_labels(
    tmp_path: Path, fail_at: int
) -> None:
    target = tmp_path / "page.md"
    target.write_bytes(b"before")
    scope = locks.write_authorizations([_authorization(tmp_path, target)])
    calls = 0

    def validate() -> None:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("injected validation refusal")

    with scope, PinnedDirectory(tmp_path) as pin:
        with pytest.raises(OSError, match="injected validation refusal"):
            pin.write(target.name, b"after", validate=validate)

    assert calls == fail_at
    assert target.read_bytes() == (b"after" if fail_at == 3 else b"before")
    assert scope.written == ([target.name] if fail_at == 3 else [])
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name != "posix", reason="POSIX borrowed directory descriptors")
@pytest.mark.parametrize("wrapper", [False, True])
def test_borrowed_parent_create_replace_and_no_clobber_without_reopening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrapper: bool
) -> None:
    target = tmp_path / "page.md"
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    real_open = os.open

    def open_child(path, flags, *args, **kwargs):
        assert kwargs.get("dir_fd") is not None, "borrowed parent was reopened"
        return real_open(path, flags, *args, **kwargs)

    def no_creation(*args, **kwargs):
        pytest.fail("borrowed mode attempted parent creation")

    def publish(data: bytes, *, replace: bool = True) -> None:
        if wrapper:
            locks.atomic_write_bytes_confined(
                tmp_path, target, data, parent_fd=parent_fd, replace=replace
            )
        else:
            confined.publish_confined(
                tmp_path, target, data, parent_fd=parent_fd, only_if_missing=not replace
            )

    try:
        monkeypatch.setattr(os, "open", open_child)
        monkeypatch.setattr(confined, "prepare_directory", no_creation)
        monkeypatch.setattr(locks, "_prepare_authorized_parent", no_creation)
        publish(b"first", replace=False)
        with pytest.raises(FileExistsError):
            publish(b"wrong", replace=False)
        assert target.read_bytes() == b"first"
        publish(b"second")
        assert target.read_bytes() == b"second"
        assert os.path.samestat(os.fstat(parent_fd), tmp_path.stat())
        assert list(tmp_path.iterdir()) == [target]
    finally:
        os.close(parent_fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX borrowed directory descriptors")
@pytest.mark.parametrize("fail_before_temp", [False, True])
def test_borrowed_parent_failure_closes_only_owned_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_before_temp: bool
) -> None:
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    owned: list[int] = []
    real_dup = os.dup

    def duplicate(fd: int) -> int:
        copied = real_dup(fd)
        owned.append(copied)
        return copied

    def refuse(*args: object) -> None:
        raise OSError("injected publication refusal")

    try:
        monkeypatch.setattr(os, "dup", duplicate)
        if fail_before_temp:
            monkeypatch.setattr(confined, "_verify_open_directory", refuse)
        with pytest.raises(OSError, match="injected publication refusal"):
            confined.publish_confined(
                tmp_path,
                tmp_path / "page.md",
                b"data",
                parent_fd=parent_fd,
                before_publish=refuse,
            )
        assert list(tmp_path.iterdir()) == []
        assert len(owned) == 1
        with pytest.raises(OSError):
            os.fstat(owned[0])
        assert os.path.samestat(os.fstat(parent_fd), tmp_path.stat())
    finally:
        os.close(parent_fd)


def test_pinned_write_reuses_parent_created_by_active_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = (tmp_path / "output" / name for name in ("first.md", "second.md"))
    scope = locks.write_authorizations([
        _authorization(tmp_path, first),
        _authorization(tmp_path, second),
    ])

    def no_creation(*args, **kwargs):
        pytest.fail("borrowed authorized write attempted parent creation")

    with scope:
        locks.atomic_write_bytes_confined(tmp_path, first, b"first")
        if os.name == "posix":
            monkeypatch.setattr(locks, "_create_missing_parents", no_creation)
        with PinnedDirectory(first.parent) as pin:
            pin.write(second.name, b"second", validate=lambda: None)
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert scope.written == [first.name, second.name]


@pytest.mark.skipif(os.name != "posix", reason="POSIX borrowed directory descriptors")
def test_borrowed_write_does_not_create_missing_authorized_parent(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "page.md"
    scope = locks.write_authorizations([_authorization(tmp_path, target)])
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with (
            scope,
            pytest.raises(locks.WriteAuthorizationError, match="borrowed parent is missing"),
        ):
            locks.atomic_write_bytes_confined(tmp_path, target, b"data", parent_fd=parent_fd)
        assert list(tmp_path.iterdir()) == []
        assert scope.written == []
        assert os.path.samestat(os.fstat(parent_fd), tmp_path.stat())
    finally:
        os.close(parent_fd)


@pytest.mark.parametrize("name", ["", ".", "nested/page.md"])
def test_pinned_write_requires_one_filename(tmp_path: Path, name: str) -> None:
    with PinnedDirectory(tmp_path) as pin, pytest.raises(OSError, match="invalid output filename"):
        pin.write(name, b"data", validate=lambda: pytest.fail("invalid name reached validation"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX rejection")
@pytest.mark.parametrize("publish", [confined.publish_confined, locks.atomic_write_bytes_confined])
def test_borrowed_descriptor_is_rejected_on_unsupported_platform(tmp_path: Path, publish) -> None:
    with pytest.raises(OSError, match=r"borrowed.*POSIX"):
        publish(tmp_path, tmp_path / "page.md", b"data", parent_fd=-1)
    assert list(tmp_path.iterdir()) == []


def test_default_wrapper_preserves_fixed_signature_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received = []

    def publish(root, path, data, *, only_if_missing, before_publish, on_published):
        received.append((root, path, data, only_if_missing, before_publish, on_published))

    monkeypatch.setattr(confined, "publish_confined", publish)
    locks.atomic_write_bytes_confined(tmp_path, tmp_path / "page.md", b"data")
    assert received == [(tmp_path, tmp_path / "page.md", b"data", False, None, None)]
