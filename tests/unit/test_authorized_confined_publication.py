"""Real filesystem controls for authorized, root-confined state publication."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock
from tensor_grep.io import confined


def _authorization(root: Path, path: Path, label: str) -> _index_lock.WriteAuthorization:
    parent_identity = _index_lock.dir_identity(path.parent) if path.parent.exists() else None
    identity = _index_lock.file_identity(path) if os.path.lexists(path) else None
    ancestor = (str(root), _index_lock.dir_identity(root)) if parent_identity is None else None
    return _index_lock.WriteAuthorization(str(path), identity, parent_identity, label, ancestor)


def _race_no_clobber_publish(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    if os.name == "nt":
        from tensor_grep.io import windows_publication

        rename = windows_publication.rename_open

        def raced(descriptor, destination, *, replace: bool) -> None:
            path.write_bytes(b"racing writer")
            rename(descriptor, destination, replace=replace)

        monkeypatch.setattr(windows_publication, "rename_open", raced)
    else:
        link = confined.os.link

        def raced(source, destination, **kwargs) -> None:
            path.write_bytes(b"racing writer")
            link(source, destination, **kwargs)

        monkeypatch.setattr(confined.os, "link", raced)


def test_AtomicConfinedWrite_UnlistedTarget_IsRefusedBeforeCreation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    authorized = root / "approved.json"
    denied = root / "unlisted.json"
    scope = _index_lock.write_authorizations([_authorization(root, authorized, "approved")])

    with scope:
        with pytest.raises(_index_lock.WriteAuthorizationError, match="not an authorized"):
            _index_lock.atomic_write_bytes_confined(root, denied, b"{}")

    assert not denied.exists()
    assert list(root.iterdir()) == []


def test_AtomicConfinedWrite_AnotherApprovedTargetChanged_PreflightCreatesNothing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    victim = root / "existing.json"
    victim.write_bytes(b"before")
    new_target = root / "new" / "state.json"
    scope = _index_lock.write_authorizations([
        _authorization(root, new_target, "new state"),
        _authorization(root, victim, "existing state"),
    ])
    victim.write_bytes(b"changed after approval")

    with scope:
        with pytest.raises(_index_lock.WriteAuthorizationError, match="changed after"):
            _index_lock.atomic_write_bytes_confined(root, new_target, b"{}")

    assert not new_target.parent.exists()
    assert victim.read_bytes() == b"changed after approval"


def test_AtomicConfinedWrite_ApprovedAbsentTargetRaces_ExistingBytesSurvive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "state.json"
    scope = _index_lock.write_authorizations([_authorization(root, target, "state")])
    _race_no_clobber_publish(monkeypatch, target)

    with scope:
        with pytest.raises(FileExistsError):
            _index_lock.atomic_write_bytes_confined(root, target, b"authorized")

    assert target.read_bytes() == b"racing writer"
    assert scope.written == []


def test_AtomicConfinedWrite_ApprovedExistingTargetChanges_RefusesReplacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "state.json"
    target.write_bytes(b"approved")
    scope = _index_lock.write_authorizations([_authorization(root, target, "state")])
    real_identity = confined._file_identity

    def changed_after_probe(path: Path) -> tuple[int, int, int, int]:
        identity = real_identity(path)
        if path == target:
            path.write_bytes(b"racing writer")
        return identity

    monkeypatch.setattr(confined, "_file_identity", changed_after_probe)

    with scope:
        with pytest.raises(OSError):
            _index_lock.atomic_write_bytes_confined(root, target, b"authorized")

    assert target.read_bytes() == b"racing writer"
    assert scope.written == []


def test_AtomicConfinedWrite_NormalAndInvalidationPublish_RecordAuthorizedLabels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cache = root / ".state" / "cache.json"
    invalidation = root / ".state" / "cache-invalidated.json"
    scope = _index_lock.write_authorizations([
        _authorization(root, cache, "cache write"),
        _authorization(root, invalidation, "cache invalidation"),
    ])

    with scope:
        _index_lock.atomic_write_bytes_confined(root, cache, b'{"state": true}')
        _index_lock.atomic_write_bytes_confined(root, invalidation, b"")

    assert cache.read_bytes() == b'{"state": true}'
    assert invalidation.read_bytes() == b""
    assert scope.written == ["cache write", "cache invalidation"]


def test_AtomicConfinedWrite_PostPublishVerificationFails_ReportsWrittenLabel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "cache.json"
    scope = _index_lock.write_authorizations([_authorization(root, target, "cache write")])
    verify_parents = confined._verify_parents

    def fail_after_publish(parents: list[tuple[Path, tuple[int, int]]]) -> None:
        verify_parents(parents)
        if target.exists():
            raise OSError("post-publication verification failure")

    monkeypatch.setattr(confined, "_verify_parents", fail_after_publish)

    with scope:
        with pytest.raises(OSError, match="post-publication verification failure") as refusal:
            _index_lock.atomic_write_bytes_confined(root, target, b"published")

    assert target.read_bytes() == b"published"
    assert scope.written == ["cache write"]
    assert "outputs already written: cache write" in scope.refusal_message(refusal.value)
