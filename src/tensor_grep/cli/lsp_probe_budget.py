"""Absolute-deadline budget for one external-LSP health probe (initialize, requests, cleanup).

``deadline_monotonic`` is a ``time.monotonic()`` timestamp. Remaining time is recomputed before
EVERY phase, and a small cleanup slice (``min(max_cleanup, 10%`` of the budget) is reserved so
teardown (graceful shutdown, tree kill, pipe close) fits inside the deadline too.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class ProbeBudget:
    def __init__(
        self, deadline_monotonic: float | None, phase_timeout_seconds: float, max_cleanup: float
    ) -> None:
        self.phase_timeout_seconds = phase_timeout_seconds
        self.cleanup_seconds: float | None = None
        self._probe_deadline: float | None = None
        if deadline_monotonic is not None:
            total = max(deadline_monotonic - time.monotonic(), 0.0)
            self.cleanup_seconds = min(max_cleanup, 0.1 * total)
            self._probe_deadline = deadline_monotonic - self.cleanup_seconds
        self.fired = threading.Event()
        self._cancelled = threading.Event()
        self._decision = threading.Lock()  # serializes 'watchdog fires' vs 'probe succeeds'
        self._settled = False
        self._client: Any = None

    def arm(self, client: Any) -> None:
        """Cap the client's initialize/request timeouts by the time left before cleanup.

        Raises ``TimeoutError`` (an ``OSError``) once the probe budget is spent.
        """
        timeout = self.phase_timeout_seconds
        if self._probe_deadline is not None:
            remaining = self._probe_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("doctor LSP probe deadline exhausted")
            timeout = min(timeout, remaining)
        client.request_timeout_seconds = timeout
        client.initialize_timeout_seconds = timeout

    def start_watchdog(self, client: Any) -> None:
        """ONE daemon watchdog per probe: at the absolute probe deadline it kills the provider.

        Per-call timeouts cannot bound a blocking transport write (a provider that never reads
        stdin) or a lock wait. Killing the tree (Job Object terminate / killpg) makes the blocked
        write fail with a broken pipe, and ``explain`` turns that into "deadline exceeded".
        """
        if self._probe_deadline is None:
            return
        self._client = client
        client.deadline_monotonic = self._probe_deadline  # lock/write/wait bound
        threading.Thread(target=self._watch, args=(client,), daemon=True).start()

    def _watch(self, client: Any) -> None:
        assert self._probe_deadline is not None
        if self._cancelled.wait(max(self._probe_deadline - time.monotonic(), 0.0)):
            return
        with self._decision:
            if self._settled:
                return  # the probe already decided success before the deadline
            self.fired.set()
        while not self._cancelled.is_set():  # retry: the provider may still be spawning
            containment, process = client._containment, client.process
            if containment is not None:
                containment.kill()
            if process is not None:
                try:
                    process.kill()
                except OSError:
                    pass
                return
            self._cancelled.wait(0.05)

    def settle(self) -> bool:
        """Decide success atomically with the watchdog: True only if it has not fired and the
        probe deadline has not passed. Once False, the probe can never become ``ready``; once
        True, the watchdog can no longer fire for this probe."""
        with self._decision:
            if self.fired.is_set():
                return False
            if self._probe_deadline is not None and time.monotonic() >= self._probe_deadline:
                self.fired.set()
                return False
            self._settled = True
            return True

    def cancel(self) -> None:
        self._cancelled.set()
        if self._client is not None:
            self._client.deadline_monotonic = None

    def reason(self, last_error: Any, probe_error: Any) -> Any:
        """Report the deadline as the cause when the watchdog fired (the write error is its echo)."""
        if self.fired.is_set():
            return "doctor LSP probe deadline exceeded" + (f" ({last_error})" if last_error else "")
        return last_error or probe_error

    def explain(self, exc: Exception) -> Exception:
        """The watchdog's kill surfaces as a broken pipe; report the real cause."""
        if self.fired.is_set():
            return TimeoutError("doctor LSP probe deadline exceeded")
        return exc


def remaining_seconds(client: Any, default: float) -> float:
    """Time a lock wait / write / response wait may still take: ``default`` capped by the probe's
    ABSOLUTE deadline (``client.deadline_monotonic``, set while a probe runs). Raises
    ``TimeoutError`` (an ``OSError``) once that deadline has passed."""
    deadline = client.deadline_monotonic
    if deadline is None:
        return default
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("doctor LSP probe deadline exceeded")
    return float(min(default, left))


@contextmanager
def client_lock(
    client: Any, timeout: float | None = None, *, follow_deadline: bool = True
) -> Iterator[None]:
    """``with client._lock`` that honours the probe deadline instead of waiting unboundedly.

    Every lock acquisition reachable from a probe goes through here (a census test fails if a
    bare ``with self._lock`` reappears in those methods). With no deadline and no ``timeout``
    it is the plain blocking acquire, exactly as before. ``follow_deadline=False`` is for the
    reader thread, which must still run (to wake waiters) after the probe's deadline has passed.
    """
    wait = timeout
    if follow_deadline and client.deadline_monotonic is not None:
        wait = remaining_seconds(client, timeout if timeout is not None else float("inf"))
    acquired = client._lock.acquire() if wait is None else client._lock.acquire(timeout=wait)
    if not acquired:
        raise TimeoutError("doctor LSP probe deadline exceeded (client lock busy)")
    try:
        yield
    finally:
        client._lock.release()
