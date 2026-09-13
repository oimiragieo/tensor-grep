"""Last-resort CPU retry for a failed native-backend search, and its DURABLE disclosure.

Split out of ``cli/main.py`` (2026-09-12) rather than patched in place: ``main.py`` sat one
line under its file-size ratchet ceiling, and this repo's rule at the ceiling is that a fix
needs a split, not a patch.

The behaviour fixed in the same change: the swap was announced on **stderr only**. stderr is
transient and never reaches a ``--json`` consumer or an MCP caller -- exactly the machine
readers that cannot re-run the search to notice an engine changed under them. A silent engine
swap is the failure shape the Backend Fail-Closed Contract exists to prevent, so the swap now
also stamps the same durable ``fallback_reason`` the NLP engine-swap arm sets.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.result import SearchResult


def cpu_fallback_reason(current_file: str, exc: Exception) -> str:
    """The durable reason string stamped on a native -> CPU swap.

    Names the exception CLASS rather than its message: the message can carry a path or a
    native pointer, and this string reaches the JSON envelope.
    """
    return (
        f"native backend failed on {current_file} ({type(exc).__name__}); "
        "retried on the CPU backend"
    )


def search_with_cpu_fallback(
    current_file: str,
    pattern: str,
    config: SearchConfig,
    exc: Exception,
) -> SearchResult:
    """Retry a failed native-backend search on the always-available CPU backend.

    A runtime backend failure (native panic, IO/encoding error, version skew, GPU/OOM
    fault) must never surface to the user as a clean no-match. The CPU backend is pure
    Python and always available, so it is the safe last-resort engine (audit B2/I1).
    """
    from tensor_grep.backends.cpu_backend import CPUBackend

    sys.stderr.write(
        f"tensor-grep: search backend failed on {current_file} ({exc}); "
        "retried on the CPU backend.\n"
    )
    result = CPUBackend().search(current_file, pattern, config=config)
    result.fallback_reason = cpu_fallback_reason(current_file, exc)
    return result


def record_fallback_on_aggregate(all_results: Any, result: Any) -> None:
    """Propagate a per-file swap reason onto the aggregate result.

    The envelope stamps ``all_results.fallback_reason`` from the pipeline BEFORE the per-file
    search loop begins, so a swap discovered mid-loop would otherwise never reach the output.
    First swap wins, so a reason already set (e.g. a pipeline-level engine change) is never
    overwritten by a later per-file one.
    """
    if getattr(all_results, "fallback_reason", None) is None:
        all_results.fallback_reason = getattr(result, "fallback_reason", None)
