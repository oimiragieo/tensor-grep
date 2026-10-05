from __future__ import annotations

import dataclasses
import json
import os
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
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
    src: str | Path,
    dst: str | Path,
    *,
    attempts: int = 10,
    delay_s: float = 0.02,
    precheck: Callable[[], None] | None = None,
) -> None:
    """``os.replace`` retried on the Windows-only transient ``PermissionError`` (WinError 5) that
    fires when the destination is momentarily held open by a concurrent reader / AV scanner / the
    search indexer. On POSIX ``os.replace`` is atomic and never raises this, so the retry is a
    no-op there. Fails CLOSED: re-raises the last error after ``attempts`` rather than leaving a
    stale index (Backend Fail-Closed Contract). ``precheck`` (default none) runs before EACH
    attempt, so a retry sleep never widens an authorization window (it may raise to abort)."""
    src_s, dst_s = str(src), str(dst)
    for attempt in range(attempts):
        if precheck is not None:
            precheck()
        try:
            os.replace(src_s, dst_s)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    atomic_write_bytes_anchored(path, data, mode=mode, replace=True)


def _publish_bytes_no_clobber(src: Path, dst: Path) -> None:
    """Publish ``src`` into ``dst`` only if ``dst`` does not already exist.

    We intentionally do not use :func:`os.replace` here (which overwrites existing
    paths). On both major platforms, a same-directory hard-link publish fails atomically
    when the destination exists.
    """
    os.link(str(src), str(dst))


class WriteAuthorizationError(OSError):
    """An authorized write was refused: its target changed (or was never authorized) between the
    MCP artifact guard's approval and the publish."""


@dataclass(frozen=True)
class WriteAuthorization:
    """A check-time approval carried to the write (narrows the check-then-write window).

    ``path`` is the exact target string the guard authorized (absolute, normalized, NEVER
    re-resolved at write time). ``identity is None`` means the target was ABSENT when approved, so
    the publish must be no-clobber. Otherwise ``identity`` is the approved existing file's
    ``(st_dev, st_ino, st_size, st_mtime_ns)`` (``lstat``). ``parent_identity`` is the parent
    directory's ``(st_dev, st_ino)`` (``stat``, i.e. what the parent RESOLVES to); the write is
    refused if the parent now resolves elsewhere (symlink/junction swap) or the file changed.

    Residual R-11 (accepted, docs/audits/2026-10-03-bughunt-tracker.md): a sub-millisecond window
    between the final identity re-check and ``os.replace`` is not closed. Windows has no
    handle-relative conditional replace, and an attacker who can rename or replace files in the
    user's workspace can overwrite the target directly without tg. This guard defends against an
    agent being tricked by path naming, not against a concurrent filesystem adversary."""

    path: str
    identity: tuple[int, int, int, int] | None
    parent_identity: tuple[int, int] | None
    label: str = "target"
    # When the parent was ABSENT at approval: the nearest EXISTING ancestor and its identity. The
    # writer creates the missing components itself, under this ancestor, and refuses if it moved.
    ancestor: tuple[str, tuple[int, int]] | None = None


def _output_forms(auth: WriteAuthorization) -> tuple[str, str]:
    """(lexical, resolved) normalized forms; case-folded on Windows via ``normcase``."""
    lexical = _authorization_key(auth.path)
    return lexical, os.path.normcase(os.path.realpath(auth.path))


