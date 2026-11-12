"""Per-process state of one external LSP provider, as ONE swappable object.

A client used to keep the spawned process, its writer, capabilities, open documents,
``initialized`` flag and pending request slots as loose attributes, so a *stale* teardown (an
abandoned shutdown worker, or a ``stop()`` that resumed after a restart) could clear the
REPLACEMENT provider's state. Now every piece lives on a ``ProviderSession``:

* the client holds one reference (``client._session``) and swaps it under the client lock;
* ``stop()``, the readers and the graceful-shutdown worker capture the session they belong to and
  touch ONLY that object's fields, so a stale callback cannot reach a newer session by
  construction (no generation checks at call sites);
* the client exposes the old attribute names as properties that forward to the CURRENT session,
  so existing callers and tests keep working.

``generation`` is a diagnostic only (which spawn this session was).
"""

from __future__ import annotations

import json
import queue
import threading
from collections import OrderedDict
from typing import Any, cast

from tensor_grep.cli.process_containment import wake_waiters

_MAX_LSP_MESSAGE_BYTES = 64 * 1024 * 1024
_MAX_LSP_HEADER_LINES = 128
_MAX_LSP_HEADER_BYTES = 64 * 1024


class _DecodedLSPMessage(dict[str, Any]):
    def __init__(self, message: dict[str, Any], encoded_json_bytes: int) -> None:
        super().__init__(message)
        self.encoded_json_bytes = encoded_json_bytes


def _read_bounded_line(stream: Any, limit: int) -> Any:
    return stream.readline(limit)


def _read_message(stream: Any) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    header_bytes_total = 0
    for _ in range(_MAX_LSP_HEADER_LINES):
        line = _read_bounded_line(stream, _MAX_LSP_HEADER_BYTES)
        if line in ("", b""):
            return None
        ends_with_newline = line.endswith(b"\n") if isinstance(line, bytes) else line.endswith("\n")
        if len(line) >= _MAX_LSP_HEADER_BYTES and not ends_with_newline:
            return None
        header_bytes_total += len(line)
        if header_bytes_total > _MAX_LSP_HEADER_BYTES:
            return None
        if line in ("\r\n", "\n", b"\r\n", b"\n"):
            break
        if isinstance(line, bytes):
            line = line.decode("ascii", errors="replace")
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    else:
        return None
    try:
        content_length = int(headers.get("content-length", "0"))
    except (TypeError, ValueError):
        return None
    if content_length <= 0 or content_length > _MAX_LSP_MESSAGE_BYTES:
        return None
    body = stream.read(content_length)
    if not body:
        return None
    encoded_json_bytes = len(body) if isinstance(body, bytes) else len(body.encode("utf-8"))
    parsed = json.loads(body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body)
    if not isinstance(parsed, dict):
        return None
    return _DecodedLSPMessage(cast(dict[str, Any], parsed), encoded_json_bytes)


def _estimated_json_bytes(value: Any, limit: int) -> int:
    def string_size(text: str, remaining: int) -> int:
        size = 2
        for char in text:
            code = ord(char)
            size += (
                2
                if char in '\\"\b\f\n\r\t'
                else 6
                if code < 0x20 or (code >= 0x80 and code <= 0xFFFF)
                else 12
                if code > 0xFFFF
                else 1
            )
            if size > remaining:
                return remaining + 1
        return size

    def size_of(item: Any, remaining: int) -> int:
        if isinstance(item, str):
            return string_size(item, remaining)
        if isinstance(item, dict):
            size = len(item) + 1
            if size + 1 > remaining:
                return remaining + 1
            for key, child in item.items():
                if not isinstance(key, str):
                    return remaining + 1
                size += string_size(key, remaining - size) + 1
                size += size_of(child, remaining - size)
                if size > remaining:
                    return remaining + 1
            return size + 1
        if isinstance(item, list):
            size = len(item) + 1
            if size + 1 > remaining:
                return remaining + 1
            for child in item:
                size += size_of(child, remaining - size)
                if size > remaining:
                    return remaining + 1
            return size + 1
        if item is None:
            return 4
        if item is True:
            return 4
        if item is False:
            return 5
        if isinstance(item, (int, float)):
            return len(str(item))
        return remaining + 1

    try:
        return size_of(value, limit)
    except (RecursionError, ValueError):
        return limit + 1


