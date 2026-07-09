from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

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


def _token_for_lock(lock_path: Path) -> str | None:
    """Read back the ownership token written by ``index_lock`` on acquire (the second line
    of ``{pid}\\n{token}\\n``). Returns ``None`` if the file is gone, unreadable, or lacks a
    token line (e.g. a legacy/malformed lock content in an existing test fixture) -- callers
    treat ``None`` as "not mine", never as a match."""
    try:
        content = lock_path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = content.splitlines()
    if len(lines) < 2:
        return None
    token = lines[1].strip()
    return token or None


def _release_lock(lock_path: Path, token: str) -> None:
    """audit #14 ownership-token backstop: unlink ``lock_path`` ONLY if it still carries
    ``token`` (i.e. this instance still owns it). If a waiter reclaimed the lock as stale
    while this holder was still slow-but-alive, the token on disk no longer matches --
    leave that live lock alone instead of deleting it out from under the new owner
    (the lost-update / two-holders race). Tolerates the lock already being gone
    (``FileNotFoundError``) and the Windows delete-pending ``PermissionError``.

    Known residual (theoretical, accepted): there is a sub-millisecond read-then-unlink
    window -- if a waiter stale-reclaims between the token read below and the ``unlink``,
    this ``unlink`` could delete the new owner's file. It is not portably closable (no
    filesystem compare-and-delete primitive) and is rendered practically unreachable by
    the mtime heartbeat, which keeps this holder's lock fresh (age << the 10s stale
    threshold) right up until release -- so a waiter cannot observe staleness during the
    release window unless this process is actually dead. This is strictly smaller than the
    pre-fix unconditional ``unlink`` it replaces."""
    if _token_for_lock(lock_path) != token:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def _default_heartbeat_interval_s(stale_after_s: float, poll_interval_s: float) -> float:
    # Well under stale_after_s so a live-but-slow holder's mtime never crosses the
    # staleness threshold between beats; floored at poll_interval_s so a tiny custom
    # stale_after_s (tests) can't drive this to ~0 and busy-loop the heartbeat thread.
    return max(poll_interval_s, stale_after_s / 3.0)


def _heartbeat_loop(lock_path: Path, token: str, stop: threading.Event, interval_s: float) -> None:
    """audit #14 primary defense: while a section is long-held, periodically touch the
    lockfile's mtime so a concurrent waiter's staleness check never sees a live holder as
    dead (preventing the false-stale reclaim race before it starts -- the token guard in
    ``_release_lock`` is only the backstop for if it still happens). Re-checks ownership
    every beat before touching mtime so a heartbeat thread that outlives its own lock (e.g.
    release already ran, or -- defensively -- someone else reclaimed) never props up a
    DIFFERENT holder's lock."""
    while not stop.wait(interval_s):
        if _token_for_lock(lock_path) != token:
            return  # no longer ours -- stop, do not touch whatever/whoever is there now
        try:
            os.utime(lock_path, None)
        except OSError:
            pass  # transient (e.g. Windows delete-pending); next beat retries


@contextmanager
def index_lock(
    index_path: Path,
    *,
    poll_interval_s: float = _POLL_S,
    timeout_s: float = _TIMEOUT_S,
    stale_after_s: float = _STALE_AFTER_S,
    heartbeat_interval_s: float | None = None,
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
    # audit #14: a uuid4 ownership token (not just the pid, which can collide across a
    # crash+relaunch) identifies THIS acquisition. Written alongside the pid so a stale
    # legacy/pid-only lock (no second line) is still tolerated by `_token_for_lock`.
    token = uuid4().hex
    hb_interval = (
        heartbeat_interval_s
        if heartbeat_interval_s is not None
        else _default_heartbeat_interval_s(stale_after_s, poll_interval_s)
    )
    try:
        try:
            os.write(fd, f"{os.getpid()}\n{token}\n".encode())
        finally:
            os.close(fd)
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(lock_path, token, stop_heartbeat, hb_interval),
            daemon=True,
        )
        heartbeat.start()
        try:
            yield
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=1.0)  # bounded: never hang release on a wedged thread
    finally:
        _release_lock(lock_path, token)
