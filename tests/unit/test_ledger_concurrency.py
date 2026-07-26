"""Concurrency regression tests for ``tg ledger``'s claims ``index.json`` RMW.

Mirrors ``test_index_lock_concurrency.py``'s ``test_concurrent_open_session_no_lost_insert``
shape exactly: ``ledger_store.submit_claim`` does ``_load_index`` -> mutate -> ``_write_index``
under ``index_lock``, same as ``session_store.open_session``. Widen the real RMW race window
deterministically by monkeypatching ``_write_index`` to sleep, then race N threads and assert
every thread's claim survives on disk (no lost insert).

Imports ONLY ``_index_lock`` and ``ledger_store`` (no ``cli.main`` / rust_core), so this runs
standalone and in CI, mirroring the sibling session-store concurrency file.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tensor_grep.cli import _index_lock, ledger_store


def _make_project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "mod.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    return root


def test_concurrent_claim_no_lost_insert(tmp_path: Path, monkeypatch) -> None:
    """Two-writer race on submit_claim: every claim_id returned to a caller must survive on
    disk. Pre-fix (a bare load->mutate->write with no lock) this loses inserts under a
    widened race window; post-fix (RMW under index_lock, mirroring session_store) it must
    not, mo matter how many threads race."""
    root = _make_project(tmp_path)

    orig_write_index = ledger_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)  # widen the real RMW window deterministically
        return orig_write_index(r, recs)

    monkeypatch.setattr(ledger_store, "_write_index", slow_write_index)

    results: dict[int, dict] = {}
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            results[i] = ledger_store.submit_claim(
                str(root), symbols=[f"sym{i}"], agent_id=f"agent-{i}"
            )
        except BaseException as exc:  # captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(results) == 4
    returned_ids = {result["claim"]["claim_id"] for result in results.values()}
    indexed_ids = {entry["claim_id"] for entry in ledger_store.list_claims(str(root))["claims"]}
    # TODAY (post-fix, index_lock-protected): every writer's insert survives.
    assert returned_ids == indexed_ids


def test_concurrent_claim_and_release_no_lost_state(tmp_path: Path, monkeypatch) -> None:
    """A release racing a claim on the SAME root must not clobber the other's index update:
    the claim from the opener survives, and the pre-existing claim targeted for release is
    actually gone afterward."""
    root = _make_project(tmp_path)
    victim = ledger_store.submit_claim(str(root), symbols=["victim"], agent_id="agent-victim")
    victim_id = victim["claim"]["claim_id"]

    orig_write_index = ledger_store._write_index

    def slow_write_index(r: Path, recs: list) -> None:
        time.sleep(0.05)
        return orig_write_index(r, recs)

    monkeypatch.setattr(ledger_store, "_write_index", slow_write_index)

    outcome: dict[str, dict] = {}

    def claimer() -> None:
        outcome["claimed"] = ledger_store.submit_claim(
            str(root), symbols=["new-symbol"], agent_id="agent-new"
        )

    def releaser() -> None:
        outcome["released"] = ledger_store.release_claim(str(root), claim_id=victim_id)

    threads = [threading.Thread(target=claimer), threading.Thread(target=releaser)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcome["released"]["released_count"] == 1
    live_ids = {entry["claim_id"] for entry in ledger_store.list_claims(str(root))["claims"]}
    assert outcome["claimed"]["claim"]["claim_id"] in live_ids  # the concurrent claim survived
    assert victim_id not in live_ids  # and the removal was not clobbered by the claim


def test_claim_index_lock_is_per_root_not_global(tmp_path: Path) -> None:
    """Per-root isolation for the CLAIMS lock, proven as a scheduler-independent contract.

    This is the ledger twin of ``test_index_lock_concurrency.py``'s
    ``test_index_lock_is_per_root_not_global``, and it inherits that test's history rather
    than repeating it. The session-store side went ratio -> overlap -> Event-gated; the
    overlap form was retired there because it red-ed on a loaded runner. This file kept the
    retired form and duly red-ed the same way on main (windows-latest py3.12, CI run
    30194572764)::

        project_a=[1396.734, 1397.125] project_b=[1397.281, 1397.687]

    Thread B simply had not been scheduled into the instrumented write section until 0.156s
    after thread A left it. Nothing was serialized -- the two locks never contended at all.
    Overlap is a wall-clock claim, and two independent locks are only guaranteed not to
    BLOCK each other; they are never guaranteed to be *simultaneously held* under an
    adversarial scheduler. That is why the fix is not a bigger sleep or a looser window:
    racing the scheduler to observe "simultaneous" cannot be made both sharp and non-flaky.

    So test the BLOCKING behaviour directly, Event-gated (never sleep-gated, so there is no
    timing window to race):
      1. Hold root_a's claims lock on a background thread until told to let go.
      2. INDEPENDENCE: acquiring root_b's claims lock meanwhile must succeed promptly. A
         shared/global lockfile would block root_b until root_a releases -- which never
         happens inside this check -- so the bug surfaces as a fast, deterministic
         ``IndexLockTimeoutError`` instead of a scheduler-dependent timing artifact.
      3. CONVERSE CONTROL: re-acquiring root_a's OWN lock while it is genuinely held must
         itself time out. Without this arm, check 2 would pass vacuously if the lock were a
         no-op, and the test could not tell a working per-root lock from no lock at all.
    """
    root_a = _make_project(tmp_path, name="project_a")
    root_b = _make_project(tmp_path, name="project_b")
    index_a = ledger_store._index_path(root_a)
    index_b = ledger_store._index_path(root_b)
    assert index_a != index_b  # sanity: the two roots really do map to different lock targets

    holder_ready = threading.Event()
    release_holder = threading.Event()
    holder_errors: list[BaseException] = []

    def hold_root_a() -> None:
        try:
            with _index_lock.index_lock(index_a):
                holder_ready.set()
                # Bounded: never hang the suite if the main thread's asserts raise before
                # reaching the `finally: release_holder.set()` below.
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
        holder.join(timeout=15.0)

    assert not holder.is_alive(), "root_a holder thread did not exit after release"
    assert not holder_errors, f"root_a holder thread raised: {holder_errors!r}"


def test_claim_reclaims_stale_lock(tmp_path: Path) -> None:
    """A genuinely dead lock (holder crashed) must self-heal, not hang every claim for that
    root forever -- mirrors session_store's stale-lock reclaim guard."""
    import os

    root = _make_project(tmp_path)
    index_path = ledger_store._index_path(root)
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    stale_mtime = time.time() - 3600.0  # 1 hour old, well past the 10s staleness threshold
    os.utime(lock_path, (stale_mtime, stale_mtime))

    start = time.monotonic()
    result = ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")
    elapsed = time.monotonic() - start

    assert elapsed < 4.0  # reclaimed promptly, not hung toward the acquire timeout
    live_ids = {entry["claim_id"] for entry in ledger_store.list_claims(str(root))["claims"]}
    assert result["claim"]["claim_id"] in live_ids
