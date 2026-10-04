"""Absolute-deadline budget for one external-LSP health probe (initialize, requests, cleanup).

``deadline_monotonic`` is a ``time.monotonic()`` timestamp. Remaining time is recomputed before
EVERY phase, and a small cleanup slice (``min(max_cleanup, 10%`` of the budget) is reserved so
teardown (graceful shutdown, tree kill, pipe close) fits inside the deadline too.
"""

from __future__ import annotations

import time
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
