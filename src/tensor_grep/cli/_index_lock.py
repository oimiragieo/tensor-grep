from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_POLL_S = 0.02
_STALE_AFTER_S = 10.0  # RMW-scaled, NOT daemon-launch-scaled
# H9: must exceed _STALE_AFTER_S. A holder killed mid-write can leave a lock younger than
# _STALE_AFTER_S at the moment a waiter starts polling; if the waiter's own deadline could
# expire first (the old 5.0s < 10.0s split), that fresh-but-dead lock would NEVER be
# reclaimed within the wait window -- every waiter raises IndexLockTimeoutError instead of
# self-healing. Keeping timeout > stale guarantees any lock already past (or about to pass)
# the staleness threshold is reclaimed before a waiter gives up.
_TIMEOUT_S = 12.0  # RMW of a bounded JSON index is sub-ms; generous headroom, not the hot path


class IndexLockTimeoutError(RuntimeError):
    """Fail-closed per AGENTS.md Backend Fail-Closed Contract: silently losing an index
    entry is worse than a rare, actionable error. A genuinely dead lock is reclaimed via
    mtime staleness, so this only fires under sustained LIVE contention."""


def replace_with_retry(
    src: str | Path, dst: str | Path, *, attempts: int = 10, delay_s: float = 0.02
) -> None:
    """``os.replace`` retried on the Windows-only transient ``PermissionError`` (WinError 5) that
    fires when the destination is momentarily held open by a concurrent reader / AV scanner / the
    search indexer. On POSIX ``os.replace`` is atomic and never raises this, so the retry is a
    no-op there. Fails CLOSED: re-raises the last error after ``attempts`` rather than leaving a
    stale index (Backend Fail-Closed Contract)."""
    src_s, dst_s = str(src), str(dst)
    for attempt in range(attempts):
        try:
            os.replace(src_s, dst_s)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def _lock_path_for(index_path: Path) -> Path:
    # dot-prefixed + .lock suffix: never matched by checkpoint index discovery (rglob of the
    # literal 'index.json', checkpoint_store.py:808-809) nor any '*.json' session glob.
    return index_path.with_name(f".{index_path.name}.lock")


@contextmanager
def index_lock(
    index_path: Path,
    *,
    poll_interval_s: float = _POLL_S,
    timeout_s: float = _TIMEOUT_S,
    stale_after_s: float = _STALE_AFTER_S,
) -> Iterator[None]:
    lock_path = _lock_path_for(index_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Lock is held. Reclaim it if stale (dead holder), else fall through to wait.
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after_s:
                    try:
                        lock_path.unlink()  # GUARDED: two racing reclaimers must not crash the loser
                    except OSError:
                        pass
                    continue
            except OSError:
                pass
        except PermissionError:
            # Windows delete-pending race: a concurrent reclaimer just unlink()'d the lock, so the
            # name is in a "delete pending" state and O_CREAT|O_EXCL raises ERROR_ACCESS_DENIED
            # (PermissionError) instead of the POSIX FileExistsError/ENOENT. Transient -> fall
            # through to wait/retry. A genuine permission error self-limits: it will keep failing
            # here and fail CLOSED at the deadline with IndexLockTimeoutError, never a raw leak.
            pass
        if time.monotonic() >= deadline:
            raise IndexLockTimeoutError(
                f"could not acquire {lock_path} within {timeout_s}s"
            ) from None
        time.sleep(poll_interval_s)
    try:
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
        finally:
            os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
