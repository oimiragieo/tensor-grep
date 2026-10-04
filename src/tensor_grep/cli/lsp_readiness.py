"""Warm-LSP readiness bookkeeping (workDoneProgress) for ``ExternalLSPClient``.

Split out of ``lsp_external_provider`` to keep that module under its size limit. The mixin owns
no state of its own: ``_active_progress_tokens`` / ``_progress_end_count`` /
``_progress_activity_seen`` / ``_index_ready`` / ``_lock`` live on the client. The only lock use
that does NOT go through ``client_lock`` is ``wait_until_ready``'s tiny in-memory critical
sections: it runs on navigation paths, never during a doctor probe (the lock census in
``test_lsp_teardown_fail_closed`` allowlists it with this reason).
"""

from __future__ import annotations

import time
from typing import Any

from tensor_grep.cli.lsp_probe_budget import client_lock


class ReadinessMixin:
    _lock: Any
    _active_progress_tokens: set[str]
    _progress_end_count: int
    _progress_activity_seen: bool
    _index_ready: bool

    def _note_progress_started(self, token: str) -> None:
        with client_lock(self, follow_deadline=False):
            self._active_progress_tokens.add(token)
            self._progress_activity_seen = True
            self._index_ready = False  # a new indexing round re-invalidates readiness

    def _note_progress_ended(self, token: str) -> None:
        with client_lock(self, follow_deadline=False):
            self._active_progress_tokens.discard(token)
            self._progress_activity_seen = True
            self._progress_end_count += 1

    def _handle_progress_notification(self, message: dict[str, Any]) -> bool:
        """P0-2: consume $/progress begin/report/end (previously dropped as id-less noise)."""
        if message.get("method") != "$/progress":
            return False
        params = message.get("params") or {}
        token = params.get("token")
        kind = (params.get("value") or {}).get("kind")
        if token is None:
            return True
        if kind == "begin":
            self._note_progress_started(str(token))
        elif kind == "end":
            self._note_progress_ended(str(token))
        # "report" -> in-flight; activity noted at begin. Nothing to do.
        return True

    def wait_until_ready(
        self,
        deadline_monotonic: float,
        *,
        probe: Any = None,
        no_progress_grace_seconds: float = 1.0,
        poll_interval_seconds: float = 0.05,
    ) -> bool:
        """Block until the server's workspace index has settled, or the deadline passes.

        Ready means: at least one workDoneProgress round has ENDED and none is active. For
        servers that never advertise progress, ``probe`` (a callable returning the current
        workspace/symbol hit count) is polled until stable across two consecutive polls; with
        no probe, we proceed best-effort after ``no_progress_grace_seconds`` of silence rather
        than burning the whole deadline. Returns False ONLY on a genuine timeout while indexing
        is demonstrably still in flight — and a timeout must NEVER arm
        ``disabled_until_monotonic`` (that cooldown is reserved for real initialize failures;
        arming it here would blackball the language for 30s of daemon uptime after one slow
        first index).
        """
        started_monotonic = time.monotonic()
        previous_probe_value: Any = None
        while True:
            with self._lock:
                if self._index_ready:
                    return True
                active = bool(self._active_progress_tokens)
                ended = self._progress_end_count > 0
                activity = self._progress_activity_seen
            if ended and not active:
                with self._lock:
                    self._index_ready = True
                return True
            now = time.monotonic()
            if now >= deadline_monotonic:
                return False
            if not activity:
                # No progress signal from this server (some don't emit workDoneProgress).
                if probe is not None:
                    try:
                        current_probe_value = probe()
                    except Exception:
                        current_probe_value = None
                    if (
                        current_probe_value is not None
                        and current_probe_value == previous_probe_value
                    ):
                        # Two consecutive stable polls -> index settled.
                        with self._lock:
                            self._index_ready = True
                        return True
                    previous_probe_value = current_probe_value
                elif now - started_monotonic >= max(no_progress_grace_seconds, 0.0):
                    # Silent server, no probe: best-effort after the grace window.
                    with self._lock:
                        self._index_ready = True
                    return True
            time.sleep(max(0.0, min(poll_interval_seconds, deadline_monotonic - now)))
