from collections.abc import Iterator
from typing import Any

from tensor_grep.io.base import IOBackend


class CuDFReader(IOBackend):
    def read_lines(self, file_path: str) -> Iterator[str]:
        try:
            import cudf
        except ImportError as e:
            raise ImportError("cudf is required to use CuDFReader") from e

        series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
        for text in series.to_pandas():
            yield str(text) + "\n"

    def read_to_gpu(self, file_path: str) -> Any:
        try:
            import cudf
        except ImportError as e:
            raise ImportError("cudf is required to use CuDFReader") from e
        return cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
