from collections.abc import Iterator
from typing import Protocol


class IOBackend(Protocol):
    def read_lines(self, file_path: str) -> Iterator[str]: ...
