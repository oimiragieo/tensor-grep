"""Census of every two-stage lock acquisition: inject at each step, assert nothing leaks.

Contract boundary (the same as the ``_index_lock`` module docstring): lock cleanup is
exception-safe against every synchronous exception from a real syscall (the ``OSError`` family) at
every step, plus ``BaseException`` (such as ``KeyboardInterrupt``) at each acquisition and hand-off
step enumerated below. Python cannot exclude an asynchronous exception between two arbitrary
bytecodes; a leak from that is bounded to the process lifetime, because OS locks die with the
process.

The lock file is published atomically (``publish_new_lock_file``: temp file + ``os.link``), so the
public lock path is never empty or partial and no failure path ever unlinks it. For each
acquisition, every step between RESOURCE CREATION (fd opened, temp created, OS lock taken, lock
published) and OWNERSHIP HANDOFF gets a fault, and afterwards:

  (a) no descriptor opened by the acquisition is still open,
  (b) no OS lock is held,
  (c) no file this call created is left behind (the never-deleted ``*.os`` sidecar is the one
      deliberate exception; a TEMP that a failing ``unlink`` could not remove is tolerated only for
      the steps marked ``tmp_may_remain``, and the public lock must still be absent),
  (d) a fresh acquisition succeeds.

The enumerated steps are in ``_STEPS``; a step added to an acquisition without a row here is
exactly the gap this census exists to prevent.
"""

from __future__ import annotations

import errno
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import _index_lock as il
from tensor_grep.cli import session_daemon_start_lock as sl
from tensor_grep.cli.session_daemon_start_lock import _daemon_start_lock_path

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


def _raiser(exc: BaseException) -> Callable[..., Any]:
    def _raise(*_a: Any, **_k: Any) -> Any:
        raise exc

    return _raise


def _once(exc: BaseException, then: Callable[..., Any]) -> Callable[..., Any]:
    """Raise ``exc`` on the first call only (before delegating to ``then`` afterwards)."""
    state = {"done": False}

    def _call(*a: Any, **k: Any) -> Any:
        if not state["done"]:
            state["done"] = True
            raise exc
        return then(*a, **k)

    return _call


