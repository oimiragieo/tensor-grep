from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock, session_store


def _hold_session_index(root: Path) -> tuple[threading.Event, threading.Thread]:
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with _index_lock.index_lock(session_store._index_path(root)):
            acquired.set()
            release.wait(1.5)

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(2.0)
    return release, thread


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def test_open_session_index_lock_uses_daemon_deadline(tmp_path: Path) -> None:
    root = _project(tmp_path)
    release, holder = _hold_session_index(root)
    deadline = time.monotonic() + 0.5
    started = time.monotonic()
    try:
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            session_store.open_session(str(root), deadline_monotonic=deadline)
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join(timeout=2.0)
    assert not holder.is_alive()


def test_refresh_session_index_lock_uses_daemon_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    opened = session_store.open_session(str(root))
    existing = session_store.get_session(opened.session_id, str(root))
    monkeypatch.setattr(session_store, "_clear_all_source_caches", lambda: None)
    monkeypatch.setattr(session_store, "get_session", lambda *_args, **_kwargs: existing)
    monkeypatch.setattr(session_store, "_stale_changeset", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        session_store, "build_repo_map", lambda *_args, **_kwargs: existing["repo_map"]
    )
    monkeypatch.setattr(
        session_store, "_capture_snapshot", lambda *_args, **_kwargs: existing["snapshot"]
    )
    release, holder = _hold_session_index(root)
    deadline = time.monotonic() + 0.5
    started = time.monotonic()
    try:
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            session_store.refresh_session(opened.session_id, str(root), deadline_monotonic=deadline)
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
        holder.join(timeout=2.0)
    assert not holder.is_alive()
