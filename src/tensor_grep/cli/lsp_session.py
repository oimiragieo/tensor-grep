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

import queue
import threading
from collections import OrderedDict
from typing import Any

from tensor_grep.cli.process_containment import wake_waiters


class ProviderSession:
    def __init__(self, generation: int = 0) -> None:
        self.generation = generation
        self.process: Any = None
        self.containment: Any = None
        self.writer: Any = None  # DeadlineWriter bound to THIS process's stdin
        self.capabilities: dict[str, Any] = {}
        self.initialized = False
        self.lsp_provider_response = False
        self.opened_documents: OrderedDict[str, None] = OrderedDict()
        self.message_queue: queue.Queue[Any] = queue.Queue()
        self.pending_requests: dict[int, queue.Queue[dict[str, Any]]] = {}
        self.orphan_responses: dict[int, dict[str, Any]] = {}
        self.doc_versions: dict[str, int] = {}
        self.reader_thread: threading.Thread | None = None
        self.stderr_thread: threading.Thread | None = None
        self.stderr_tail: list[str] = []
        # warm-LSP readiness (workDoneProgress) belongs to THIS provider: a fresh session starts
        # with fresh readiness, and a stale reader can only ever touch its own session
        self.active_progress_tokens: set[str] = set()
        self.progress_end_count = 0
        self.progress_activity_seen = False
        self.index_ready = False


def reset_after_stop(session: ProviderSession, closed_sentinel: Any) -> None:
    """Clear THIS session's own state and wake its waiters. Never touches another session."""
    session.process = session.containment = None
    session.capabilities = {}
    session.initialized = False
    session.lsp_provider_response = False
    session.opened_documents.clear()
    session.reader_thread = session.stderr_thread = None
    wake_waiters(session.pending_requests.values(), closed_sentinel)
    session.pending_requests = {}
    session.orphan_responses = {}
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


def mark_responding_session(client: Any) -> None:
    """Certify the session that ANSWERED this thread's last request, never whichever session is
    current now (a late mark after a restart is a no-op). Tolerates non-ExternalLSPClient doubles."""
    responding = getattr(client, "responding_session", None)
    mark = getattr(client, "mark_provider_response", None)
    if callable(responding) and callable(mark):
        mark(responding())
