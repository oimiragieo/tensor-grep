"""F-05 / F-10: stale-lock reclaim must never let two holders win.

The old reclaim was check-then-unlink: waiter A stats a stale lock, waiter B reclaims AND acquires,
then A's unlink deletes B's FRESH lock and A acquires too. The interleaving is forced
deterministically by parking waiter A immediately before its unlink of the lock file.
"""

from __future__ import annotations

import os
import pathlib
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import _index_lock, session_daemon

_PARK_TIMEOUT_S = 10.0  # council wave-2b r7: a SAFETY cap only; tests release the park explicitly


def _overlaps(intervals: list[tuple[str, float, float]]) -> bool:
    ordered = sorted(intervals, key=lambda item: item[1])
    return any(prev[2] > nxt[1] for prev, nxt in zip(ordered, ordered[1:], strict=False))


def test_overlap_detector_flags_nested_intervals_and_passes_disjoint_ones() -> None:
    # POSITIVE CONTROL for the detector the race tests rely on.
    assert _overlaps([("A", 0.0, 2.0), ("B", 1.0, 3.0)]) is True
    assert _overlaps([("A", 0.0, 1.0), ("B", 1.5, 3.0)]) is False


class _ParkWaiterA:
    """Park thread `waiter-A` right before it unlinks `lock_path`, until `release` is set."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, lock_path: Path) -> None:
        self.parked = threading.Event()
        self.release = threading.Event()
        self.expired = False
        real_unlink: Any = pathlib.Path.unlink
        lock_str = str(lock_path)
        first = {"done": False}

        def _unlink(path_self: Path, *args: Any, **kwargs: Any) -> None:
            if (
                str(path_self) == lock_str
                and threading.current_thread().name == "waiter-A"
                and not first["done"]
            ):
                first["done"] = True
                self.parked.set()
                if not self.release.wait(_PARK_TIMEOUT_S):  # council wave-2b r8: expiry is a FAILURE
                    self.expired = True
                    raise RuntimeError("park safety timeout expired before the test released it")
            return real_unlink(path_self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "unlink", _unlink)


def _age(path: Path, seconds: float) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_index_lock_stale_reclaim_never_yields_two_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999\ndeadbeef\n", encoding="utf-8")
    _age(lock_path, 60.0)

    park = _ParkWaiterA(monkeypatch, lock_path)
    b_contended = threading.Event()
    real_try = getattr(_index_lock, "try_os_file_lock", None)
    if real_try is not None:  # post-fix only; on main the witness is b_entered

        def _witness(path):
            fd = real_try(path)
            if fd is None and threading.current_thread().name == "waiter-B":
                b_contended.set()
            return fd

        monkeypatch.setattr(_index_lock, "try_os_file_lock", _witness)
    holding: set[str] = set()
    overlap_seen = threading.Event()
    a_entered, b_entered, release_b = threading.Event(), threading.Event(), threading.Event()
    guard = threading.Lock()
    errors: list[BaseException] = []

    def _hold(name: str) -> None:
        # council wave-2b r7: EVENT-controlled holds, never timed sleeps -> deterministic interleaving
        try:
            with _index_lock.index_lock(index_path, poll_interval_s=0.01, timeout_s=8.0, stale_after_s=5.0):
                with guard:
                    holding.add(name)
                    if len(holding) > 1:
                        overlap_seen.set()
                (b_entered if name == "B" else a_entered).set()
                if name == "B" and not release_b.wait(5.0):
                    raise RuntimeError("B hold expired before A entered")  # council wave-2b r8
                with guard:
                    holding.discard(name)
        except BaseException as exc:
            errors.append(exc)

    a = threading.Thread(target=_hold, args=("A",), name="waiter-A")
    b = threading.Thread(target=_hold, args=("B",), name="waiter-B")
    try:
        a.start()
        assert park.parked.wait(5.0), "premise: waiter A reached its stale-lock unlink"
        b.start()
        # council wave-2b r12: an ASSERTED witness that B reached the contention point while A is
        # parked -- main: B ENTERS (b_entered); fixed: B is REFUSED by the sidecar (b_contended).
        deadline = time.monotonic() + 5.0
        while not (b_entered.is_set() or b_contended.is_set()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert b_entered.is_set() or b_contended.is_set(), "B never reached the lock while A was parked"
        park.release.set()  # main: A now unlinks B's FRESH lock while B still holds -> overlap
        assert a_entered.wait(5.0), "A never entered"  # A always gets in (alone, post-fix)
    finally:
        park.release.set()
        release_b.set()
        a.join(10.0)
        b.join(10.0)
    assert not a.is_alive() and not b.is_alive(), "waiter hung"
    assert errors == []
    assert not park.expired
    assert not overlap_seen.is_set(), "two holders at once"


def test_daemon_start_lock_stale_reclaim_never_yields_two_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    lock_path = session_daemon._daemon_start_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999\n", encoding="utf-8")
    _age(lock_path, session_daemon._DAEMON_START_LOCK_STALE_SECONDS + 60)

    park = _ParkWaiterA(monkeypatch, lock_path)
    results: dict[str, bool] = {}

    errors: list[BaseException] = []

    def _try(name: str) -> None:
        try:
            results[name] = session_daemon._try_acquire_daemon_start_lock(root)
        except BaseException as exc:  # council wave-2b r8
            errors.append(exc)

    a = threading.Thread(target=_try, args=("A",), name="waiter-A")
    b = threading.Thread(target=_try, args=("B",), name="waiter-B")
    try:  # council wave-2b r19: guaranteed cleanup -- never leave the start lock held
        a.start()
        assert park.parked.wait(5.0), "premise: waiter A reached its stale-lock unlink"
        b.start()
        b.join(5.0)  # B is non-blocking: it returns True (bug) or False (fixed)
        assert not b.is_alive() and park.parked.is_set() and not park.release.is_set(), "B must finish while A is still parked"
        park.release.set()
        a.join(10.0)
        assert not a.is_alive(), "waiter A hung"
    finally:
        park.release.set()
        for t in (a, b):
            if t.ident is not None:
                t.join(10.0)
        session_daemon._release_daemon_start_lock(root)  # release whatever this process acquired

    assert errors == [] and not park.expired, (errors, park.expired)
    assert set(results) == {"A", "B"}, results
    assert results.get("A") is True, results  # the parked reclaimer still gets the lock
    assert results.get("B") is not True, f"both waiters acquired the start lock: {results}"


def test_daemon_start_lock_release_leaves_another_owners_lock(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    lock_path = session_daemon._daemon_start_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    other_pid = os.getpid() + 1
    lock_path.write_text(f"{other_pid}\n", encoding="utf-8")
    session_daemon._release_daemon_start_lock(root)
    assert lock_path.exists(), "released a lock this process does not own"

    # POSITIVE CONTROL: this process's own lock is released.
    lock_path.unlink()
    assert session_daemon._try_acquire_daemon_start_lock(root) is True
    session_daemon._release_daemon_start_lock(root)
    assert not lock_path.exists()
