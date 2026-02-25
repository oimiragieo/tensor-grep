from typing import Protocol
from cudf_grep.core.result import SearchResult

class ComputeBackend(Protocol):
    def search(self, file_path: str, pattern: str) -> SearchResult:
        ...

    def is_available(self) -> bool:
        ...