def _find_output_conflict(auths: list[WriteAuthorization]) -> str | None:
    """Return a refusal naming BOTH labels when two outputs are the same file (lexically, after
    resolution, or by identity of an existing file) or when one output's path is an ANCESTOR of
    another's (a file can never be the parent directory of a sibling output); else None."""
    forms = [_output_forms(a) for a in auths]
    for i, first in enumerate(auths):
        for j in range(i + 1, len(auths)):
            second = auths[j]
            pair = f"{first.label} and {second.label}"
            same = any(a == b for a in forms[i] for b in forms[j])
            if not same:
                try:
                    same = os.path.samefile(first.path, second.path)
                except OSError:
                    same = False  # at least one does not exist yet
            if same:
                return f"{pair} name the same output file (refused)"
            for x, y, parent, child in (
                (forms[i], forms[j], first, second),
                (forms[j], forms[i], second, first),
            ):
                if any(c.startswith(p + os.sep) for p in x for c in y):
                    return (
                        f"{parent.label} is a file path that is the parent directory of "
                        f"{child.label} (refused)"
                    )
    return None


class WriteScope:
    """One authorization scope (a context manager; see :func:`write_authorizations`).

    Beyond the authorizations it tracks, for a multi-output request: ``created_dirs`` (every
    directory this scope created, with its captured identity, so a LATER writer may reuse it but
    nobody else's), ``written`` (labels of outputs already published) and whether the pre-publish
    sweep over ALL outputs has run."""

    def __init__(self, auths: Iterable[WriteAuthorization]) -> None:
        ordered = list(auths)
        # The whole output SET is validated up front (duplicates, file-vs-parent-directory
        # conflicts); a conflict is raised on entry, before anything is created or published.
        self.conflict = _find_output_conflict(ordered)
        self.auths: dict[str, WriteAuthorization] = {}
        for auth in ordered:  # first wins: a later entry can never silently replace an earlier one
            self.auths.setdefault(_authorization_key(auth.path), auth)
        self.created_dirs: dict[str, tuple[int, int]] = {}
        self.written: list[str] = []
        self.preflighted = False
        self._tokens: list[Any] = []

    def __enter__(self) -> WriteScope:
        if self.conflict:
            raise WriteAuthorizationError(self.conflict)
        self._tokens.append(_WRITE_SCOPE.set(self if self.auths else None))
        return self

    def __exit__(self, *exc_info: object) -> None:
        _WRITE_SCOPE.reset(self._tokens.pop())

    def refusal_message(self, exc: BaseException) -> str:
        """The refusal text, honest about outputs that were already published."""
        message = str(exc)
        if self.written:
            message += f"; outputs already written: {', '.join(self.written)}"
        return message


_WRITE_SCOPE: ContextVar[WriteScope | None] = ContextVar("tg_write_scope", default=None)


def _authorization_key(path: str | Path) -> str:
    """Lexical key: absolute + normalized, deliberately NOT ``resolve()``d, so a swapped parent
    can never map a write onto a different (unauthorized) key."""
    return os.path.normcase(os.path.abspath(str(path)))


def file_identity(path: Path) -> tuple[int, int, int, int]:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def dir_identity(path: Path) -> tuple[int, int]:
    st = os.stat(path)
    return (st.st_dev, st.st_ino)


def write_authorizations(auths: Iterable[WriteAuthorization]) -> WriteScope:
    """Return a scope (use as ``with``) that makes ``auths`` binding. While a NON-EMPTY scope is
    active, every ``atomic_write_bytes_anchored`` must target an authorized path (anything else
    is refused: fail closed). An empty scope leaves writers unchanged (default-off)."""
    return WriteScope(auths)


def _enforce_parent(path: Path, auth: WriteAuthorization) -> None:
    """The parent must still resolve to the directory that was authorized."""
    try:
        if auth.parent_identity is None:
            # Parent did not exist when approved: it must still not exist (the writer creates it).
            if path.parent.exists():
                raise WriteAuthorizationError(
                    f"{auth.label} parent appeared after it was authorized (refused)"
                )
        elif dir_identity(path.parent) != auth.parent_identity:
            raise WriteAuthorizationError(
                f"{auth.label} parent changed after it was authorized (refused)"
            )
    except WriteAuthorizationError:
        raise
    except OSError:
        raise WriteAuthorizationError(
            f"{auth.label} parent changed after it was authorized (refused)"
        ) from None