class ProviderSession:
    def __init__(self, generation: int = 0) -> None:
        self.generation = generation
        self.process: Any = None
        self.containment: Any = None
        self.unusable = False
        self.writer: Any = None  # DeadlineWriter bound to THIS process's stdin
        self.capabilities: dict[str, Any] = {}
        self.initialized = False
        self.lsp_provider_response = False
        self.opened_documents: OrderedDict[str, None] = OrderedDict()
        self.message_queue: queue.Queue[Any] = queue.Queue()
        self.pending_requests: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.orphan_responses: dict[int, dict[str, Any]] = {}
        self.orphan_response_sizes: dict[int, int] = {}
        self.orphan_response_total_bytes = 0
        self.doc_versions: dict[str, int] = {}
        self.reader_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.stderr_tail: list[str] = []
        self.stderr_tail_bytes = 0
        # warm-LSP readiness (workDoneProgress) belongs to THIS provider: a fresh session starts
        # with fresh readiness, and a stale reader can only ever touch its own session
        self.active_progress_tokens: set[str] = set()
        self.progress_end_count = 0
        self.progress_activity_seen = False
        self.index_ready = False

    def pop_orphan_response(self, request_id: int) -> dict[str, Any] | None:
        response = self.orphan_responses.pop(request_id, None)
        self.orphan_response_total_bytes -= self.orphan_response_sizes.pop(request_id, 0)
        return response

    def retain_orphan_response(
        self,
        request_id: int,
        response: dict[str, Any],
        encoded_bytes: int | None,
        *,
        max_count: int,
        max_bytes: int,
    ) -> None:
        if encoded_bytes is None:
            encoded_bytes = _estimated_json_bytes(response, max_bytes)
        if encoded_bytes <= 0 or encoded_bytes > max_bytes:
            return
        self.pop_orphan_response(request_id)
        while self.orphan_responses and (
            len(self.orphan_responses) >= max_count
            or self.orphan_response_total_bytes + encoded_bytes > max_bytes
        ):
            self.pop_orphan_response(next(iter(self.orphan_responses)))
        self.orphan_responses[request_id] = response
        self.orphan_response_sizes[request_id] = encoded_bytes
        self.orphan_response_total_bytes += encoded_bytes


def reset_after_stop(session: ProviderSession, closed_sentinel: Any) -> None:
    """Clear THIS session's own state and wake its waiters. Never touches another session."""
    session.unusable = True
    session.process = session.containment = None
    session.capabilities = {}
    session.initialized = False
    session.lsp_provider_response = False
    session.opened_documents.clear()
    session.reader_thread = session.stderr_thread = None
    wake_waiters(session.pending_requests.values(), closed_sentinel)
    session.pending_requests = {}
    session.orphan_responses = {}
    session.orphan_response_sizes = {}
    session.orphan_response_total_bytes = 0
    session.doc_versions = {}
    writer, session.writer = session.writer, None
    if writer is not None:
        writer.close()


def session_field(name: str) -> property:
    """READ-ONLY client attribute reading the CURRENT session's ``name`` field.

    There is deliberately no setter: an assignment through the client would land on whichever
    session is current when it executes, which is the wrong one after a restart. Writers bind
    to a session explicitly (``client.mark_provider_response(session)``; tests use
    ``tests/helpers/lsp_session.install_session``).
    """

    def getter(client: Any) -> Any:
        return getattr(client._session, name)

    return property(getter)


class SessionBackedState:
    """Mixin: READ-ONLY views of the client's CURRENT ``_session`` for external readers."""

    _session: ProviderSession
    process = session_field("process")
    _containment = session_field("containment")
    _writer = session_field("writer")
    capabilities = session_field("capabilities")
    initialized = session_field("initialized")
    lsp_provider_response = session_field("lsp_provider_response")
    _opened_documents = session_field("opened_documents")
    _message_queue = session_field("message_queue")
    _pending_requests = session_field("pending_requests")
    _orphan_responses = session_field("orphan_responses")
    _doc_versions = session_field("doc_versions")
    _reader_thread = session_field("reader_thread")
    _stderr_thread = session_field("stderr_thread")
    _stderr_tail = session_field("stderr_tail")


def proof_request(client: Any, method: str, params: dict[str, Any]) -> Any:
    """``client.request`` for a request whose valid response counts as LSP proof.

    ExternalLSPClient records the proof ON THE SESSION that answered, inside ``_request``, so no
    later (lockable, restartable) step is involved. A request implementation without a ``proof``
    parameter (a test double) is treated as proof-bearing for the session that was current when
    the call began; that object is its own, so a restart cannot redirect the mark.
    """
    import inspect

    if "proof" in inspect.signature(client.request).parameters:
        return client.request(method, params, proof=True)
    current = getattr(client, "current_session", None)
    session = current() if callable(current) else None
    result = client.request(method, params)
    if session is not None:
        session.lsp_provider_response = True
    return result
