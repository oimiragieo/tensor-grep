from __future__ import annotations
from typing import TYPE_CHECKING
from cudf_grep.backends.base import ComputeBackend
from cudf_grep.core.result import SearchResult, MatchLine

if TYPE_CHECKING:
    import cudf

class CuDFBackend(ComputeBackend):
    def __init__(self, chunk_size_mb: int = 512):
        self.chunk_size_mb = chunk_size_mb

    def is_available(self) -> bool:
        try:
            import cudf as _cudf
            return True
        except ImportError:
            return False

    def search(self, file_path: str, pattern: str) -> SearchResult:
        import cudf
        import os

        file_size = os.path.getsize(file_path)
        chunk_bytes = self.chunk_size_mb * 1024 * 1024
        matches: list[MatchLine] = []

        if file_size <= chunk_bytes:
            series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
            mask = series.str.contains(pattern, regex=True)
            matched = series[mask]
            for idx, text in zip(matched.index.to_pandas(), matched.to_pandas()):
                matches.append(MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path))
        else:
            offset = 0
            line_offset = 0
            while offset < file_size:
                size = min(chunk_bytes, file_size - offset)
                series = cudf.read_text(
                    file_path, delimiter="\n",
                    byte_range=(offset, size), strip_delimiters=True,
                )
                mask = series.str.contains(pattern, regex=True)
                matched = series[mask]
                for idx, text in zip(matched.index.to_pandas(), matched.to_pandas()):
                    matches.append(MatchLine(
                        line_number=line_offset + int(idx) + 1,
                        text=str(text), file=file_path,
                    ))
                line_offset += len(series)
                offset += size

        return SearchResult(matches=matches, total_files=1, total_matches=len(matches))
