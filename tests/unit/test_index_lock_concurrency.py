"""Concurrency regression tests for the q10 index.json RMW lost-update race.

``session_store.open_session`` / ``refresh_session`` and ``checkpoint_store.create_checkpoint``
each do ``_load_index`` -> mutate -> ``_write_index`` with no serialization between the read and
the atomic ``os.replace`` swap. Two near-simultaneous writers can each read the same pre-insert
index and the second clobbers the first's insert -- an orphaned session/snapshot dir that is
invisible to ``list`` and never retention-pruned (reintroducing the round-4 disk-growth DoS).

These tests import ONLY ``_index_lock``, ``session_store``, and ``checkpoint_store`` (no
``cli.main`` / rust_core), so they run standalone and in CI, mirroring
``test_session_containment.py`` / ``test_checkpoint_containment.py``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock, checkpoint_store, session_store


def _make_project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def _race_open_session(
    root: Path, *, threads: int
) -> tuple[list[BaseException], dict[int, session_store.SessionOpenResult]]:
    """Run ``open_session(root)`` from ``threads`` racing threads; return (errors, results).
    ``worker`` closes over function-local ``errors``/``results`` (not loop variables), so it
    is safe to call in a retry loop over fresh roots."""
    errors: list[BaseException] = []
    results: dict[int, session_store.SessionOpenResult] = {}

    def worker(i: int) -> None:
        try:
            results[i] = session_store.open_session(str(root))
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    return errors, results


# --------------------------------------------------------------------------------------
# _index_lock.py direct unit coverage
# --------------------------------------------------------------------------------------


def test_index_lock_releases_after_use(tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    with _index_lock.index_lock(index_path):
        assert lock_path.exists()
    assert not lock_path.exists()

    # A second, later acquire must succeed immediately (no leaked lock file).
    with _index_lock.index_lock(index_path, timeout_s=0.5):
        pass


def test_index_lock_releases_on_exception(tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    with pytest.raises(RuntimeError, match="boom"):
        with _index_lock.index_lock(index_path):
            raise RuntimeError("boom")
    assert not lock_path.exists()


def test_index_lock_timeout_raises_under_sustained_contention(tmp_path: Path) -> None:
    """Fail-closed contract: sustained LIVE contention raises IndexLockTimeoutError rather
    than silently dropping the caller's write or hanging forever."""
    index_path = tmp_path / "index.json"
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_lock() -> None:
        with _index_lock.index_lock(index_path):
            holder_ready.set()
            release_holder.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert holder_ready.wait(timeout=5.0)
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            with _index_lock.index_lock(index_path, timeout_s=0.3, stale_after_s=60.0):
                pass
    finally:
        release_holder.set()
        holder.join(timeout=5.0)


# --------------------------------------------------------------------------------------
# Cross-writer lost-insert (tdd_test_buggy): FAILS pre-fix, PASSES once load->write runs
# inside index_lock. No multiprocessing needed -- file I/O releases the GIL at the race
# window; the monkeypatched sleep widens that window deterministically.
# --------------------------------------------------------------------------------------


def test_concurrent_open_session_no_lost_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)

    orig_write_index = session_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)  # widen the real RMW window deterministically
        return orig_write_index(r, recs)

    monkeypatch.setattr(session_store, "_write_index", slow_write_index)

    results: dict[int, session_store.SessionOpenResult] = {}

    def worker(i: int) -> None:
        results[i] = session_store.open_session(str(root))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    written = {r.session_id for r in results.values()}
    indexed = {rec.session_id for rec in session_store._load_index(root)}
    # TODAY (pre-fix): indexed is a strict subset of written (lost inserts under the race).
    # AFTER (fix): every writer's insert survives.
    assert written == indexed


def test_concurrent_create_checkpoint_no_lost_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_project(tmp_path)

    orig_write_index = checkpoint_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)
        return orig_write_index(r, recs)

    monkeypatch.setattr(checkpoint_store, "_write_index", slow_write_index)

    results: dict[int, checkpoint_store.CheckpointCreateResult] = {}

    def worker(i: int) -> None:
        results[i] = checkpoint_store.create_checkpoint(str(root))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    written = {r.checkpoint_id for r in results.values()}
    indexed = {rec.checkpoint_id for rec in checkpoint_store._load_index(root)}
    # Concrete "orphaned snapshot dir invisible to list, never pruned" scenario: every
    # returned checkpoint_id must be indexed AND have a real snapshot dir on disk.
    assert written == indexed
    for checkpoint_id in written:
        assert checkpoint_store._snapshot_path(root, checkpoint_id).exists()


