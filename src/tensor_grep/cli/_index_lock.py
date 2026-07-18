from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
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


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    """Write ``data`` to ``path`` atomically, refusing to write through a pre-existing symlink.

    The ONE shared hardening baseline (audit C4 / CWE-59, PR #659; uniformized across every
    sibling writer by task #211) for every JSON/key/manifest writer in the ``cli`` package: an
    ``is_symlink()`` refusal precheck on the PRE-resolve destination, a same-directory temp file,
    POSIX ``O_CREAT|O_EXCL`` plus ``O_NOFOLLOW`` where the platform has it, an ``fsync`` of the
    written bytes before the rename, and :func:`replace_with_retry` (``os.replace``) to publish.

    Why the precheck AND the rename-based swap: ``os.replace`` never dereferences a destination
    symlink -- POSIX ``rename()`` atomically replaces the link ENTRY itself, never the file it
    points to -- so this function can never be tricked into corrupting an arbitrary symlink
    TARGET through the publish step alone. But without the precheck it would still silently
    destroy a pre-existing symlink at the destination with no signal that something unexpected was
    already there; refusing outright is the same fail-closed posture
    ``evidence_signing._write_private_key_atomic`` has always taken (the original C4 fix), now
    uniform across every sibling writer instead of copy-pasted (or, in three sites, MISSING)
    per-module.

    ``O_NOFOLLOW`` is a documented no-op on Windows (``getattr(os, "O_NOFOLLOW", 0)`` mirrors both
    cpython's own ``tempfile`` module and this codebase's established
    ``main._write_json_refuse_symlink`` idiom) -- it is belt-and-suspenders defense-in-depth
    against a symlink swapped in at the randomly-named temp path itself (astronomically unlikely,
    since the name includes a fresh ``uuid4``), not the cross-platform guard. The cross-platform
    guard is the precheck + same-directory-temp + rename shape, which holds identically on POSIX
    and Windows -- do NOT treat ``O_NOFOLLOW`` alone as sufficient hardening on this codebase's
    primary (Windows) development platform.

    Callers that first ``.expanduser().resolve()`` a caller-supplied path MUST check
    ``is_symlink()`` on the PRE-resolve path themselves before calling this function (mirrors
    ``evidence_signing.generate_keypair``) -- ``.resolve()`` follows symlinks, so by the time a
    resolved path reaches here the symlink-ness of the ORIGINAL destination is already lost. This
    function's own check remains as defense-in-depth against a symlink planted directly at an
    already-resolved leaf path (the common case for internal callers that never round-trip through
    a caller-supplied string) and against the narrow TOCTOU window between an outer check and this
    call.

    ``mode``, when given, is applied to the temp file at creation (honoring the umask) and
    re-asserted with ``os.chmod`` afterward for determinism (in case the umask stripped a bit);
    when ``None`` the temp file is created at the same effective permissions plain ``open()``
    would use (``0o666`` masked by umask).

    Raises ``OSError`` if ``path`` is already a symlink; propagates any other write/rename failure
    (Backend Fail-Closed Contract -- never a silent partial write).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to write through a symlink: {path}")
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    create_mode = 0o666 if mode is None else mode
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp_path, flags, create_mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            # M6: fsync the data before the rename so a crash can never publish a truncated
            # file (mirrors session_store._write_json_atomic, audit I5).
            os.fsync(handle.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # don't leave a partial temp behind
        raise
    if mode is not None:
        # O_CREAT honors the umask, so the created mode is never broader than `mode`; force the
        # exact requested bits for determinism (in case the umask stripped an owner bit).
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
    replace_with_retry(tmp_path, path)
    # Best-effort durability of the rename itself; directory fsync is a no-op or unsupported on
    # Windows, so failures here are non-fatal.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def atomic_write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    """``json.dumps(payload, indent=2)`` convenience wrapper over :func:`atomic_write_bytes`.

    Shared by every caller whose serialization is exactly ``json.dumps(payload, indent=2)`` (no
    ``sort_keys``, no trailing newline) -- currently ``session_store``/``checkpoint_store``/
    ``audit_manifest``'s index/metadata writers. A caller with different serialization (e.g.
    ``dogfood``'s ``sort_keys=True`` + trailing newline) calls :func:`atomic_write_bytes` directly
    with its own precomputed bytes instead, so its on-disk output stays byte-for-byte unchanged.
    """
    atomic_write_bytes(path, json.dumps(payload, indent=2).encode("utf-8"), mode=mode)


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
