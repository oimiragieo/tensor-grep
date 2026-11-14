"""Bounded ownership helpers for the daemon's implicit-session LRU."""

from __future__ import annotations

import errno
import threading
from pathlib import Path
from types import ModuleType
from typing import Any


def _identity(root: str, session_id: str) -> tuple[str, str]:
    return root, session_id


def _pin(
    server: Any, key: tuple[str, str, str], root: str, session_id: str, receipt: list[Any]
) -> None:
    identity = _identity(root, session_id)
    server._implicit_active_pins[identity] = server._implicit_active_pins.get(identity, 0) + 1
    receipt[:] = [(key, root, session_id)]


def _mark_for_cleanup(server: Any, root: str, session_id: str) -> bool:
    identity = _identity(root, session_id)
    if server._implicit_active_pins.get(identity, 0):
        server._implicit_pending_evictions.add(identity)
        return False
    server._implicit_cleanup_owned.add(identity)
    return True


def _cleanup(
    owner: ModuleType, server: Any, root: str, session_id: str, deadline: float | None
) -> None:
    identity = _identity(root, session_id)
    try:
        try:
            owner._remove_implicit_session_payload(root, session_id, deadline_monotonic=deadline)
        except Exception:
            pass
    finally:
        with server._implicit_session_lock:
            server._implicit_cleanup_owned.discard(identity)


def remove_payload(
    owner: ModuleType, path: str, session_id: str, *, deadline_monotonic: float | None = None
) -> None:
    root = owner._resolve_root(Path(path))
    if deadline_monotonic is not None and owner.monotonic() >= deadline_monotonic:
        return
    try:
        with owner.index_lock(owner._index_path(root), deadline_monotonic=deadline_monotonic):
            if deadline_monotonic is not None and owner.monotonic() >= deadline_monotonic:
                return
            owner._session_payload_path(root, session_id).unlink(missing_ok=True)
            records = [
                record for record in owner._load_index(root) if record.session_id != session_id
            ]
            owner._write_index(root, records)
    except (OSError, ValueError, owner.IndexLockTimeoutError):
        # Cleanup is best-effort under this request's budget; session-store retention is the fallback.
        pass


def _wait_until_ready(owner: ModuleType, event: threading.Event, deadline: float | None) -> None:
    if deadline is None:
        event.wait()
        return
    remaining = max(0.0, deadline - owner.monotonic())
    if remaining <= 0 or not event.wait(remaining) or owner.monotonic() >= deadline:
        raise TimeoutError("implicit session open exceeded request deadline")


def resolve(
    owner: ModuleType,
    server: Any,
    *,
    command: str,
    session_id: str,
    path: str,
    request: dict[str, Any],
    deadline_monotonic: float | None = None,
    pin_receipt: list[Any] | None = None,
) -> str:
    if session_id or command not in owner._IMPLICIT_SESSION_COMMANDS:
        return session_id

    max_repo_files = owner._implicit_session_max_repo_files(command, request)
    root = owner._path_cache_key(path)
    requested_path = owner.os.path.normcase(str(Path(path).expanduser().resolve()))
    key = (root, requested_path, str(max_repo_files or ""))
    receipt = pin_receipt if pin_receipt is not None else []
    while True:
        with server._implicit_session_lock:
            existing = server.implicit_session_ids.get(key)
            if existing:
                identity = _identity(root, existing)
                if identity not in server._implicit_cleanup_owned:
                    server.implicit_session_ids.move_to_end(key)
                    if pin_receipt is not None:
                        _pin(server, key, root, existing, receipt)
                    return str(existing)
                server.implicit_session_ids.pop(key, None)

            event = server._implicit_inflight.get(key)
            if event is None:
                event = threading.Event()
                server._implicit_inflight[key] = event
                owner_event = True
            else:
                owner_event = False

        if not owner_event:
            _wait_until_ready(owner, event, deadline_monotonic)
            continue

        cleanup: list[tuple[str, str]] = []
        try:
            opened = owner.open_session(
                path,
                max_repo_files=max_repo_files,
                deadline_monotonic=deadline_monotonic,
            )
            opened_id = str(opened.session_id)
            identity = _identity(root, opened_id)
            with server._implicit_session_lock:
                if identity in server._implicit_cleanup_owned:
                    raise RuntimeError("implicit session ID is already being cleaned up")
                server.implicit_session_ids[key] = opened_id
                if pin_receipt is not None:
                    _pin(server, key, root, opened_id, receipt)
                while len(server.implicit_session_ids) > owner._DAEMON_IMPLICIT_SESSION_MAX_ENTRIES:
                    evicted_key, evicted_id = server.implicit_session_ids.popitem(last=False)
                    evicted_root = evicted_key[0]
                    if _mark_for_cleanup(server, evicted_root, evicted_id):
                        cleanup.append((evicted_root, evicted_id))
                if server._implicit_inflight.get(key) is event:
                    del server._implicit_inflight[key]
                event.set()
            for cleanup_root, cleanup_id in cleanup:
                _cleanup(owner, server, cleanup_root, cleanup_id, deadline_monotonic)
            return opened_id
        finally:
            with server._implicit_session_lock:
                if server._implicit_inflight.get(key) is event:
                    del server._implicit_inflight[key]
                event.set()


