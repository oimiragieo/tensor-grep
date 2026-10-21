"""Regression coverage for lexical parent traversal in confined filesystem APIs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from tensor_grep.io import confined

Operation = Literal["read", "publish", "prefix", "prepare"]


def _exercise(operation: Operation, root: Path, path: Path) -> bytes | None:
    if operation == "read":
        return confined.read_confined(root, path, 64)
    if operation == "publish":
        confined.publish_confined(root, path, b"replacement")
        return None
    if operation == "prefix":
        confined.validate_confined_parent_prefix(root, path)
        return None
    confined.prepare_directory(root, path)
    return None


def _metadata_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[Path], Callable[[Path], object]]:
    observed: list[Path] = []
    real_lstat = Path.lstat

    def record(path: Path) -> object:
        observed.append(path)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", record)
    return observed, real_lstat


def _valid_state(path: Path) -> tuple[bool, bytes | None]:
    return path.is_dir(), path.read_bytes() if path.is_file() else None


def _entry_names(path: Path) -> list[str]:
    return sorted(entry.name for entry in path.iterdir())


def _traversal_target(root: Path, operation: Operation) -> Path:
    leaf = "created" if operation == "prepare" else "state.json"
    return root / ".." / "sibling" / leaf


def _invalid_root_relation(outside: Path, invalid_kind: str) -> Path:
    return outside if invalid_kind == "outside_absolute" else Path("outside.json")


def _valid_child_setup(root: Path, operation: Operation) -> Path:
    path = root / ("child" if operation == "prepare" else "state.json")
    if operation != "prepare":
        path.write_bytes(b"original")
    return path


def _inside_traversal_target(root: Path, operation: Operation) -> Path:
    leaf = "new-directory" if operation == "prepare" else "leaf"
    return root / "child" / ".." / leaf


def _root_with_style(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool) -> Path:
    absolute_root = tmp_path / "repo"
    absolute_root.mkdir()
    monkeypatch.chdir(tmp_path)
    return Path("repo") if relative else absolute_root


@pytest.mark.parametrize("operation", ["read", "publish", "prefix", "prepare"])
def test_ConfinedOperation_ParentTraversal_LeavesOwnedSiblingUntouched(
    tmp_path: Path, operation: Operation
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    leaf = sibling / "state.json"
    leaf.write_bytes(b"owned sibling")
    target = _traversal_target(root, operation)

    with pytest.raises(OSError, match="parent traversal"):
        _exercise(operation, root, target)

    assert leaf.read_bytes() == b"owned sibling"
    assert _entry_names(sibling) == ["state.json"]
    assert _entry_names(root) == []


@pytest.mark.parametrize("operation", ["read", "publish", "prefix", "prepare"])
@pytest.mark.parametrize("invalid_kind", ["outside_absolute", "mismatched_relative"])
def test_ConfinedOperation_InvalidRootRelation_RefusesBeforeMetadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
    invalid_kind: str,
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"unchanged")
    target = _invalid_root_relation(outside, invalid_kind)
    observed, _real_lstat = _metadata_spy(monkeypatch)

    with pytest.raises(ValueError):
        _exercise(operation, root, target)

    assert observed == []
    assert outside.read_bytes() == b"unchanged"


@pytest.mark.parametrize("operation", ["read", "publish", "prefix", "prepare"])
def test_ConfinedOperation_ParentTail_RefusesBeforeMetadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Operation
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    target = root / ".." / "trusted" / "leaf"
    observed, _real_lstat = _metadata_spy(monkeypatch)

    with pytest.raises(OSError, match="parent traversal"):
        _exercise(operation, root, target)

    assert observed == []
    assert _entry_names(root) == []


@pytest.mark.parametrize("operation", ["read", "publish", "prefix", "prepare"])
def test_ConfinedOperation_InsideReturningDotDot_RefusesWithoutMutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Operation
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    leaf = root / "leaf"
    leaf.write_bytes(b"original")
    target = _inside_traversal_target(root, operation)
    observed, _real_lstat = _metadata_spy(monkeypatch)

    with pytest.raises(OSError, match="parent traversal"):
        _exercise(operation, root, target)

    assert leaf.read_bytes() == b"original"
    assert _entry_names(root) == ["child", "leaf"]
    assert child.is_dir()
    assert observed == []


@pytest.mark.parametrize("operation", ["read", "publish", "prefix"])
def test_ConfinedFileOperation_RootEquality_RefusesBeforeMetadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: Operation
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    observed, _real_lstat = _metadata_spy(monkeypatch)

    with pytest.raises(OSError, match="must be below trusted root"):
        _exercise(operation, root, root)

    assert observed == []
    assert _entry_names(root) == []


@pytest.mark.parametrize(
    ("operation", "expected_result", "expected_directory", "expected_bytes"),
    [
        ("read", b"original", False, b"original"),
        ("publish", None, False, b"replacement"),
        ("prefix", None, False, b"original"),
        ("prepare", None, True, None),
    ],
)
@pytest.mark.parametrize("relative", [False, True])
def test_ConfinedOperation_ValidChild_PreservesAbsoluteAndRelativeBehavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Operation,
    relative: bool,
    expected_result: bytes | None,
    expected_directory: bool,
    expected_bytes: bytes | None,
) -> None:
    root = _root_with_style(tmp_path, monkeypatch, relative)
    path = _valid_child_setup(root, operation)

    result = _exercise(operation, root, path)

    assert result == expected_result
    assert path.exists()
    assert _valid_state(path) == (expected_directory, expected_bytes)


def test_PrepareDirectory_RootEquality_RemainsNoOp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "trusted"
    root.mkdir()
    observed, _real_lstat = _metadata_spy(monkeypatch)

    confined.prepare_directory(root, root)

    assert root.is_dir()
    assert _entry_names(root) == []
    assert observed == []


@pytest.mark.parametrize("operation", ["read", "publish", "prefix", "prepare"])
def test_ConfinedOperation_RegularFileRootWithChild_RefusesAndPreservesBytes(
    tmp_path: Path, operation: Operation
) -> None:
    root = tmp_path / "file-root"
    root.write_bytes(b"root bytes")
    target = root / "child"

    with pytest.raises(OSError, match="link/junction parent"):
        _exercise(operation, root, target)

    assert root.read_bytes() == b"root bytes"
    assert not target.exists()