def _is_link_or_junction(path: str | Path) -> bool:
    st = os.lstat(path)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & reparse)


def _plan_missing_parents(path: Path, auth: WriteAuthorization, scope: WriteScope) -> list[Path]:
    """THE single walk over every directory component between the authorized existing ancestor and
    the target's parent, shared by the pre-publish sweep and the writer so they can never
    disagree. Returns the components still to be created, top-down. Refuses (raises) on:
    an ancestor that moved or became a link; a path outside the ancestor; any component that
    ALREADY EXISTS unless this scope created it and it is still that very real directory (not a
    link/junction, identity intact) -- i.e. a directory made by anyone else."""
    label = auth.label
    if auth.ancestor is None:
        raise WriteAuthorizationError(f"{label} parent was not authorized for creation (refused)")
    ancestor_path, ancestor_id = auth.ancestor
    try:
        if _is_link_or_junction(ancestor_path) or dir_identity(Path(ancestor_path)) != ancestor_id:
            raise WriteAuthorizationError(
                f"{label} parent changed after it was authorized (refused)"
            )
        components: list[Path] = []
        probe = path.parent
        while os.path.normcase(str(probe)) != os.path.normcase(ancestor_path):
            components.append(probe)
            if probe.parent == probe:
                raise WriteAuthorizationError(f"{label} parent is outside its ancestor (refused)")
            probe = probe.parent
        missing: list[Path] = []
        for component in reversed(components):
            if os.path.lexists(component):
                recorded = scope.created_dirs.get(os.path.normcase(str(component)))
                if (
                    recorded is None
                    or missing  # an existing child under a still-missing parent: inconsistent
                    or _is_link_or_junction(component)
                    or dir_identity(component) != recorded
                ):
                    raise WriteAuthorizationError(
                        f"{label} parent was created by someone else (refused)"
                    )
                continue
            missing.append(component)
        return missing
    except WriteAuthorizationError:
        raise
    except OSError:
        raise WriteAuthorizationError(
            f"{label} parent changed after it was authorized (refused)"
        ) from None


def _create_missing_parents(
    path: Path, auth: WriteAuthorization, scope: WriteScope
) -> WriteAuthorization:
    """The authorized parent was ABSENT: create the components :func:`_plan_missing_parents`
    approved (``os.mkdir`` fails if someone else got there first -> refuse), reject a
    symlink/junction, keep every new directory under the authorized existing ancestor, record
    each in the scope, and return the authorization with the NEW parent's identity captured, so
    the pre-publish check verifies exactly what we created."""
    label = auth.label
    missing = _plan_missing_parents(path, auth, scope)
    assert auth.ancestor is not None
    real_ancestor = os.path.normcase(os.path.realpath(auth.ancestor[0]))
    try:
        for component in missing:
            os.mkdir(component)  # FileExistsError: created by someone else -> refuse
            if _is_link_or_junction(component):
                raise WriteAuthorizationError(f"{label} parent is a link (refused)")
            real = os.path.normcase(os.path.realpath(component))
            if os.path.commonpath([real, real_ancestor]) != real_ancestor:
                raise WriteAuthorizationError(f"{label} parent escaped its ancestor (refused)")
            scope.created_dirs[os.path.normcase(str(component))] = dir_identity(component)
        return _dc_replace(auth, parent_identity=dir_identity(path.parent))
    except WriteAuthorizationError:
        raise
    except OSError:  # incl. FileExistsError from the mkdir above
        raise WriteAuthorizationError(
            f"{label} parent changed after it was authorized (refused)"
        ) from None


def _enforce_authorization(path: Path, auth: WriteAuthorization) -> None:
    """Immediately-before-publish re-check (run before EACH replace attempt)."""
    _enforce_parent(path, auth)
    if auth.identity is None:
        return
    try:
        unchanged = file_identity(path) == auth.identity
    except OSError:
        unchanged = False
    if not unchanged:
        raise WriteAuthorizationError(f"{auth.label} changed after it was authorized (refused)")


