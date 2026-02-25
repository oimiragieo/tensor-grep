from typing import Protocol, Iterator

class IOBackend(Protocol):
    def read_lines(self, file_path: str) -> Iterator[str]:
        ...
