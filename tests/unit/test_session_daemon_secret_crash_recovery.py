"""Secret temp links left by a crash are recovered (PR #1197 round 6).

The writer publishes with ``os.link(tmp, secret)`` and then ``tmp.unlink()``. A writer killed in between
leaves BOTH names pointing at the secret (``st_nlink == 2``) forever, and a writer killed before the link
leaves an unpublished temp holding a would-be secret. Under the creation lock, on every create -- and on
a load that notices ``st_nlink > 1`` -- orphan temps matching EXACTLY ``.daemon-secret.json.<32 hex>.tmp``
are unlinked, but only if they are regular files (never symlinks / directories), owned by the current
user. Nothing outside that name pattern is touched and links are never followed.

(The shared ``atomic_write_bytes_anchored(replace=False)`` helper has the same crash window between
link and unlink; its other callers write non-secret scaffolding, so only the window is documented there.)
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_SRC = str(Path(trust.__file__).resolve().parents[2])

_CRASHING_WRITER = textwrap.dedent(
    """
    import os, sys
    from tensor_grep.cli import _index_lock, session_daemon_trust as trust

    def _link_then_die(src, dst):
        os.link(str(src), str(dst))   # the secret is now published under BOTH names ...
        os._exit(7)                    # ... and the writer is killed before the temp is unlinked

    _index_lock._publish_bytes_no_clobber = _link_then_die   # POSIX writer (shared helper)
    trust._publish_bytes_no_clobber = _link_then_die         # Windows writer
    trust._load_or_create_user_secret()
    os._exit(0)
    """
)


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def _fast_stale_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """The killed writer also left its creation lock behind; reclaim it quickly (it is dead)."""
    real = trust.index_lock
    monkeypatch.setattr(trust, "index_lock", lambda p, **kw: real(p, stale_after_s=0.3, **kw))


def _names(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_a_writer_killed_right_after_the_link_is_recovered_by_the_next_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    done = subprocess.run(
        [sys.executable, "-c", _CRASHING_WRITER],
        env={**os.environ, "PYTHONPATH": _SRC},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 7, (done.returncode, done.stdout, done.stderr)
    path = trust._daemon_secret_path()
    temps = [n for n in _names(secret_dir) if n.endswith(".tmp")]
    assert path.exists() and len(temps) == 1, _names(secret_dir)
    assert path.stat().st_nlink == 2, "precondition: the crash window was not reproduced"
    _fast_stale_lock(monkeypatch)
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    assert [n for n in _names(secret_dir) if n.endswith(".tmp")] == []
    assert path.stat().st_nlink == 1
    assert trust._read_user_secret(path) == secret  # the secret itself is unchanged and trusted
    assert [n for n in _names(secret_dir) if not n.endswith(".lock")] == [path.name]


def test_an_orphan_unpublished_temp_is_removed_on_creation(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secret"
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    path.unlink()  # start over: no published secret, only an orphan from a writer killed pre-link
    orphan = secret_dir / f".daemon-secret.json.{uuid.uuid4().hex}.tmp"
    orphan.write_text('{"secret": "' + "d" * 64 + '"}', encoding="utf-8")
    assert trust._load_or_create_user_secret() is not None
    assert not orphan.exists()
    assert [n for n in _names(secret_dir) if n.endswith(".tmp")] == []


def test_only_the_exact_temp_pattern_is_ever_deleted(tmp_path: Path) -> None:
    secret_dir = tmp_path / "secret"
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    path.unlink()
    bystanders = [
        secret_dir / "notes.tmp",
        secret_dir / ".daemon-secret.json.tmp",
        secret_dir / f".daemon-secret.json.{uuid.uuid4().hex[:8]}.tmp",
        secret_dir / f".daemon-secret.json.{uuid.uuid4().hex.upper()}.tmp",
        secret_dir / f"x.daemon-secret.json.{uuid.uuid4().hex}.tmp",
        secret_dir / f".daemon-secret.json.{uuid.uuid4().hex}.tmp.keep",
        secret_dir / "daemon.json",
    ]
    for b in bystanders:
        b.write_text("keep me", encoding="utf-8")
    assert trust._load_or_create_user_secret() is not None
    for b in bystanders:
        assert b.read_text(encoding="utf-8") == "keep me", f"{b.name} was deleted"


def test_a_symlink_or_directory_with_the_pattern_name_is_never_followed_or_removed(
    tmp_path: Path,
) -> None:
    secret_dir = tmp_path / "secret"
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    path.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("precious", encoding="utf-8")
    link = secret_dir / f".daemon-secret.json.{uuid.uuid4().hex}.tmp"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    folder = secret_dir / f".daemon-secret.json.{uuid.uuid4().hex}.tmp"
    folder.mkdir()
    (folder / "inner.txt").write_text("inner", encoding="utf-8")
    assert trust._load_or_create_user_secret() is not None
    assert outside.read_text(encoding="utf-8") == "precious"
    assert link.is_symlink(), "the symlink with the pattern name was removed"
    assert (folder / "inner.txt").read_text(encoding="utf-8") == "inner"


def test_a_clean_load_does_not_take_the_creation_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert trust._load_or_create_user_secret() is not None
    calls: list[Any] = []
    real = trust.index_lock

    def _spy(p: Path, **kw: Any) -> Any:
        calls.append(p)
        return real(p, **kw)

    monkeypatch.setattr(trust, "index_lock", _spy)
    assert trust._load_or_create_user_secret() is not None  # hot path: nlink == 1, no lock
    assert calls == []