def release(
    owner: ModuleType,
    server: Any,
    receipt: list[Any] | None,
    *,
    deadline_monotonic: float | None = None,
) -> None:
    if not receipt:
        return
    _key, root, session_id = receipt.pop()
    identity = _identity(root, session_id)
    cleanup = False
    with server._implicit_session_lock:
        count = server._implicit_active_pins.get(identity, 0)
        if count <= 1:
            server._implicit_active_pins.pop(identity, None)
            if identity in server._implicit_pending_evictions:
                server._implicit_pending_evictions.discard(identity)
                cleanup = _mark_for_cleanup(server, root, session_id)
        else:
            server._implicit_active_pins[identity] = count - 1
    if cleanup:
        _cleanup(owner, server, root, session_id, deadline_monotonic)


def _expected_missing_payload(
    owner: ModuleType, path: str, session_id: str, exc: BaseException
) -> bool:
    if not isinstance(exc, FileNotFoundError) or exc.errno not in (None, errno.ENOENT):
        return False
    try:
        root = owner._resolve_root(Path(path))
        payload = owner._session_payload_path(root, session_id)
        if not payload.parent.is_dir():
            return False
        payload.lstat()
        return False
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return False
    exact_message = str(exc) == f"Session not found: {session_id}"
    exact_leaf = False
    if exc.filename is not None:
        try:
            exact_leaf = owner.os.path.normcase(
                owner.os.path.abspath(exc.filename)
            ) == owner.os.path.normcase(owner.os.path.abspath(payload))
        except (OSError, TypeError, ValueError):
            return False
    return exact_message or exact_leaf


def reacquire_missing(
    owner: ModuleType,
    server: Any,
    *,
    path: str,
    request: dict[str, Any],
    session_id: str,
    pin_receipt: list[Any],
    deadline_monotonic: float | None,
    exc: BaseException,
) -> str | None:
    if not pin_receipt or not _expected_missing_payload(owner, path, session_id, exc):
        return None
    key, root, pinned_id = pin_receipt[0]
    if pinned_id != session_id or root != owner._path_cache_key(path):
        return None

    old_identity = _identity(root, session_id)
    cleanup = False
    with server._implicit_session_lock:
        if server.implicit_session_ids.get(key) == session_id:
            del server.implicit_session_ids[key]
        count = server._implicit_active_pins.get(old_identity, 0)
        if count <= 1:
            server._implicit_active_pins.pop(old_identity, None)
            server._implicit_pending_evictions.discard(old_identity)
            cleanup = _mark_for_cleanup(server, root, session_id)
        else:
            server._implicit_active_pins[old_identity] = count - 1
            server._implicit_pending_evictions.add(old_identity)
        pin_receipt.clear()
    if cleanup:
        _cleanup(owner, server, root, session_id, deadline_monotonic)
    return resolve(
        owner,
        server,
        command=str(request.get("command", "")).strip().lower(),
        session_id="",
        path=path,
        request=request,
        deadline_monotonic=deadline_monotonic,
        pin_receipt=pin_receipt,
    )
