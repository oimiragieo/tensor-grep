from typing import Protocol

from tensor_grep.core.result import SearchResult


class OutputFormatter(Protocol):
    def format(self, result: SearchResult) -> str: ...
