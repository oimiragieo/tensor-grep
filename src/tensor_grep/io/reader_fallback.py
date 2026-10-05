import gzip
import itertools
import os
from collections.abc import Iterator

from tensor_grep.io.base import IOBackend


class FallbackReader(IOBackend):
    def read_lines(self, file_path: str) -> Iterator[str]:
        if not os.path.exists(file_path):
            return

        is_gzip = file_path.endswith(".gz")
        open_func = gzip.open if is_gzip else open
        mode = "rt"

        yielded = 0
        try:
            with open_func(file_path, mode, encoding="utf-8") as f:
                for line in f:
                    yield str(line)
                    yielded += 1
        except UnicodeDecodeError:
            # Lines already yielded were valid UTF-8; resume after them, never from line 0.
            with open_func(file_path, mode, encoding="latin-1") as f:
                for line in itertools.islice(f, yielded, None):
                    yield str(line)
