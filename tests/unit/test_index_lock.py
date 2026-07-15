"""Unit coverage for ``_index_lock`` constants/mechanics not owned by
``test_index_lock_concurrency.py`` (that file is owned by another change).

H9: ``_TIMEOUT_S`` must exceed ``_STALE_AFTER_S``. A holder killed mid-write can leave a lock
younger than ``_STALE_AFTER_S`` at the moment a waiter starts polling; if the waiter's own
deadline could expire first (the pre-fix 5.0s timeout < 10.0s staleness split), that
fresh-but-dead lock would NEVER be reclaimed within the wait window -- every waiter raises
``IndexLockTimeoutError`` instead of self-healing.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from tensor_grep.cli import _index_lock


def test_default_timeout_exceeds_stale_threshold() -> None:
    """Regression pin for the H9 fix: the wait deadline must outlast the staleness
    threshold, or a fresh dead holder can never be reclaimed before a waiter gives up."""
    assert _index_lock._TIMEOUT_S > _index_lock._STALE_AFTER_S


def test_fresh_stale_lock_is_reclaimed_before_the_waiter_gives_up(tmp_path: Path) -> None:
    """Behavioral proof of the H9 fix, using the SAME timeout>stale ratio as production
    (scaled down 10x for test speed/determinism): a lock planted well under
    ``stale_after_s`` old at the moment a waiter arrives -- e.g. a daemon killed partway
    into a fresh mid-write, mirroring the audit's "a daemon killed mid-write leaves a lock
    <10s old" scenario against a 10s staleness threshold -- must be reclaimed before the
    waiter's OWN deadline, not raise ``IndexLockTimeoutError``.

    Pre-fix ratio (timeout_s=0.5 < stale_after_s=1.0) would ALWAYS time out here:
    age + timeout = 0.3 + 0.5 = 0.8 < 1.0, so the lock can never reach the staleness
    threshold before the waiter's deadline fires (the ratio invariant itself is pinned by
    ``test_default_timeout_exceeds_stale_threshold`` above). Post-fix ratio
    (timeout_s=1.2 > stale_after_s=1.0) guarantees reclaim: the lock reaches staleness at
    age=1.0 (elapsed ~0.7s from the waiter's start), comfortably inside the 1.2s deadline.

    Release-blocking flake fixed here: this test used to ALSO assert a hardcoded
    ``elapsed < 1.2`` wall-clock bound. A loaded macOS CI runner measured 1.68s and failed
    it -- not because reclaim was broken, but because ``index_lock``'s acquire loop checks
    staleness *before* it checks its own deadline: the ``except FileExistsError`` branch
    ``continue``s straight back to the top of the loop on a stale hit, skipping the
    deadline check for that iteration entirely. A single overshot
    ``time.sleep(poll_interval_s)`` on a starved scheduler can push real wall-clock past
    ``timeout_s`` while that very wake-up still finds the lock stale and reclaims it
    correctly -- correct self-healing, misread as a failure by a tight wall-clock assert.

    The real H9 invariant -- "reclaimed before giving up", not a specific latency -- is now
    asserted as an OUTCOME instead of a stopwatch reading:
      1. no ``IndexLockTimeoutError`` escapes the ``with`` (a never-reclaims regression
         raises here and fails the test before the body below ever runs);
      2. the lock's owner inside the ``with`` is THIS process, not the planted fake pid
         99999 (proves a reclaim actually happened, not merely that some file exists);
      3. the lock file is gone after release.
    A generous, CI-jitter-tolerant wall-clock ceiling remains as a backstop against a
    "technically reclaims, but pathologically slow" regression in the reclaim path itself
    (which -- unlike the plain wait above -- is NOT bounded by ``timeout_s``, per the
    ``continue`` above): several multiples of the waiter's own give-up deadline, so routine
    CI scheduling jitter (the observed 1.68s against a 1.2s bound, ~1.4x) can never trip it.
    """
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.write_text("99999\n", encoding="utf-8")
    stale_after_s = 1.0
    timeout_s = 1.2  # H9 ratio: must exceed stale_after_s -- see module docstring above
    fresh_mtime = time.time() - 0.3  # "crashed" 0.3s ago: well under stale_after_s
    os.utime(lock_path, (fresh_mtime, fresh_mtime))

    start = time.monotonic()
    # A never-reclaims (or reclaims-only-after-giving-up) regression raises
    # IndexLockTimeoutError out of __enter__ here, failing the test before the body runs.
    with _index_lock.index_lock(index_path, timeout_s=timeout_s, stale_after_s=stale_after_s):
        # The reclaim genuinely happened: THIS process's pid displaced the planted fake
        # "dead holder" pid (99999), not just "a lock file happens to exist".
        owner_pid = lock_path.read_text(encoding="utf-8").splitlines()[0]
        assert owner_pid == str(os.getpid())
    elapsed = time.monotonic() - start

    assert not lock_path.exists()
    # Generous CI-jitter backstop (secondary -- see docstring): catches a "reclaims, but
    # pathologically slow" regression in the reclaim path itself, which the deadline check
    # above does not bound.
    assert elapsed < timeout_s * 5


# --------------------------------------------------------------------------------------
# audit #14: index-lock not ownership-aware. The lock wrote `{pid}` on acquire but never
# read it back, and `finally` unconditionally unlinked the lockfile. Failure sequence: A
# acquires; A goes slow past the staleness threshold; a waiter B sees A's lock as stale and
# reclaims it (deletes A's file, writes its own); A's `finally` finally runs and
# unconditionally deletes B's LIVE lock -- the index now has no lock while B still thinks
# it holds one -> lost-update / two concurrent writers. Fix is two mechanisms: a uuid4
# ownership token verified on release (the correctness backstop), plus an mtime heartbeat
# while a section is long-held (the primary defense -- prevents a live-but-slow holder from
# ever LOOKING stale to a waiter in the first place).
# --------------------------------------------------------------------------------------


def test_acquire_writes_pid_and_token(tmp_path: Path) -> None:
    """Acquire must write `{pid}\\n{token}\\n`, and the token must round-trip through the
    same reader `_release_lock` uses to decide ownership."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    with _index_lock.index_lock(index_path):
        content = lock_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert len(lines) == 2
        assert lines[0] == str(os.getpid())
        assert lines[1]  # a non-empty uuid4 hex token
        assert _index_lock._token_for_lock(lock_path) == lines[1]


def test_release_with_matching_token_unlinks(tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    token = "matching-token"
    lock_path.write_text(f"{os.getpid()}\n{token}\n", encoding="utf-8")

    _index_lock._release_lock(lock_path, token)

    assert not lock_path.exists()


def test_release_with_mismatched_token_does_not_unlink(tmp_path: Path) -> None:
    """The correctness backstop: if the token on disk isn't ours (another holder reclaimed
    it), release must leave that live lock alone."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.write_text(f"{os.getpid()}\nsomeone-elses-token\n", encoding="utf-8")

    _index_lock._release_lock(lock_path, "my-token")

    assert lock_path.exists()


def test_release_tolerates_lock_already_gone(tmp_path: Path) -> None:
    """`_release_lock` must not raise if the lockfile is already gone (e.g. reclaimed and
    then that reclaimer also already released)."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    assert not lock_path.exists()

    _index_lock._release_lock(lock_path, "any-token")  # must not raise

    assert not lock_path.exists()


def test_core_race_A_release_does_not_delete_B_live_lock(tmp_path: Path) -> None:
    """The audit #14 core race, exercised through the real acquire/release code paths (not
    a hand simulation): A acquires; A's lock is pushed past the staleness threshold
    (simulating "A went slow"); B runs the real acquire loop, sees A's lock as stale, and
    reclaims it (unlink + own O_CREAT|O_EXCL + own token); A's `finally` then runs and MUST
    NOT delete B's live lock because A's token no longer matches what's on disk. The
    heartbeat is disabled here (a huge interval) to force the exact race the token backstop
    exists for -- the heartbeat's own defense is proven separately."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)

    cm_a = _index_lock.index_lock(
        index_path, stale_after_s=0.05, timeout_s=5.0, heartbeat_interval_s=999.0
    )
    cm_a.__enter__()  # A acquires
    try:
        token_a = _index_lock._token_for_lock(lock_path)
        assert token_a is not None

        # A "goes slow": push the lock well past the stale threshold.
        stale_mtime = time.time() - 10.0
        os.utime(lock_path, (stale_mtime, stale_mtime))

        # B runs the REAL acquire loop: sees A's lock as stale, reclaims it, writes its own.
        cm_b = _index_lock.index_lock(index_path, stale_after_s=0.05, timeout_s=5.0)
        cm_b.__enter__()
        try:
            token_b = _index_lock._token_for_lock(lock_path)
            assert token_b is not None
            assert token_b != token_a

            # A finally finishes its slow work and releases.
            cm_a.__exit__(None, None, None)
            cm_a = None  # already exited -- don't double-exit in the outer finally

            # B's live lock must survive A's release.
            assert lock_path.exists()
            assert _index_lock._token_for_lock(lock_path) == token_b
        finally:
            cm_b.__exit__(None, None, None)
        assert not lock_path.exists()  # B released cleanly too
    finally:
        if cm_a is not None:
            cm_a.__exit__(None, None, None)


def test_heartbeat_keeps_mtime_fresh_during_long_hold(tmp_path: Path) -> None:
    """The primary defense: while a section is long-held, the heartbeat thread must keep
    the lockfile's mtime fresh so a concurrent waiter's staleness check never sees it as
    dead -- the observed age of the lockfile must never approach `stale_after_s` during the
    hold."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    # Absolute values are deliberately generous: the property under test is that the
    # heartbeat keeps mtime younger than stale_after_s, which only requires the heartbeat
    # interval to be a small fraction of stale_after_s. Tight bounds flake on loaded 2-core
    # CI runners where the heartbeat thread can be GIL-starved well past the heartbeat
    # interval (observed: 0.152s > 0.15s on macos-latest; then 0.784s > 0.6s on
    # windows-latest 2026-07-15, which blocked the v1.76.9 release run). stale_after_s here
    # is 40x the heartbeat interval -- roughly 2.5x headroom over the worst observed
    # starvation of 0.78s -- so realistic CI scheduling jitter stays well clear of the bound.
    stale_after_s = 2.0
    hold_s = 4.0  # several multiples of stale_after_s

    max_observed_age = 0.0
    stop_observer = threading.Event()

    def observe() -> None:
        nonlocal max_observed_age
        while not stop_observer.wait(0.02):
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue
            max_observed_age = max(max_observed_age, age)

    observer = threading.Thread(target=observe)
    with _index_lock.index_lock(index_path, stale_after_s=stale_after_s, heartbeat_interval_s=0.05):
        observer.start()
        time.sleep(hold_s)
        stop_observer.set()
        observer.join(timeout=2.0)

    assert max_observed_age < stale_after_s


def test_heartbeat_stops_propping_up_a_lock_it_no_longer_owns(tmp_path: Path) -> None:
    """Defensive property of `_heartbeat_loop`: once the token on disk no longer matches
    the heartbeat's own token, it must stop touching mtime rather than propping up whatever
    (or whoever) is there now."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    my_token = "my-token"
    lock_path.write_text(f"{os.getpid()}\n{my_token}\n", encoding="utf-8")
    stop = threading.Event()
    thread = threading.Thread(
        target=_index_lock._heartbeat_loop, args=(lock_path, my_token, stop, 0.02)
    )
    thread.start()
    time.sleep(0.05)  # let at least one beat land while it's still "ours"

    # Someone else takes over the same path (as a real stale-reclaim would).
    lock_path.write_text("999999\nsomeone-elses-token\n", encoding="utf-8")
    other_mtime = time.time() - 5.0
    os.utime(lock_path, (other_mtime, other_mtime))

    # Give the heartbeat thread time to notice the mismatch and self-stop, then a further
    # window during which it must NOT have touched the file again.
    time.sleep(0.2)
    age_after_takeover = time.time() - lock_path.stat().st_mtime
    stop.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()

    assert age_after_takeover >= 5.0 - 0.25  # untouched since the "someone else" write


def test_repeated_acquire_release_hammer_windows(tmp_path: Path) -> None:
    """#355 repeated-spawn class: 15-20x acquire/release of the SAME index lock in one
    process must never crash, leak a PermissionError, or leave an orphaned lockfile --
    now also exercising the added heartbeat-thread spawn/join and token round-trip on every
    single cycle."""
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    for i in range(20):
        with _index_lock.index_lock(index_path, timeout_s=5.0):
            assert lock_path.exists()
            assert _index_lock._token_for_lock(lock_path) is not None
        assert not lock_path.exists(), f"orphaned lockfile after cycle {i}"
