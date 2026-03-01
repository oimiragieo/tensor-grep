from __future__ import annotations

from contextlib import nullcontext
from typing import Any


def nvtx_range(message: str, color: str = "blue") -> Any:
    """
    Return an NVTX context manager if nvtx is installed, otherwise a no-op context.
    """
    try:
        import nvtx  # type: ignore[import-not-found]

        return nvtx.annotate(message=message, color=color, domain="tensor-grep")
    except Exception:
        return nullcontext()
