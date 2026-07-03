from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_POLL_S = 0.02
_TIMEOUT_S = 5.0  # RMW of a bounded JSON index is sub-ms; generous headroom, not the hot path
_STALE_AFTER_S = 10.0  # RMW-scaled, NOT daemon-launch-scaled


class IndexLockTimeoutError(RuntimeError):
    """Fail-closed per AGENTS.md Backend Fail-Closed Contract: silently losing an index
    entry is worse than a rare, actionable error. A genuinely dead lock is reclaimed via
    mtime staleness, so this only fires under sustained LIVE contention."""


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
            try:
                if time.time() - lock_path.stat().st_mtime > stale_after_s:
                    try:
                        lock_path.unlink()  # GUARDED: two racing reclaimers must not crash the loser
                    except OSError:
                        pass
                    continue
            except OSError:
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
