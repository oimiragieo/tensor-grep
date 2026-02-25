from typing import Iterator
from cudf_grep.io.base import IOBackend

class CuDFReader(IOBackend):
    def read_lines(self, file_path: str) -> Iterator[str]:
        try:
            import cudf
        except ImportError:
            raise ImportError("cudf is required to use CuDFReader")
            
        series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
        for text in series.to_pandas():
            yield str(text) + "\n"
