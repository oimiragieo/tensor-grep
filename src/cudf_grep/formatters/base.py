from typing import Protocol
from cudf_grep.core.result import SearchResult

class OutputFormatter(Protocol):
    def format(self, result: SearchResult) -> str:
        ...
