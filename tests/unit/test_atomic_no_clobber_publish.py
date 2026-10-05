"""A successful no-clobber publish must not leave a second link to the file (PR #1197 round 5).

``atomic_write_bytes_anchored(..., replace=False)`` publishes with ``os.link(tmp, path)`` (fails if the
destination exists). It used to leave the temp name behind on SUCCESS, so ``.<name>.<uuid>.tmp`` kept
a permanent second hard link to the (secret) content. The shared helper now unlinks the temp name
after a successful link, so every ``replace=False`` caller (secret writer, ``tg new`` scaffolds,
the scaffold command) ends with exactly one link and no temp file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli._index_lock import atomic_write_bytes_anchored


def _leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.rglob("*") if p.name.endswith(".tmp"))


def test_a_successful_no_clobber_publish_leaves_one_link_and_no_temp_file(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes_anchored(target, b"payload", mode=0o600, replace=False)
    assert target.read_bytes() == b"payload"
    assert target.stat().st_nlink == 1, "a second hard link to the published file remains"
    assert _leftovers(tmp_path) == []
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_a_refused_no_clobber_publish_keeps_the_original_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes_anchored(target, b"first", replace=False)
    with pytest.raises(FileExistsError):
        atomic_write_bytes_anchored(target, b"second", replace=False)
    assert target.read_bytes() == b"first"
    assert target.stat().st_nlink == 1
    assert _leftovers(tmp_path) == []


def test_the_replacing_publish_control_also_leaves_one_link(tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    atomic_write_bytes_anchored(target, b"one", replace=True)
    atomic_write_bytes_anchored(target, b"two", replace=True)
    assert target.read_bytes() == b"two"
    assert target.stat().st_nlink == 1
    assert _leftovers(tmp_path) == []


def test_the_project_scaffold_caller_leaves_no_second_links(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    from tensor_grep.cli.ast_scaffold import _write_ast_project_scaffold

    base = tmp_path / "proj"
    _write_ast_project_scaffold(base, "python")
    files = [p for p in base.rglob("*") if p.is_file()]
    assert len(files) == 3
    assert all(p.stat().st_nlink == 1 for p in files), [p.name for p in files]
    assert _leftovers(base) == []


def test_a_first_secret_creation_leaves_no_temp_copy_and_one_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    assert path.stat().st_nlink == 1, "the secret content has a second hard link"
    assert _leftovers(secret_dir) == []
    # nothing but the secret (the creation lock is released)
    assert sorted(p.name for p in secret_dir.iterdir() if not p.name.endswith(".lock")) == [
        path.name
    ]