def _preflight_all(scope: WriteScope) -> None:
    """Refuse BEFORE the first publish if ANY output of the scope can no longer be written
    (parent/ancestor/identity re-verified for every authorization), so a multi-output request is
    not left half-published by a refusal that was already visible."""
    for auth in scope.auths.values():
        target = Path(auth.path)
        if auth.parent_identity is None:
            _plan_missing_parents(Path(auth.path), auth, scope)  # same walk the writer uses
        else:
            _enforce_authorization(target, auth)
            if auth.identity is None and os.path.lexists(target):
                raise WriteAuthorizationError(
                    f"{auth.label} appeared after it was authorized (refused)"
                )
    scope.preflighted = True


def atomic_write_bytes_anchored(
    path: Path, data: bytes, *, mode: int | None = None, replace: bool = True
) -> None:
    """Write ``data`` to ``path`` atomically, with explicit overwrite mode.

    This is a shared extension point for class-level writer ratchets:

    - ``replace=True`` preserves legacy overwrite semantics via :func:`replace_with_retry`.
    - ``replace=False`` performs a fail-closed, no-clobber publish that refuses to
      create over an existing destination.

    Crash window (``replace=False``): the publish is ``os.link(tmp, path)`` followed by
    ``tmp.unlink()``. A writer killed between the two leaves BOTH names on the content
    (``st_nlink == 2``) until someone removes the ``.<name>.<uuid>.tmp`` sibling. This helper cannot
    recover that itself (it cannot tell a live concurrent writer from an orphan); a caller that needs
    it serializes writers with ``index_lock`` and sweeps the exact temp-name pattern under the lock --
    see ``session_daemon_trust._recover_orphan_temps`` for the secret. The other callers write
    non-secret scaffolding, where a stray ``.tmp`` link is harmless.
    """
    scope = _WRITE_SCOPE.get()
    auth = None
    if scope is not None:
        auth = scope.auths.get(_authorization_key(path))
        if auth is None:  # an authorization scope is active and this target is not in it
            raise WriteAuthorizationError(
                f"write target is not an authorized artifact path (refused): {path.name}"
            )
        if not scope.preflighted:
            _preflight_all(scope)
        if auth.parent_identity is None:
            auth = _create_missing_parents(path, auth, scope)
        else:
            _enforce_parent(path, auth)  # BEFORE temp creation can touch a swapped parent
        if auth.identity is None:
            replace = False  # approved as ABSENT: publish no-clobber, never replace
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
            # M6: fsync the data before publish so a crash can never publish a truncated file.
            os.fsync(handle.fileno())
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # don't leave partial temp behind
        raise

    if mode is not None:
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass

    try:
        if replace:
            replace_with_retry(
                tmp_path,
                path,
                precheck=(lambda: _enforce_authorization(path, auth)) if auth else None,
            )
        else:
            if auth is not None:
                _enforce_authorization(path, auth)
            try:
                _publish_bytes_no_clobber(tmp_path, path)
            except FileExistsError:
                if auth is None:
                    raise
                raise WriteAuthorizationError(
                    f"{auth.label} appeared after it was authorized (refused)"
                ) from None
            # The hard link left a SECOND name for the published bytes: drop the temp name now
            # (before the directory fsync), so only the authorized artifact remains.
            tmp_path.unlink(missing_ok=True)
        if scope is not None and auth is not None:
            scope.written.append(auth.label)
    except BaseException:
        # If publish fails, make sure the sibling temp is removed before control exits.
        tmp_path.unlink(missing_ok=True)
        raise

    # Best-effort durability of the directory entry after publish.
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


def record_from_entry(record_cls: Any, entry: dict[str, Any]) -> Any:
    """Build ``record_cls`` from an index entry, ignoring keys it does not declare (F-12).

    An index written by a newer tg carries extra fields; ``record_cls(**entry)`` would raise
    TypeError and wedge every command that loads the index. A rewritten index drops the unknown
    keys, which is acceptable for a forward-compat read."""
    names = {f.name for f in dataclasses.fields(record_cls)}
    return record_cls(**{k: v for k, v in entry.items() if k in names})


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


