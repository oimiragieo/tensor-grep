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
    threshold before the waiter's deadline fires. Post-fix ratio (timeout_s=1.2 >
    stale_after_s=1.0) guarantees reclaim: the lock reaches staleness at age=1.0 (elapsed
    0.7s from the waiter's start), comfortably inside the 1.2s deadline.
    """
    index_path = tmp_path / "index.json"
    lock_path = _index_lock._lock_path_for(index_path)
    lock_path.write_text("99999\n", encoding="utf-8")
    fresh_mtime = time.time() - 0.3  # "crashed" 0.3s ago: well under stale_after_s (1.0s)
    os.utime(lock_path, (fresh_mtime, fresh_mtime))

    start = time.monotonic()
    with _index_lock.index_lock(index_path, timeout_s=1.2, stale_after_s=1.0):
        pass
    elapsed = time.monotonic() - start

    assert elapsed < 1.2
    assert not lock_path.exists()
