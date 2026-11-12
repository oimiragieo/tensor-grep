from __future__ import annotations

import json
import socketserver
import threading
import time
from time import monotonic
from typing import Any, cast


class _HandlerAdmissionThreads(socketserver.ThreadingMixIn):
    _max_concurrent_connections = 32

    def _init_handler_admission(self, timeout_seconds: float) -> None:
        self._connection_slots = threading.BoundedSemaphore(self._max_concurrent_connections)
        self._handler_deadline_lock = threading.Lock()
        self._handler_deadlines: dict[Any, float] = {}
        self._handler_admissions: dict[Any, float] = {}
        self._handler_timeout_seconds = timeout_seconds

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        with self._handler_deadline_lock:
            admitted_at = monotonic()
            self._handler_admissions[request] = admitted_at
            self._handler_deadlines[request] = admitted_at + self._handler_timeout_seconds
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._release_handler(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_handler(request)

    def _release_handler(self, request: Any) -> None:
        with self._handler_deadline_lock:
            self._handler_deadlines.pop(request, None)
            self._handler_admissions.pop(request, None)
        self._connection_slots.release()


def _load_payload_with_status_retry(
    cache: Any,
    session_id: str,
    path: str,
    *,
    deadline_monotonic: float | None = None,
    retry_seconds: float = 0.25,
) -> tuple[dict[str, Any], str]:
    retry_deadline = monotonic() + retry_seconds
    while True:
        now = monotonic()
        if deadline_monotonic is not None and now >= deadline_monotonic:
            raise TimeoutError("daemon request deadline expired while loading session payload")
        try:
            loaded = cache.load_with_status(session_id, path)
            if deadline_monotonic is not None and monotonic() >= deadline_monotonic:
                raise TimeoutError("daemon request deadline expired while loading session payload")
            return cast(tuple[dict[str, Any], str], loaded)
        except (FileNotFoundError, json.JSONDecodeError):
            now = monotonic()
            if deadline_monotonic is not None and now >= deadline_monotonic:
                raise TimeoutError(
                    "daemon request deadline expired while loading session payload"
                ) from None
            if now >= retry_deadline:
                raise
            remaining = retry_deadline - now
            if deadline_monotonic is not None:
                remaining = min(remaining, deadline_monotonic - now)
            time.sleep(min(0.05, remaining))


def _read_bounded_request_line(
    rfile: Any,
    max_bytes: int = 1 * 1024 * 1024,
    *,
    deadline: float | None = None,
    connection: Any | None = None,
) -> str | None:
    raw = bytearray()
    reader = getattr(rfile, "read1", rfile.readline)
    while len(raw) <= max_bytes:
        try:
            if deadline is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return None
                if connection is not None:
                    connection.settimeout(remaining)
            chunk = reader(min(4096, max_bytes + 1 - len(raw)))
        except (TimeoutError, OSError):
            return None
        if not chunk:
            break
        newline = chunk.find(b"\n")
        raw.extend(chunk if newline < 0 else chunk[: newline + 1])
        if newline >= 0:
            break
    if not raw or len(raw) > max_bytes:
        return None
    return bytes(raw).decode("utf-8", errors="replace").strip()
