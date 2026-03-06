from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TYPE_CHECKING

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _process_chunk_on_device(
    device_id: int,
    file_path: str,
    offset: int,
    size: int,
    pattern: str,
    config: SearchConfig | None = None,
) -> tuple[list[MatchLine], int]:
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

    return matches, len(series)


class CuDFBackend(ComputeBackend):
    def __init__(
        self, chunk_sizes_mb: list[int] | None = None, device_ids: list[int] | None = None
    ):
        self.chunk_sizes_mb = chunk_sizes_mb or [512]
        self.device_ids = device_ids or list(range(len(self.chunk_sizes_mb)))

    @staticmethod
    def _normalize_device_chunks(
        device_chunks_mb: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        """
        Deduplicate device entries while preserving first-seen order.
        If a device appears multiple times, keep the largest configured chunk size.
        """
        normalized: list[tuple[int, int]] = []
        index_by_device: dict[int, int] = {}
        for device_id, chunk_mb in device_chunks_mb:
            if device_id not in index_by_device:
                index_by_device[device_id] = len(normalized)
                normalized.append((device_id, chunk_mb))
                continue
            slot = index_by_device[device_id]
            existing_device_id, existing_chunk_mb = normalized[slot]
            if chunk_mb > existing_chunk_mb:
                normalized[slot] = (existing_device_id, chunk_mb)
        return normalized

    @staticmethod
    def _build_execution_plan(
        *,
        file_size: int,
        device_chunks_mb: list[tuple[int, int]],
    ) -> list[tuple[int, int, int]]:
        """
        Build a (device_id, offset, size) plan for chunked execution.
        Chunks are assigned round-robin across concrete device IDs.
        """
        if file_size <= 0 or not device_chunks_mb:
            return []

        plan: list[tuple[int, int, int]] = []
        offset = 0
        slot = 0
        while offset < file_size:
            device_id, chunk_mb = device_chunks_mb[slot % len(device_chunks_mb)]
            chunk_bytes = max(chunk_mb * 1024 * 1024, 1024 * 1024)
            size = min(chunk_bytes, file_size - offset)
            plan.append((device_id, offset, size))
            offset += size
            slot += 1
        return plan

    def is_available(self) -> bool:
        try:
            import importlib.util

            if not importlib.util.find_spec("cudf"):
                return False

            # Attempt a physical import to catch cudaErrorInsufficientDriver on systems
            # where the library is installed but the physical GPU drivers are missing.
            import cudf

            # Actually allocate a GPU tensor to force the RMM initialization hook.
            # If the driver is missing, this will throw CUDARuntimeError.
            cudf.Series([1])
            return True
        except Exception:
            return False

    def _search_distributed(
        self,
        *,
        file_path: str,
        pattern: str,
        file_size: int,
        device_chunks_mb: list[tuple[int, int]],
        config: SearchConfig | None,
    ) -> tuple[list[MatchLine], int]:
        matches: list[MatchLine] = []
        normalized_device_chunks = self._normalize_device_chunks([
            (device_id, chunk_mb) for device_id, chunk_mb in device_chunks_mb
        ])
        execution_plan = self._build_execution_plan(
            file_size=file_size,
            device_chunks_mb=normalized_device_chunks,
        )
        if len(execution_plan) == 1:
            device_id, chunk_offset, chunk_size = execution_plan[0]
            single_matches, _ = _process_chunk_on_device(
                device_id,
                file_path,
                chunk_offset,
                chunk_size,
                pattern,
                config,
            )
            return sorted(single_matches, key=lambda m: m.line_number), 1

        max_workers = min(len(normalized_device_chunks), len(execution_plan))
        if max_workers <= 1:
            # Avoid process-pool startup overhead when all planned chunks map to one worker.
            cumulative_line_offset = 0
            for device_id, chunk_offset, chunk_size in execution_plan:
                chunk_matches, chunk_line_count = _process_chunk_on_device(
                    device_id,
                    file_path,
                    chunk_offset,
                    chunk_size,
                    pattern,
                    config,
                )
                for match in chunk_matches:
                    object.__setattr__(
                        match, "line_number", match.line_number + cumulative_line_offset
                    )
                    matches.append(match)
                cumulative_line_offset += chunk_line_count

            matches.sort(key=lambda m: m.line_number)
            return matches, 1

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for task_index, (device_id, chunk_offset, chunk_size) in enumerate(execution_plan):
                future = executor.submit(
                    _process_chunk_on_device,
                    device_id,
                    file_path,
                    chunk_offset,
                    chunk_size,
                    pattern,
                    config,
                )
                future._task_index = task_index  # type: ignore[attr-defined]
                futures.append(future)

            ordered_chunk_results: dict[int, tuple[list[MatchLine], int]] = {}
            for future in as_completed(futures):
                chunk_matches, chunk_line_count = future.result()
                task_index = getattr(future, "_task_index", 0)
                ordered_chunk_results[task_index] = (chunk_matches, chunk_line_count)

            cumulative_line_offset = 0
            for task_index in sorted(ordered_chunk_results):
                chunk_matches, chunk_line_count = ordered_chunk_results[task_index]
                for match in chunk_matches:
                    object.__setattr__(
                        match, "line_number", match.line_number + cumulative_line_offset
                    )
                    matches.append(match)
                cumulative_line_offset += chunk_line_count

        matches.sort(key=lambda m: m.line_number)
        return matches, max_workers

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
            # PHASE 3: Zero-Copy ingestion via PyCapsule if tensor-grep rust core is available
            try:
                import pyarrow as pa

                from tensor_grep.rust_core import read_mmap_to_arrow

                # 1. Rust memory maps the file and returns an Arrow PyCapsule
                pycapsule = read_mmap_to_arrow(file_path)
                zero_copy_array = pa.array(pycapsule)

                # 2. cuDF ingests the Arrow memory directly into VRAM
                series = cudf.Series.from_arrow(zero_copy_array)

                if config and config.use_jit:
                    try:
                        # Attempt to use JIT compilation for complex string patterns
                        compiled_pattern = cudf.core.column.string.compile_regex_jit(
                            pattern, flags=flags
                        )
                        mask = series.str.contains(compiled_pattern, regex=True)
                    except AttributeError:
                        mask = series.str.contains(pattern, regex=True, flags=flags)
                else:
                    mask = series.str.contains(pattern, regex=True, flags=flags)

                if config and config.invert_match:
                    mask = ~mask
                matched = series[mask]
                for idx, text in zip(matched.index.to_pandas(), matched.to_pandas(), strict=False):
                    matches.append(
                        MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path)
                    )
            except ImportError:
                # Fallback to cuDF's native text reader if the rust bridge isn't compiled
                # or if the PyCapsule conversion fails during testing environments
                series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
                mask = series.str.contains(pattern, regex=True, flags=flags)
                if config and config.invert_match:
                    mask = ~mask
                matched = series[mask]
                for idx, text in zip(matched.index.to_pandas(), matched.to_pandas(), strict=False):
                    matches.append(
                        MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path)
                    )
            except Exception as exc:
                logger.warning(
                    "Zero-copy Rust bridge failed for %s, using native cudf.read_text fallback: %s",
                    file_path,
                    exc,
                )
                series = cudf.read_text(file_path, delimiter="\n", strip_delimiters=True)
                mask = series.str.contains(pattern, regex=True, flags=flags)
                if config and config.invert_match:
                    mask = ~mask
                matched = series[mask]
                for idx, text in zip(matched.index.to_pandas(), matched.to_pandas(), strict=False):
                    matches.append(
                        MatchLine(line_number=int(idx) + 1, text=str(text), file=file_path)
                    )

        else:
            # PHASE 3.1: VRAM Chunking for Large Files
            device_chunks_mb = list(zip(self.device_ids, self.chunk_sizes_mb, strict=False))
            if not device_chunks_mb:
                device_chunks_mb = [(0, 512)]

            # For multi-GPU configurations, distributed fanout is the primary runtime path.
            if len(device_chunks_mb) > 1:
                matches, worker_count = self._search_distributed(
                    file_path=file_path,
                    pattern=pattern,
                    file_size=file_size,
                    device_chunks_mb=device_chunks_mb,
                    config=config,
                )
                return SearchResult(
                    matches=matches,
                    total_files=1,
                    total_matches=len(matches),
                    routing_distributed=worker_count > 1,
                    routing_worker_count=worker_count,
                )

            chunked_processing_succeeded = False
            try:
                import pyarrow as pa

                from tensor_grep.core.hardware.memory_manager import MemoryManager
                from tensor_grep.rust_core import read_mmap_to_arrow_chunked

                # Dynamically calculate VRAM chunk sizes to prevent CUDA Out-Of-Memory exceptions
                memory_manager = MemoryManager()
                # Default to 80% of free VRAM if NVML is available, otherwise fallback to configured size
                vram_budget = memory_manager.get_vram_budget_mb()

                if vram_budget > 0:
                    chunk_bytes = vram_budget * 1024 * 1024
                else:
                    chunk_bytes = self.chunk_sizes_mb[0] * 1024 * 1024

                if chunk_bytes == 0:
                    chunk_bytes = 1024 * 1024

                try:
                    from opentelemetry import trace

                    tracer = trace.get_tracer(__name__)
                    with tracer.start_as_current_span("read_mmap_to_arrow_chunked"):
                        pycapsule_chunks = read_mmap_to_arrow_chunked(file_path, chunk_bytes)
                except ImportError:
                    pycapsule_chunks = read_mmap_to_arrow_chunked(file_path, chunk_bytes)

                line_offset = 0
                for capsule in pycapsule_chunks:
                    try:
                        from opentelemetry import trace

                        tracer = trace.get_tracer(__name__)
                        with tracer.start_as_current_span("cudf.Series.from_arrow"):
                            zero_copy_array = pa.array(capsule)
                            series = cudf.Series.from_arrow(zero_copy_array)

                            mask = series.str.contains(pattern, regex=True, flags=flags)
                    except ImportError:
                        zero_copy_array = pa.array(capsule)
                        series = cudf.Series.from_arrow(zero_copy_array)

                        mask = series.str.contains(pattern, regex=True, flags=flags)

                    if config and config.invert_match:
                        mask = ~mask
                    matched = series[mask]

                    chunk_lines_count = len(series)
                    for idx, text in zip(
                        matched.index.to_pandas(), matched.to_pandas(), strict=False
                    ):
                        matches.append(
                            MatchLine(
                                line_number=int(idx) + 1 + line_offset,
                                text=str(text),
                                file=file_path,
                            )
                        )

                    line_offset += chunk_lines_count

                    # Force VRAM cleanup before loading the next chunk
                    del series
                    del matched
                    del mask
                    cudf.core.buffer.acquire_spill_lock()

                chunked_processing_succeeded = True

            except ImportError:
                # Fallback to pure Python multi-processing CPU mapping
                logger.debug(
                    "Chunked PyArrow/cudf path unavailable for %s, falling back", file_path
                )
            except Exception as exc:
                logger.warning(
                    "Chunked PyArrow/cudf path failed for %s, falling back to process pool: %s",
                    file_path,
                    exc,
                )

            if chunked_processing_succeeded:
                matches.sort(key=lambda m: m.line_number)
                return SearchResult(matches=matches, total_files=1, total_matches=len(matches))
            matches, worker_count = self._search_distributed(
                file_path=file_path,
                pattern=pattern,
                file_size=file_size,
                device_chunks_mb=device_chunks_mb,
                config=config,
            )
            return SearchResult(
                matches=matches,
                total_files=1,
                total_matches=len(matches),
                routing_distributed=worker_count > 1,
                routing_worker_count=worker_count,
            )

        return SearchResult(matches=matches, total_files=1, total_matches=len(matches))
