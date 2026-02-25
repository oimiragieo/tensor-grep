from typing import Protocol

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import SearchResult


class ComputeBackend(Protocol):
    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult: ...

    def is_available(self) -> bool: ...