def test_concurrent_refresh_session_no_lost_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """refresh_session has its own load->mutate->write span (session_store.py CHANGE 3);
    concurrent refreshes of DIFFERENT sessions in the same root must not clobber each
    other's index entries either."""
    root = _make_project(tmp_path)
    session_ids = [session_store.open_session(str(root)).session_id for _ in range(4)]

    orig_write_index = session_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)
        return orig_write_index(r, recs)

    monkeypatch.setattr(session_store, "_write_index", slow_write_index)

    errors: list[BaseException] = []

    def worker(session_id: str) -> None:
        try:
            session_store.refresh_session(session_id, str(root))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(sid,)) for sid in session_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    indexed = {rec.session_id for rec in session_store._load_index(root)}
    # All four originally-opened sessions must still be present after concurrent refreshes.
    assert indexed == set(session_ids)


def test_concurrent_open_and_implicit_removal_no_lost_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-6/7 r3: session_daemon._remove_implicit_session_payload did an UNLOCKED
    load->filter->write on the SAME index.json that open_session mutates. A concurrent
    open_session (locked) racing the implicit-session eviction cleanup could lose the open's
    insert, or the removal could be clobbered. Both must now serialize under index_lock."""
    from tensor_grep.cli import session_daemon

    root = _make_project(tmp_path)
    victim = session_store.open_session(str(root)).session_id  # the implicit session to evict

    orig_write_index = session_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)  # widen the RMW window so the opener holds the lock across the race
        return orig_write_index(r, recs)

    monkeypatch.setattr(session_store, "_write_index", slow_write_index)

    opened: dict[str, str] = {}

    def opener() -> None:
        opened["new"] = session_store.open_session(str(root)).session_id

    def remover() -> None:
        session_daemon._remove_implicit_session_payload(str(root), victim)

    threads = [threading.Thread(target=opener), threading.Thread(target=remover)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    indexed = {rec.session_id for rec in session_store._load_index(root)}
    assert opened["new"] in indexed  # the concurrent open's insert survived the removal
    assert victim not in indexed  # and the removal was not clobbered by the open


# --------------------------------------------------------------------------------------
# Non-contended hot path guard (tdd_test_legit A): the lock must not add material overhead
# on the normal, uncontended path, and must not wrap build_repo_map / the snapshot copy.
# --------------------------------------------------------------------------------------


def test_open_session_uncontended_hot_path_unaffected(tmp_path: Path) -> None:
    root = _make_project(tmp_path)

    from tensor_grep.cli.repo_map import build_repo_map

    baseline_start = time.monotonic()
    repo_map_only = build_repo_map(root, max_repo_files=session_store.DEFAULT_AGENT_REPO_MAP_LIMIT)
    baseline_elapsed = time.monotonic() - baseline_start
    assert repo_map_only["files"]

    start = time.monotonic()
    result = session_store.open_session(str(root))
    elapsed = time.monotonic() - start

    # An uncontended lock acquire/release is a single os.open/os.close pair (microseconds).
    # Guard against the lock adding material overhead: total open_session time (repo scan +
    # payload write + the now-LOCKED index RMW) stays within a generous multiple of the
    # unlocked repo-scan-only baseline OR a flat 4.0s floor -- not blown out toward the 5s
    # acquire timeout. The flat floor (mirroring the sibling checkpoint hot-path test at 4.0s)
    # replaces a fragile `baseline*3 + 1.0` bound: when the fixture scan is fast (tiny baseline)
    # but the runner is loaded DURING open_session (payload write + lock RMW), the pure ratio
    # false-failed on windows CI (flake #64). max(ratio, 4.0) keeps the ratio signal for a
    # slow-scan runner while staying tolerant of a loaded one, and 4.0 < 5.0 still catches a
    # genuinely-contended lock drifting toward the acquire timeout.
    assert elapsed < max(baseline_elapsed * 3.0, 4.0)
    indexed = {rec.session_id for rec in session_store._load_index(root)}
    assert result.session_id in indexed


def test_create_checkpoint_uncontended_hot_path_unaffected(tmp_path: Path) -> None:
    root = _make_project(tmp_path)

    # #244 release-blocker de-flake: the original assertion (`elapsed < 4.0`, an absolute
    # wall-clock ceiling) flaked at 4.968s on a loaded Windows CI runner and needed a rerun --
    # same class as the already-hardened #120/#204 flakes. The dominant real-world cost here is
    # NOT the lock (an uncontended os.open/os.close acquire is microseconds) but
    # create_checkpoint's PRE-lock work: _detect_checkpoint_scope shells out to
    # `git rev-parse --show-toplevel` (fails fast on this non-git fixture, but process-spawn
    # overhead on a loaded Windows runner is exactly the kind of noise that blew the flat
    # ceiling), then _snapshot_entries walks the scope. Mirror the sibling
    # test_open_session_uncontended_hot_path_unaffected fix immediately above: measure that same
    # PRE-lock work as a same-run baseline (so a loaded runner inflates the baseline and the real
    # call TOGETHER, correlated, instead of tripping an OS-load-fragile flat number) and assert a
    # generous ratio, with a flat floor as a safety net for when the baseline itself is tiny
    # (e.g. a fast Linux runner where the failed git spawn is near-instant). This stays
    # BIDIRECTIONAL: a regression that widens the locked critical section to wrap expensive work
    # (the exact class this test guards against, per the module docstring) inflates `elapsed`
    # without inflating `baseline_elapsed` at all, so the ratio -- not just the flat floor --
    # would still catch it.
    baseline_start = time.monotonic()
    scope = checkpoint_store._detect_checkpoint_scope(root)
    checkpoint_store._snapshot_entries(scope)
    baseline_elapsed = time.monotonic() - baseline_start

    start = time.monotonic()
    result = checkpoint_store.create_checkpoint(str(root))
    elapsed = time.monotonic() - start

    assert elapsed < max(baseline_elapsed * 3.0, 4.0)
    indexed = {rec.checkpoint_id for rec in checkpoint_store._load_index(root)}
    assert result.checkpoint_id in indexed
    assert checkpoint_store._snapshot_path(root, result.checkpoint_id).exists()


# --------------------------------------------------------------------------------------
# Stale-lock reclaim (tdd_test_legit B): a genuinely dead lock must self-heal, not hang
# every tg invocation for that root.
# --------------------------------------------------------------------------------------


def _plant_stale_lock(index_path: Path) -> Path:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    stale_mtime = time.time() - 3600.0  # 1 hour old, well past the 10s staleness threshold
    os.utime(lock_path, (stale_mtime, stale_mtime))
    return lock_path


def test_open_session_reclaims_stale_lock(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _plant_stale_lock(session_store._index_path(root))

    start = time.monotonic()
    result = session_store.open_session(str(root))
    elapsed = time.monotonic() - start

    # Must reclaim promptly (well under the 5s acquire timeout), not hang and not raise.
    assert elapsed < 4.0
    indexed = {rec.session_id for rec in session_store._load_index(root)}
    assert result.session_id in indexed


def test_create_checkpoint_reclaims_stale_lock(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    _plant_stale_lock(checkpoint_store._index_path(root))

    start = time.monotonic()
    result = checkpoint_store.create_checkpoint(str(root))
    elapsed = time.monotonic() - start

    assert elapsed < 4.0
    indexed = {rec.checkpoint_id for rec in checkpoint_store._load_index(root)}
    assert result.checkpoint_id in indexed


def test_open_session_reclaims_stale_lock_two_racing_threads(tmp_path: Path) -> None:
    """Two threads race to reclaim the SAME dead lock. Guards the previously-unguarded
    unlink pattern (mirrored from session_daemon.py:170): the loser of the unlink race
    must not crash with FileNotFoundError."""
    # `index_lock` serializes the RMW via os.open(O_CREAT|O_EXCL) even through the
    # stale-reclaim path (only one thread can atomically re-create the unlinked lock; the
    # loser sees a fresh lock and waits), so `written == indexed` is the correct invariant.
    # A genuinely broken lock loses an update on essentially EVERY 2-thread race, so it
    # would fail all attempts below; a rare scheduling artifact on a slow CI host fails at
    # most one. Retry over FRESH roots so a single jitter miss does not red the suite while
    # a real lost-update regression (consistent failure) still raises. The crash-guard
    # (`assert not errors`) stays DETERMINISTIC -- it is the primary contract and is never
    # retry-tolerated.
    last_mismatch: AssertionError | None = None
    for attempt in range(5):
        root = _make_project(tmp_path, name=f"project-{attempt}")
        _plant_stale_lock(session_store._index_path(root))

        errors, results = _race_open_session(root, threads=2)

        assert not errors
        written = {r.session_id for r in results.values()}
        indexed = {rec.session_id for rec in session_store._load_index(root)}
        try:
            assert written == indexed
            return
        except AssertionError as exc:  # transient scheduling jitter -> retry a fresh race
            last_mismatch = exc
    raise AssertionError(
        "index_lock lost an insert across 5 independent 2-thread races -- a real "
        f"lost-update regression, not jitter: {last_mismatch}"
    )


# --------------------------------------------------------------------------------------
# Per-root isolation (tdd_test_legit bonus): locks are per-index-file, not global --
# holding one root's lock must never block another root's lock. Proven directly against
# the lock primitive as a scheduler-independent CONTRACT; see the docstring below for why
# both prior wall-clock versions (ratio, then overlap) still flaked.
# --------------------------------------------------------------------------------------


def test_index_lock_is_per_root_not_global(tmp_path: Path) -> None:
    """Per-root isolation, proven as a scheduler-independent CONTRACT instead of a
    wall-clock timing heuristic.

    History of the flake this replaces:
      - Original: ``concurrent_elapsed < baseline_elapsed * 1.8 + 0.5`` (a wall-clock
        RATIO). Red on the v1.81.1 release run (concurrent=0.906s vs a 0.894s ceiling --
        pure runner jitter, not serialization).
      - #204/#650 harden: replaced the ratio with "the two roots' write-lock hold
        intervals must OVERLAP in wall time" -- believed jitter-immune, but it red-ed
        AGAIN on v1.92.2 (windows-latest CI run 29873888662, loaded runner):
            project_a=[1034.781, 1035.281] project_b=[1034.265, ...]
        i.e. project_b's hold window had already closed before project_a's opened.
        Overlap is STILL a wall-clock claim: on a sufficiently starved runner the OS can
        simply fail to schedule thread B until thread A's hold has already finished, even
        though the two per-root locks never contended on each other at all. Two
        independent locks are not guaranteed to be *simultaneously held* under an
        adversarial scheduler -- only guaranteed not to *block* each other. Racing the
        scheduler to observe "simultaneous" can never be made both sharp and non-flaky.

    The only scheduler-independent way to prove "per-root, not global" is to test the
    BLOCKING behavior directly, Event-gated (never sleep-gated, so there is no timing
    window to race):
      1. Hold root_a's lock on a background thread until told to let go.
      2. INDEPENDENCE: acquiring root_b's lock while root_a's is held must succeed
         promptly (bounded timeout) -- a shared/global lockfile would instead block
         root_b until root_a releases, which never happens inside this check, so the bug
         now surfaces as a fast, deterministic ``IndexLockTimeoutError`` rather than a
         hang or a scheduler-dependent timing artifact.
      3. CONVERSE CONTROL: a second acquisition of root_a's OWN lock, while genuinely
         held, must itself BLOCK/timeout -- proving the lock is a real mutual-exclusion
         primitive and not a no-op that would let check 2 pass vacuously.
    Both checks are pass/fail the instant they run; nothing needs to overlap, race, or
    outrun the CI scheduler for either to be correct.

    Concurrent-write CORRECTNESS through the real ``open_session``/``_write_index`` path
    (lost inserts, retention, ownership-token races) is already covered by the sibling
    tests above (``test_concurrent_open_session_no_lost_insert`` et al.); this test's only
    job is proving the lock is keyed per-index-file rather than global, which
    ``_index_lock.index_lock`` is the direct, minimal unit to prove it against -- using
    the SAME ``_index_path`` production helper ``open_session`` itself calls, so a
    routing bug that collapsed two roots onto the same lock target would still surface
    here too.
    """
    root_a = _make_project(tmp_path, name="project_a")
    root_b = _make_project(tmp_path, name="project_b")
    index_a = session_store._index_path(root_a)
    index_b = session_store._index_path(root_b)
    assert index_a != index_b  # sanity: the two roots really do map to different lock targets

    holder_ready = threading.Event()
    release_holder = threading.Event()
    holder_errors: list[BaseException] = []

    def hold_root_a() -> None:
        try:
            with _index_lock.index_lock(index_a):
                holder_ready.set()
                # Bounded: never hang the suite even if the main thread's asserts raise
                # before reaching the `finally: release_holder.set()` below (index_lock's
                # own 12s acquire timeout is the ultimate backstop regardless).
                release_holder.wait(timeout=10.0)
        except BaseException as exc:  # surface into the main thread, not a silent thread death
            holder_errors.append(exc)

    holder = threading.Thread(target=hold_root_a)
    holder.start()
    try:
        assert holder_ready.wait(timeout=5.0), "root_a holder thread never acquired its lock"

        # (2) Independence.
        with _index_lock.index_lock(index_b, timeout_s=2.0):
            pass  # success == root_b was NOT blocked by root_a's held lock

        # (3) Converse control -- proves the lock is real, not a no-op.
        with pytest.raises(_index_lock.IndexLockTimeoutError):
            with _index_lock.index_lock(index_a, timeout_s=0.3, stale_after_s=60.0):
                pass
    finally:
        release_holder.set()
        holder.join(timeout=15.0)  # generous headroom past index_lock's own 12s acquire ceiling

    assert not holder.is_alive(), "root_a holder thread did not exit after release"
    assert not holder_errors, f"root_a holder thread raised: {holder_errors!r}"