def _outer_release_allowed(owner_pid: int) -> bool:
    """False in a fork child that inherited an active ``index_lock`` context: it must release
    nothing (the sidecar fd number may already belong to a lock the child acquired itself)."""
    return os.getpid() == owner_pid


def _legacy_release_allowed(owner_pid: int) -> bool:
    """False in a fork child: its inherited legacy cleanup would unlink the PARENT's lock file
    (the token matches), so the release step is skipped there."""
    return os.getpid() == owner_pid


def write_new_lock_file(fd: int, lock_path: Path, content: bytes) -> None:
    """Write ``content`` into the lock file THIS call just created (``fd``) and close ``fd``.

    On ANY failure (``OSError`` such as ENOSPC, or a ``BaseException`` like KeyboardInterrupt) the
    descriptor is closed and the file this call created is unlinked -- but only after the fd and the
    path are verified to be the same file (``st_dev``/``st_ino``), so another holder's lock is never
    deleted -- and the primary error propagates. Otherwise a failed write would leave an empty lock
    file behind that blocks every later acquisition until it goes stale."""
    try:
        os.write(fd, content)
    except BaseException:
        try:
            held, current = os.fstat(fd), os.stat(lock_path)
            ours = (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)
        except OSError:
            ours = False
        try:
            os.close(fd)
        finally:
            if ours:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
        raise
    os.close(fd)


