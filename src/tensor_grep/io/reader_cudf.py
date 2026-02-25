from typing import Iterator
from tensor_grep.io.base import IOBackend

class CuDFReader(IOBackend):
    def read_lines(self, file_path: str) -> Iterator[str]:
        try:
            import cudf
        except ImportError:
            raise ImportError("cudf is required to use CuDFReader")
            
        series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
        for text in series.to_pandas():
            yield str(text) + "\n"

    def read_to_gpu(self, file_path: str) -> "cudf.Series":  # type: ignore
        try:
            import cudf
        except ImportError:
            raise ImportError("cudf is required to use CuDFReader")
        return cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
