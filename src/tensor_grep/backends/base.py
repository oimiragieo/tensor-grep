from typing import Protocol

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import SearchResult


class BackendExecutionError(RuntimeError):
    """A search backend failed at runtime for a reason that is NOT an invalid regex.

    Covers native panics, encoding/IO errors, version skew, and GPU/CUDA/OOM faults.
    Backends MUST raise this instead of returning an empty ``SearchResult``, so a real
    failure is never reported to the user as a clean no-match; callers may catch it to
    retry on the CPU fallback (audit B2/I1).
    """


class ComputeBackend(Protocol):
    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult: ...

    def is_available(self) -> bool: ...