@contextmanager
def _legacy_index_lock(
    index_path: Path,
    *,
    poll_interval_s: float = _POLL_S,
    timeout_s: float = _TIMEOUT_S,
    stale_after_s: float = _STALE_AFTER_S,
    heartbeat_interval_s: float | None = None,
) -> Iterator[None]:
    """The original O_EXCL + token + heartbeat + stale-reclaim protocol, unchanged apart from the
    fork-child release guard. Old-version processes only ever see this file, so wrapping it in the
    OS sidecar lock (``index_lock``) keeps new-vs-old behaviour identical to the pre-sidecar one."""
    lock_path = _lock_path_for(index_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    # audit #14: a uuid4 ownership token (not just the pid, which can collide across a
    # crash+relaunch) identifies THIS acquisition. Written alongside the pid so a stale
    # legacy/pid-only lock (no second line) is still tolerated by `_token_for_lock`. Everything
    # that can raise is computed BEFORE the lock file exists, so nothing can be created and then
    # orphaned between creation and the write below.
    owner_pid = os.getpid()
    token = uuid4().hex
    hb_interval = (
        heartbeat_interval_s
        if heartbeat_interval_s is not None
        else _default_heartbeat_interval_s(stale_after_s, poll_interval_s)
    )
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
        write_new_lock_file(fd, lock_path, f"{os.getpid()}\n{token}\n".encode())
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
            if _legacy_release_allowed(owner_pid):
                stop_heartbeat.set()
                heartbeat.join(timeout=1.0)  # bounded: never hang release on a wedged thread
    finally:
        if _legacy_release_allowed(owner_pid):
            _release_lock(lock_path, token)


# --- OS advisory sidecar lock (F-05 / F-10) ---------------------------------------------------
#
# Every NEW process takes an OS advisory lock on `<lockfile>.os` first, then runs the unchanged
# legacy protocol above on the original lock file. New-vs-new exclusion is exact (the sidecar
# admits one process at a time, so two reclaimers can never interleave on the legacy file);
# new-vs-old is exactly the legacy protocol, as between two old processes. The OS releases the
# lock when the handle closes or the process dies; the sidecar file is never deleted.

_HELD_LOCK_FDS: set[int] = set()
_REGISTRY_LOCK = threading.Lock()
_AFTER_FORK_CHILD_HOOKS: list[Callable[[], None]] = []


def register_after_fork_child_hook(hook: Callable[[], None]) -> None:
    """Run ``hook`` in a fork child after this module reset its own lock state (POSIX only)."""
    _AFTER_FORK_CHILD_HOOKS.append(hook)


def _os_lock_path_for(index_path: Path) -> Path:
    legacy = _lock_path_for(index_path)
    return legacy.with_name(legacy.name + ".os")


def _register_held_fd(fd: int) -> None:
    _HELD_LOCK_FDS.add(fd)


def _unlock_and_close(fd: int) -> None:
    """Unlock and close ``fd``; never raises and never touches the registry lock."""
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _lock_identity_matches(fd: int, lock_path: Path) -> bool:
    try:
        held, current = os.fstat(fd), os.stat(lock_path)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


def try_os_file_lock(lock_path: Path) -> int | None:
    """Non-blocking exclusive OS lock on ``lock_path`` (created, never unlinked). Returns the
    held descriptor, or None when another handle/process holds it or the path no longer names the
    file that was locked (identity re-check)."""
    with _REGISTRY_LOCK:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        handed_off = False
        try:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return None  # contended: the finally below closes the descriptor
            if not _lock_identity_matches(fd, lock_path):
                return None
            _register_held_fd(fd)
            handed_off = True  # ownership transfers ONLY on a successful return
            return fd
        finally:
            if not handed_off:
                # Any early return or ANY exception (KeyboardInterrupt included) between the open
                # and the hand-off: unlock + close here. Never re-enter the registry lock.
                _unlock_and_close(fd)


def release_os_file_lock(fd: int) -> None:
    with _REGISTRY_LOCK:
        _HELD_LOCK_FDS.discard(fd)
        _unlock_and_close(fd)


if hasattr(os, "register_at_fork"):  # absent on Windows

    def _before_fork() -> None:
        _REGISTRY_LOCK.acquire()

    def _after_fork_in_parent() -> None:
        _REGISTRY_LOCK.release()

    def _after_fork_in_child() -> None:
        # A fork child inherits the open-file descriptions of every held sidecar lock. Closing the
        # child's copies does NOT unlock (flock releases when the LAST descriptor closes and the
        # parent's stays open), but leaving them open would keep the lock held after the parent
        # dies for as long as the child lives.
        global _REGISTRY_LOCK
        _REGISTRY_LOCK = threading.Lock()
        for held_fd in list(_HELD_LOCK_FDS):
            try:
                os.close(held_fd)
            except OSError:
                pass
        _HELD_LOCK_FDS.clear()
        for hook in _AFTER_FORK_CHILD_HOOKS:
            hook()

    os.register_at_fork(
        before=_before_fork,
        after_in_parent=_after_fork_in_parent,
        after_in_child=_after_fork_in_child,
    )


@contextmanager
def index_lock(
    index_path: Path,
    *,
    poll_interval_s: float = _POLL_S,
    timeout_s: float = _TIMEOUT_S,
    stale_after_s: float = _STALE_AFTER_S,
    heartbeat_interval_s: float | None = None,
) -> Iterator[None]:
    sidecar = _os_lock_path_for(index_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while (fd := try_os_file_lock(sidecar)) is None:
        if time.monotonic() >= deadline:
            raise IndexLockTimeoutError(
                f"could not acquire {_lock_path_for(index_path)} within {timeout_s}s"
            )
        time.sleep(poll_interval_s)
    owner_pid = os.getpid()
    try:
        remaining = max(0.0, deadline - time.monotonic())
        with _legacy_index_lock(
            index_path,
            poll_interval_s=poll_interval_s,
            timeout_s=remaining,
            stale_after_s=stale_after_s,
            heartbeat_interval_s=heartbeat_interval_s,
        ):
            yield
    finally:
        if _outer_release_allowed(owner_pid):
            release_os_file_lock(fd)