def _fail_tmp_create(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    real_open = os.open

    def _open(path: Any, *args: Any, **kwargs: Any) -> int:
        if str(path).endswith(".tmp"):
            raise exc
        return real_open(path, *args, **kwargs)

    mp.setattr(os, "open", _open)


def _fail_close_once(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    real_close = os.close
    state = {"done": False}

    def _close(fd: int) -> None:
        real_close(fd)  # the descriptor IS closed; the call then reports the fault
        if not state["done"]:
            state["done"] = True
            raise exc

    mp.setattr(os, "close", _close)


def _fail_tmp_unlink(mp: pytest.MonkeyPatch, exc: BaseException) -> None:
    real_unlink = os.unlink

    def _unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        if str(path).endswith(".tmp"):
            raise exc
        real_unlink(path, *args, **kwargs)

    mp.setattr(os, "unlink", _unlink)


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


# (step name, injector, tmp_may_remain) applied to BOTH lock-file acquisitions (index / start).
_PUBLISH_STEPS: list[tuple[str, Injector, bool]] = [
    ("pid-computation", lambda mp, e: mp.setattr(os, "getpid", _raiser(e)), False),
    ("tmp-create", _fail_tmp_create, False),
    ("tmp-write", lambda mp, e: mp.setattr(os, "write", _raiser(e)), False),
    ("tmp-fsync", lambda mp, e: mp.setattr(os, "fsync", _raiser(e)), False),
    ("tmp-close", _fail_close_once, False),
    ("link", lambda mp, e: mp.setattr(os, "link", _raiser(e)), False),
    (
        "link-exists-then-free",
        lambda mp, e: mp.setattr(os, "link", _once(FileExistsError(errno.EEXIST, "held"), os.link)),
        False,
    ),
    ("tmp-unlink", _fail_tmp_unlink, True),
]

_STEPS: list[tuple[str, Callable[[Path], Any], Injector, bool]] = [
    # try_os_file_lock: open -> lock -> identity check -> register -> return
    ("os_lock:lock-call", _acq_os_lock, _inject_os_lock_call, False),
    (
        "os_lock:identity-check",
        _acq_os_lock,
        lambda mp, e: mp.setattr(il, "_lock_identity_matches", _raiser(e)),
        False,
    ),
    (
        "os_lock:registration",
        _acq_os_lock,
        lambda mp, e: mp.setattr(il, "_register_held_fd", _raiser(e)),
        False,
    ),
    # index_lock: pid -> sidecar -> legacy [token, publish(tmp create/write/fsync/close, link,
    # tmp unlink), heartbeat start] -> body -> release
    (
        "index_lock:sidecar-acquire",
        _acq_index_lock,
        lambda mp, e: mp.setattr(il, "try_os_file_lock", _raiser(e)),
        False,
    ),
    (
        "index_lock:token",
        _acq_index_lock,
        lambda mp, e: mp.setattr(il, "uuid4", _raiser(e)),
        False,
    ),
    ("index_lock:heartbeat-start", _acq_index_lock, _inject_thread_start, False),
    # daemon start lock: token -> sidecar -> legacy [publish(...)] -> register
    (
        "start_lock:sidecar-acquire",
        _acq_start_lock,
        lambda mp, e: mp.setattr(sl, "try_os_file_lock", _raiser(e)),
        False,
    ),
    (
        "start_lock:token",
        _acq_start_lock,
        lambda mp, e: mp.setattr(sl, "uuid4", _raiser(e)),
        False,
    ),
    (
        "start_lock:legacy-helper",
        _acq_start_lock,
        lambda mp, e: mp.setattr(sl, "_legacy_try_acquire_daemon_start_lock", _raiser(e)),
        False,
    ),
    ("start_lock:registration", _acq_start_lock, _inject_guard_enter, False),
]
for _name, _inj, _tmp in _PUBLISH_STEPS:
    _STEPS.append((f"index_lock:{_name}", _acq_index_lock, _inj, _tmp))
    _STEPS.append((f"start_lock:{_name}", _acq_start_lock, _inj, _tmp))


def _files(base: Path, *, ignore_tmp: bool = False) -> set[str]:
    """Files under ``base`` (relative), minus the never-deleted ``*.os`` sidecar handles."""
    return {
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file()
        and not p.name.endswith(".os")
        and not (ignore_tmp and p.name.endswith(".tmp"))
    }


@pytest.mark.parametrize("make_error", _ERRORS, ids=_ERROR_IDS)
@pytest.mark.parametrize("step", _STEPS, ids=[s[0] for s in _STEPS])
def test_a_fault_at_every_acquisition_step_leaks_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    step: tuple[str, Callable[[Path], Any], Injector, bool],
    make_error: Callable[[], BaseException],
) -> None:
    name, acquire, inject, tmp_may_remain = step
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
        except (OSError, KeyboardInterrupt, il.IndexLockTimeoutError):
            pass  # the primary error may propagate or be converted to "not acquired"
    finally:
        mp.undo()

    assert tracker.open_fds == set(), f"{name}: descriptors left open: {tracker.open_fds}"  # (a)
    left = _files(base, ignore_tmp=tmp_may_remain) - before
    assert not left, f"{name}: files left behind: {left}"  # (c)
    assert not il._lock_path_for(base / "index.json").exists()
    assert not _daemon_start_lock_path(base.resolve()).exists()  # never an orphaned public lock
    # (b) + (d): no OS lock is held and a fresh acquisition succeeds
    sidecar_fd = il.try_os_file_lock(il._os_lock_path_for(base / "index.json"))
    assert sidecar_fd is not None, f"{name}: the OS lock is still held"
    il.release_os_file_lock(sidecar_fd)
    assert acquire(base), f"{name}: a fresh acquisition failed after the fault"


def test_the_census_covers_every_acquisition_kind_and_publish_step() -> None:
    names = {s[0] for s in _STEPS}
    assert {n.split(":")[0] for n in names} == {"os_lock", "index_lock", "start_lock"}
    assert len(names) == len(_STEPS)
    for publish_step, _inj, _tmp in _PUBLISH_STEPS:
        assert f"index_lock:{publish_step}" in names and f"start_lock:{publish_step}" in names


# --- publish_new_lock_file directly -------------------------------------------------------------


def test_cleanup_that_itself_raises_still_closes_the_fd_and_loses_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write fault (ENOSPC) followed by a fault IN the cleanup (the temp unlink raising
    KeyboardInterrupt): the descriptor is closed first, nothing public exists, the OS lock is free."""
    base = tmp_path / "work"
    base.mkdir()
    mp = pytest.MonkeyPatch()
    tracker = _Tracker(mp, base)
    mp.setattr(os, "write", _raiser(OSError(errno.ENOSPC, "full")))
    _fail_tmp_unlink(mp, KeyboardInterrupt())
    try:
        with pytest.raises(KeyboardInterrupt):
            il.publish_new_lock_file(base / "x.lock", b"1\n")
    finally:
        mp.undo()
    assert tracker.open_fds == set()
    assert not (base / "x.lock").exists()
    assert il.try_os_file_lock(il._os_lock_path_for(base / "index.json")) is not None


def test_a_failed_publish_never_creates_the_public_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "x.lock"
    monkeypatch.setattr(os, "fsync", _raiser(OSError(errno.ENOSPC, "full")))
    with pytest.raises(OSError):
        il.publish_new_lock_file(lock, b"1\n")
    monkeypatch.undo()
    assert not lock.exists()
    assert list(tmp_path.iterdir()) == []  # the temp was removed too


def test_successful_publish_has_complete_content_and_no_temp_left(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    il.publish_new_lock_file(lock, b"123\ntoken\n")
    assert lock.read_bytes() == b"123\ntoken\n"  # binary: no text-mode translation
    assert [p.name for p in tmp_path.iterdir()] == ["x.lock"]


def test_publish_refuses_to_replace_a_held_lock(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    il.publish_new_lock_file(lock, b"A\n")
    with pytest.raises(FileExistsError):
        il.publish_new_lock_file(lock, b"B\n")
    assert lock.read_bytes() == b"A\n"
    assert [p.name for p in tmp_path.iterdir()] == ["x.lock"]


def test_a_filesystem_without_hard_links_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "x.lock"
    monkeypatch.setattr(os, "link", _raiser(OSError(errno.ENOTSUP, "links unsupported")))
    with pytest.raises(OSError) as caught:
        il.publish_new_lock_file(lock, b"1\n")
    assert caught.value.errno == errno.ENOTSUP
    assert not lock.exists()  # never silently taken another way


def test_event_gated_a_never_deletes_or_unlocks_bs_lock(tmp_path: Path) -> None:
    """A pauses between writing its temp and linking it; B publishes the lock in that window.
    A's link then fails (FileExistsError) and A's cleanup runs -- B's lock must be untouched."""
    lock = tmp_path / "x.lock"
    a_before_link, resume_a = threading.Event(), threading.Event()
    real_link = os.link
    results: dict[str, BaseException | None] = {}

    def _gated_link(src: str, dst: str, *a: Any, **k: Any) -> None:
        if threading.current_thread().name == "A":
            a_before_link.set()
            assert resume_a.wait(10)
        real_link(src, dst, *a, **k)

    def _run(name: str, content: bytes) -> None:
        try:
            il.publish_new_lock_file(lock, content)
            results[name] = None
        except BaseException as exc:
            results[name] = exc

    mp = pytest.MonkeyPatch()
    mp.setattr(os, "link", _gated_link)
    ta = threading.Thread(target=_run, args=("A", b"A-lock\n"), name="A")
    tb = threading.Thread(target=_run, args=("B", b"B-lock\n"), name="B")
    try:
        ta.start()
        assert a_before_link.wait(10), "A never reached its link"
        tb.start()
        tb.join(10)
        assert results.get("B") is None and lock.read_bytes() == b"B-lock\n"
        resume_a.set()
        ta.join(10)
    finally:
        resume_a.set()
        mp.undo()
    assert isinstance(results.get("A"), FileExistsError), results
    assert lock.read_bytes() == b"B-lock\n", "A's cleanup touched B's lock"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.lock"]  # no temps left either
