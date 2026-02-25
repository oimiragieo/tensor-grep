from typing import Protocol, Optional
from tensor_grep.core.result import SearchResult
from tensor_grep.core.config import SearchConfig

class ComputeBackend(Protocol):
    def search(self, file_path: str, pattern: str, config: Optional[SearchConfig] = None) -> SearchResult:
        ...

    def is_available(self) -> bool:
        ...
