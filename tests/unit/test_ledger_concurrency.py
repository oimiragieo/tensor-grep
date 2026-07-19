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


def test_claim_index_lock_is_per_root_not_global(tmp_path: Path, monkeypatch) -> None:
    """Two DIFFERENT roots must not serialize against each other's claims lock -- mirrors
    session_store's own per-root isolation guard, causally proven via overlapping
    write-hold intervals rather than a flaky wall-clock ratio."""
    root_a = _make_project(tmp_path, name="project_a")
    root_b = _make_project(tmp_path, name="project_b")

    orig_write_index = ledger_store._write_index
    HOLD_SECONDS = 0.4
    intervals: dict[str, tuple[float, float]] = {}
    intervals_lock = threading.Lock()

    def slow_write_index(r: Path, recs: list) -> None:
        enter = time.monotonic()
        time.sleep(HOLD_SECONDS)
        leave = time.monotonic()
        with intervals_lock:
            intervals[r.name] = (enter, leave)
        return orig_write_index(r, recs)

    monkeypatch.setattr(ledger_store, "_write_index", slow_write_index)

    ready = threading.Barrier(2)

    def worker(root: Path) -> None:
        ready.wait(timeout=10)
        ledger_store.submit_claim(str(root), symbols=["value"], agent_id="agent-a")

    threads = [
        threading.Thread(target=worker, args=(root_a,)),
        threading.Thread(target=worker, args=(root_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert "project_a" in intervals and "project_b" in intervals
    a_enter, a_leave = intervals["project_a"]
    b_enter, b_leave = intervals["project_b"]
    assert a_enter < b_leave and b_enter < a_leave, (
        "per-root ledger locks serialized (write-hold intervals did not overlap): "
        f"project_a=[{a_enter:.3f}, {a_leave:.3f}] project_b=[{b_enter:.3f}, {b_leave:.3f}]"
    )


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
