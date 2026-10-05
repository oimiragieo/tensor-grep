"""B-03: a nonexistent PATH must fail, never silently re-anchor to cwd / an ancestor."""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import checkpoint_store, ledger_store, session_store
from tensor_grep.cli.main import _daemon_directory_path


def _stray_state(root: Path) -> list[Path]:
    return sorted(root.rglob(".tensor-grep"))


def test_open_session_nonexistent_path_raises_and_writes_nothing(tmp_path: Path) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    # CONTROL: the same call on the existing directory opens a session (and writes state).
    session_store.open_session(str(anchor))
    assert _stray_state(anchor), "control: an existing path must create .tensor-grep"

    with pytest.raises(FileNotFoundError, match="Path not found"):
        session_store.open_session(str(anchor / "nodir"))
    assert not (anchor / "nodir").exists()


def test_open_session_still_accepts_a_file_path(tmp_path: Path) -> None:
    # CONTROL: a FILE path legitimately scans its parent directory; the guard must not refuse it.
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    target = anchor / "a.py"
    target.write_text("def a():\n    return 1\n", encoding="utf-8")
    assert session_store.open_session(str(target)).file_count == 1


def test_create_checkpoint_nonexistent_directory_raises_and_snapshots_nothing(
    tmp_path: Path,
) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    (anchor / "a.txt").write_text("hi\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Path not found"):
        checkpoint_store.create_checkpoint(str(anchor / "nodir"))
    assert not (anchor / ".tensor-grep").exists()


def test_create_checkpoint_missing_parent_of_a_new_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Path not found"):
        checkpoint_store.create_checkpoint(str(tmp_path / "nodir" / "new.txt"))


def test_create_checkpoint_of_a_not_yet_created_file_is_still_allowed(tmp_path: Path) -> None:
    # CONTROL / preserved contract: `_detect_checkpoint_scope` deliberately scopes a suffixed
    # nonexistent path as a file so a to-be-created file can be undone.
    result = checkpoint_store.create_checkpoint(str(tmp_path / "new.txt"))
    assert result.checkpoint_id.startswith("ckpt-")


def test_ledger_claim_nonexistent_path_fails_closed_and_writes_nothing(tmp_path: Path) -> None:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    with pytest.raises(ledger_store.LedgerError, match="Path not found"):
        ledger_store.submit_claim(str(anchor / "nodir"), symbols=["x"])
    assert not (anchor / ".tensor-grep").exists()
    assert not (tmp_path / ".tensor-grep").exists()
    # CONTROL: the same claim on the existing directory succeeds.
    ledger_store.submit_claim(str(anchor), symbols=["x"])
    assert (anchor / ".tensor-grep").exists()


def test_daemon_directory_path_is_none_for_a_missing_path(tmp_path: Path) -> None:
    existing = tmp_path / "d"
    existing.mkdir()
    a_file = tmp_path / "f.py"
    a_file.write_text("x = 1\n", encoding="utf-8")
    # CONTROL: an existing directory still routes to the daemon; a file still does not.
    assert _daemon_directory_path(str(existing)) == str(existing.resolve())
    assert _daemon_directory_path(str(a_file)) is None
    assert _daemon_directory_path(str(tmp_path / "nodir")) is None
