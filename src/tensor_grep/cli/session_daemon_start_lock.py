"""The session daemon's per-root start lock (F-05 / F-10).

An OS advisory sidecar lock (``<start-lock>.os``) WRAPS the unchanged legacy ``O_EXCL`` protocol,
exactly as ``_index_lock.index_lock`` does. Only one new-version process at a time can be inside the
legacy section, so the check-then-unlink stale reclaim cannot interleave and a release can never
unlink another holder's lock. New-vs-old is the legacy protocol, unchanged (an old-version tg only
ever sees the legacy file).

Kept out of ``session_daemon.py``: that file is size-ratcheted and may only shrink.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from tensor_grep.cli._index_lock import (
    register_after_fork_child_hook,
    release_os_file_lock,
    try_os_file_lock,
)
from tensor_grep.cli.session_store import _sessions_dir

_DAEMON_START_LOCK_FILE = ".daemon-start.lock"

_DAEMON_START_LOCK_FDS: dict[str, int] = {}
_DAEMON_START_LOCK_GUARD = threading.Lock()


def _daemon_start_lock_path(root: Path) -> Path:
    return _sessions_dir(root) / _DAEMON_START_LOCK_FILE


def _reset_start_lock_state_after_fork() -> None:
    """A fork child never owns the parent's start lock (its inherited fds were closed by
    `_index_lock`); forget the bookkeeping so a child release is a no-op."""
    global _DAEMON_START_LOCK_GUARD
    _DAEMON_START_LOCK_GUARD = threading.Lock()
    _DAEMON_START_LOCK_FDS.clear()


register_after_fork_child_hook(_reset_start_lock_state_after_fork)


def _daemon_start_sidecar_path(root: Path) -> Path:
    lock_path = _daemon_start_lock_path(root)
    return lock_path.with_name(lock_path.name + ".os")


def _try_acquire_daemon_start_lock(root: Path) -> bool:
    _daemon_start_lock_path(root).parent.mkdir(parents=True, exist_ok=True)
    fd = try_os_file_lock(_daemon_start_sidecar_path(root))
    if fd is None:
        return False
    # Exception-safe two-stage acquisition: on ANY unsuccessful outcome (a False return, an OSError
    # such as ENOSPC at the legacy file, or a BaseException like KeyboardInterrupt) the sidecar is
    # released -- otherwise a long-lived process would block every later daemon start -- and the
    # primary error propagates unchanged.
    legacy_held = False
    registered = False
    try:
        legacy_held = _legacy_try_acquire_daemon_start_lock(root)
        if legacy_held:
            with _DAEMON_START_LOCK_GUARD:
                _DAEMON_START_LOCK_FDS[str(root)] = fd
            registered = True
        return registered
    finally:
        if not registered:
            try:
                if legacy_held:
                    _legacy_release_daemon_start_lock(root)
            finally:
                release_os_file_lock(fd)


def _release_daemon_start_lock(root: Path) -> None:
    with _DAEMON_START_LOCK_GUARD:
        fd = _DAEMON_START_LOCK_FDS.pop(str(root), None)
    if fd is None:
        return  # this process holds no start lock for `root`: touch nothing (F-10)
    try:
        _legacy_release_daemon_start_lock(root)
    finally:
        release_os_file_lock(fd)


def _legacy_try_acquire_daemon_start_lock(root: Path) -> bool:
    # Late-bound so a test (or operator) patching ``session_daemon._DAEMON_START_LOCK_STALE_SECONDS``
    # still takes effect after the move out of that module.
    from tensor_grep.cli import session_daemon

    lock_path = _daemon_start_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
                if lock_age > session_daemon._DAEMON_START_LOCK_STALE_SECONDS:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            return False
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
        finally:
            os.close(fd)
        return True
    return False


def _legacy_release_daemon_start_lock(root: Path) -> None:
    try:
        _daemon_start_lock_path(root).unlink()
    except OSError:
        pass
