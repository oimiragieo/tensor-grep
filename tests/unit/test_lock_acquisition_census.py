"""Census of every two-stage lock acquisition: inject at each step, assert nothing leaks.

For each acquisition (the OS sidecar, ``index_lock``'s legacy stage, the daemon start lock) every
step between RESOURCE CREATION (fd opened, file created, OS lock taken) and OWNERSHIP HANDOFF
(registered / returned) gets a BaseException injected, and afterwards:

  (a) no descriptor opened by the acquisition is still open,
  (b) no OS lock is held,
  (c) no file this call created is left behind (the never-deleted ``*.os`` sidecar is the one
      deliberate exception),
  (d) a fresh acquisition succeeds.

The enumerated steps are listed in ``_STEPS`` (and in the PR body). A step added to an acquisition
without a row here is exactly the gap this census exists to prevent.
"""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import _index_lock as il
from tensor_grep.cli import session_daemon_start_lock as sl

_ERRORS: list[Callable[[], BaseException]] = [
    lambda: OSError(errno.ENOSPC, "No space left on device"),
    lambda: KeyboardInterrupt(),
]
_ERROR_IDS = ["OSError-ENOSPC", "KeyboardInterrupt"]


class _Tracker:
    """Records descriptors opened under ``base`` and closed again (os.open / os.close spy)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, base: Path) -> None:
        self.open_fds: set[int] = set()
        base_str = str(base)
        real_open, real_close = os.open, os.close

        def _open(path: Any, *args: Any, **kwargs: Any) -> int:
            fd = real_open(path, *args, **kwargs)
            if str(path).startswith(base_str):
                self.open_fds.add(fd)
            return fd

        def _close(fd: int) -> None:
            self.open_fds.discard(fd)
            real_close(fd)

        monkeypatch.setattr(os, "open", _open)
        monkeypatch.setattr(os, "close", _close)


def _fail_open_for(monkeypatch: pytest.MonkeyPatch, *, legacy: bool, exc: BaseException) -> None:
    """Make os.open raise ``exc`` for the legacy ``.lock`` file (legacy=True) or the sidecar."""
    real_open = os.open

    def _open(path: Any, *args: Any, **kwargs: Any) -> int:
        if str(path).endswith(".os") != legacy:
            raise exc
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", _open)


def _raiser(exc: BaseException) -> Callable[..., Any]:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise exc

    return _raise


class _BoomThread:
    """threading.Thread stand-in whose start() fails (the heartbeat start in the legacy stage)."""

    exc: BaseException

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def start(self) -> None:
        raise self.exc


# --- the acquisitions -------------------------------------------------------------------------


def _acq_os_lock(base: Path) -> Any:
    fd = il.try_os_file_lock(il._os_lock_path_for(base / "index.json"))
    if fd is not None:
        il.release_os_file_lock(fd)
    return fd


def _acq_index_lock(base: Path) -> Any:
    with il.index_lock(base / "index.json", timeout_s=2, poll_interval_s=0.01):
        pass
    return True


def _acq_start_lock(base: Path) -> Any:
    got = sl._try_acquire_daemon_start_lock(base.resolve())
    if got:
        sl._release_daemon_start_lock(base.resolve())
    return got


# --- the enumerated steps: (id, acquisition, injector) ---------------------------------------

Injector = Callable[[pytest.MonkeyPatch, BaseException], None]


def _inject_os_lock_call(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    if sys.platform == "win32":
        import msvcrt

        mp.setattr(msvcrt, "locking", _raiser(exc))
    else:
        import fcntl

        mp.setattr(fcntl, "flock", _raiser(exc))


def _inject_thread_start(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    _BoomThread.exc = exc
    mp.setattr(il.threading, "Thread", _BoomThread)


def _inject_guard_enter(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    class _Guard:
        def __enter__(self) -> None:
            raise exc

        def __exit__(self, *_a: object) -> None:
            return None

    mp.setattr(sl, "_DAEMON_START_LOCK_GUARD", _Guard())


_STEPS: list[tuple[str, Callable[[Path], Any], Injector]] = [
    # try_os_file_lock: open -> lock -> identity check -> register -> return
    ("os_lock:lock-call", _acq_os_lock, _inject_os_lock_call),
    (
        "os_lock:identity-check",
        _acq_os_lock,
        lambda mp, e: mp.setattr(il, "_lock_identity_matches", _raiser(e)),
    ),
    (
        "os_lock:registration",
        _acq_os_lock,
        lambda mp, e: mp.setattr(il, "_register_held_fd", _raiser(e)),
    ),
    # index_lock: sidecar -> legacy [token, create O_EXCL, write, heartbeat start] -> body -> release
    (
        "index_lock:sidecar-acquire",
        _acq_index_lock,
        lambda mp, e: mp.setattr(il, "try_os_file_lock", _raiser(e)),
    ),
    (
        "index_lock:legacy-token",
        _acq_index_lock,
        lambda mp, e: mp.setattr(il, "uuid4", _raiser(e)),
    ),
    (
        "index_lock:legacy-create",
        _acq_index_lock,
        lambda mp, e: _fail_open_for(mp, legacy=True, exc=e),
    ),
    (
        "index_lock:legacy-write",
        _acq_index_lock,
        lambda mp, e: mp.setattr(os, "write", _raiser(e)),
    ),
    ("index_lock:legacy-heartbeat-start", _acq_index_lock, _inject_thread_start),
    # daemon start lock: sidecar -> legacy [create O_EXCL, write] -> register
    (
        "start_lock:sidecar-acquire",
        _acq_start_lock,
        lambda mp, e: mp.setattr(sl, "try_os_file_lock", _raiser(e)),
    ),
    (
        "start_lock:legacy-create",
        _acq_start_lock,
        lambda mp, e: _fail_open_for(mp, legacy=True, exc=e),
    ),
    (
        "start_lock:legacy-write",
        _acq_start_lock,
        lambda mp, e: mp.setattr(os, "write", _raiser(e)),
    ),
    (
        "start_lock:legacy-helper",
        _acq_start_lock,
        lambda mp, e: mp.setattr(sl, "_legacy_try_acquire_daemon_start_lock", _raiser(e)),
    ),
    ("start_lock:registration", _acq_start_lock, _inject_guard_enter),
]


def _files(base: Path) -> set[str]:
    """Files under ``base`` (relative), minus the never-deleted ``*.os`` sidecar handles."""
    return {
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file() and not p.name.endswith(".os")
    }


@pytest.mark.parametrize("make_error", _ERRORS, ids=_ERROR_IDS)
@pytest.mark.parametrize("step", _STEPS, ids=[s[0] for s in _STEPS])
def test_a_fault_at_every_acquisition_step_leaks_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    step: tuple[str, Callable[[Path], Any], Injector],
    make_error: Callable[[], BaseException],
) -> None:
    name, acquire, inject = step
    base = tmp_path / "work"
    base.mkdir()
    before = _files(base)

    # (control) the unfaulted acquisition succeeds, so a leak below is attributable to the fault
    assert acquire(base), f"{name}: the unfaulted acquisition must succeed"
    assert _files(base) == before

    mp = pytest.MonkeyPatch()
    tracker = _Tracker(mp, base)
    inject(mp, make_error())
    try:
        try:
            acquire(base)
        except (OSError, KeyboardInterrupt):
            pass  # the primary error may propagate or be converted to "not acquired"
    finally:
        mp.undo()

    assert tracker.open_fds == set(), f"{name}: descriptors left open: {tracker.open_fds}"  # (a)
    assert _files(base) == before, f"{name}: files left behind: {_files(base) - before}"  # (c)
    # (b) + (d): no OS lock is held and a fresh acquisition succeeds
    sidecar_fd = il.try_os_file_lock(il._os_lock_path_for(base / "index.json"))
    assert sidecar_fd is not None, f"{name}: the OS lock is still held"
    il.release_os_file_lock(sidecar_fd)
    assert acquire(base), f"{name}: a fresh acquisition failed after the fault"


def test_the_census_covers_every_acquisition_kind() -> None:
    kinds = {s[0].split(":")[0] for s in _STEPS}
    assert kinds == {"os_lock", "index_lock", "start_lock"}
    assert len({s[0] for s in _STEPS}) == len(_STEPS)


# --- write_new_lock_file: real os.write faults, and never deleting someone else's lock --------


def test_failed_write_removes_the_file_this_call_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "x.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    monkeypatch.setattr(os, "write", _raiser(OSError(errno.ENOSPC, "full")))
    with pytest.raises(OSError):
        il.write_new_lock_file(fd, lock, b"1\n")
    monkeypatch.undo()
    assert not lock.exists(), "a failed write orphaned the lock file it created"
    with pytest.raises(OSError):
        os.fstat(fd)  # the descriptor is closed


def test_failed_write_never_deletes_a_lock_that_is_not_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "x.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    real_fstat = os.fstat

    def _other_inode(f: int) -> os.stat_result:
        st = real_fstat(f)
        return os.stat_result((st.st_mode, st.st_ino + 1, *tuple(st)[2:]))

    monkeypatch.setattr(os, "fstat", _other_inode)  # the path now "names a different file"
    monkeypatch.setattr(os, "write", _raiser(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        il.write_new_lock_file(fd, lock, b"1\n")
    monkeypatch.undo()
    assert lock.exists(), "deleted a lock file that identity says is not ours"


def test_successful_write_keeps_the_file_and_closes_the_fd(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    il.write_new_lock_file(fd, lock, b"1\n")
    assert lock.read_bytes().replace(b"\r\n", b"\n") == b"1\n"  # Windows text-mode fd
    with pytest.raises(OSError):
        os.fstat(fd)
