from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TYPE_CHECKING

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

if TYPE_CHECKING:
    pass


def _process_chunk_on_device(
    device_id: int,
    file_path: str,
    offset: int,
    size: int,
    pattern: str,
    config: SearchConfig | None = None,
) -> list[MatchLine]:
    import re

    import cudf
    import rmm

    try:
        rmm.reinitialize(devices=[device_id])
    except Exception:
        # Fallback to default RMM initialization if specific device mapping fails (common in WSL multiprocess)
        try:
            rmm.reinitialize()
        except Exception:
            pass

    series = cudf.read_text(
        file_path,
        delimiter="\n",
        byte_range=(offset, size),
        strip_delimiters=True,
    )

    flags = 0
    if config and (config.ignore_case or (config.smart_case and pattern.islower())):
        flags |= re.IGNORECASE

    mask = series.str.contains(pattern, regex=True, flags=flags)

    if config and config.invert_match:
        mask = ~mask

    matched = series[mask]

    matches = []
    for idx, text in zip(matched.index.to_pandas(), matched.to_pandas(), strict=False):
        matches.append(
            MatchLine(
                line_number=int(idx) + 1,
                text=str(text),
                file=file_path,
            )
        )

    return matches


class CuDFBackend(ComputeBackend):
    def __init__(self, chunk_sizes_mb: list[int] | None = None):
        self.chunk_sizes_mb = chunk_sizes_mb or [512]

    def is_available(self) -> bool:
        try:
            import importlib.util

            if not importlib.util.find_spec("cudf"):
                return False

            # Attempt a physical import to catch cudaErrorInsufficientDriver on systems
            # where the library is installed but the physical GPU drivers are missing.
            import cudf  # noqa: F401
            
            # Actually allocate a GPU tensor to force the RMM initialization hook.
            # If the driver is missing, this will throw CUDARuntimeError.
            cudf.Series([1])
            return True
        except Exception:
            return False

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        import os
        import re

        import cudf

        file_size = os.path.getsize(file_path)
        matches: list[MatchLine] = []

        total_capacity_bytes = sum(self.chunk_sizes_mb) * 1024 * 1024

        flags = 0
        if config and (config.ignore_case or (config.smart_case and pattern.islower())):
            flags |= re.IGNORECASE

        if file_size <= total_capacity_bytes and len(self.chunk_sizes_mb) == 1:
            series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
            mask = series.str.contains(pattern, regex=True, flags=flags)
            if config and config.invert_match:
                mask = ~mask
            matched = series[mask]
            for idx, text in zip(matched.index.to_pandas(), matched.to_pandas(), strict=False):
                matches.append(MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path))
        else:
            offset = 0
            line_offset = 0

            with ProcessPoolExecutor(max_workers=len(self.chunk_sizes_mb)) as executor:
                futures = []
                while offset < file_size:
                    for i, chunk_mb in enumerate(self.chunk_sizes_mb):
                        if offset >= file_size:
                            break

                        chunk_bytes = chunk_mb * 1024 * 1024
                        if chunk_bytes == 0:
                            # Prevent infinite loop if a chunk size evaluates to 0
                            chunk_bytes = 1024 * 1024

                        size = min(chunk_bytes, file_size - offset)

                        future = executor.submit(
                            _process_chunk_on_device, i, file_path, offset, size, pattern, config
                        )
                        # We attach the line_offset to the future for correct numbering later
                        future._line_offset = line_offset
                        futures.append(future)

                        offset += size
                        line_offset += (
                            size // 50
                        )  # Rough estimate for fast numbering, true line offset is complex for chunked reads

                for future in as_completed(futures):
                    chunk_matches = future.result()
                    for match in chunk_matches:
                        match.line_number += future._line_offset
                        matches.append(match)

            # Re-sort matches since they might finish out of order
            matches.sort(key=lambda m: m.line_number)

        return SearchResult(matches=matches, total_files=1, total_matches=len(matches))
