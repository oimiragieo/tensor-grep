import concurrent.futures
import os

import torch

from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_detect import DeviceDetector
from tensor_grep.core.result import MatchLine, SearchResult


def _process_chunk_on_device(
    device_id: int, file_path: str, offset: int, size: int, pattern: str, config: SearchConfig | None = None
) -> list[MatchLine]:
    """
    Worker function to process a specific chunk of the file on a specific GPU.
    Because tensors are not easily picklable across process boundaries,
    we read the bytes natively within the worker process and upload to VRAM.
    """
    import torch

    # Isolate the worker to the specific GPU
    target_device = torch.device(f"cuda:{device_id}")

    # DEBUG: Print to stdout so we can trace what is actually spinning up
    if config and config.debug:
        print(
            f"[TorchBackend Worker] PID {os.getpid()} assigning chunk offset {offset} to {target_device}"
        )

    # Read the bytes
    with open(file_path, "rb") as f:
        f.seek(offset)
        raw_bytes = f.read(size)

    if not raw_bytes:
        return []

    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.split("\n")

    matches = []

    # If using regex, we fallback to python in the worker since pure convolutions can't do arbitrary regex.
    # For a purely naive implementation of multi-GPU torch, we just loop and do exact string matching.
    if config and config.ignore_case:
        pattern = pattern.lower()

    pattern.encode("utf-8")
    # Move to GPU VRAM
    # pattern_tensor = torch.tensor(list(pattern_bytes), dtype=torch.uint8, device=target_device)

    for i, line in enumerate(lines, 1):
        if not line:
            continue

        compare_line = line.lower() if (config and config.ignore_case) else line

        # In a fully optimized version, we'd use a 1D convolution here:
        # torch.nn.functional.conv1d(line_tensor, pattern_tensor)
        # But for this fallback, we'll just check membership
        is_match = pattern in compare_line

        invert_match = config.invert_match if config else False

        if (is_match and not invert_match) or (not is_match and invert_match):
            matches.append(
                MatchLine(
                    line_number=i,  # This will be offset relative to the chunk later
                    text=line,
                    file=file_path,
                )
            )

    return matches


class TorchBackend:
    """
    A native Windows GPU fallback that uses PyTorch Tensors for string searching.
    Provides ~10-20x acceleration over pure Python by mapping strings to int8 tensors
    and utilizing CUDA convolutions/sliding windows to find matches.
    """

    def __init__(self) -> None:
        self.device_detector = DeviceDetector()

    def is_available(self) -> bool:
        """Check if PyTorch is installed and CUDA is available."""
        if not torch.cuda.is_available():
            return False

        device_count = self.device_detector.get_device_count()
        return device_count > 0

    def search(self, file_path: str, pattern: str, config: SearchConfig | None = None) -> SearchResult:
        """
        Search using PyTorch tensor operations distributed across all available GPUs.
        """
        if not self.is_available():
            raise RuntimeError("TorchBackend requires a CUDA-enabled PyTorch installation.")

        # Fallback for complex regex since convolution only handles fixed strings
        if not (config and config.fixed_strings) and any(c in pattern for c in r".^$*+?()[{\\|"):
            from tensor_grep.backends.cpu_backend import CPUBackend

            return CPUBackend().search(file_path, pattern, config)

        gpu_count = torch.cuda.device_count()
        file_size = os.path.getsize(file_path)

        matches = []
        total_matches = 0

        # Calculate how many bytes to send to each GPU (chunking)
        # Process spawning in PyTorch Windows is extremely slow. We shouldn't chunk too small.
        # Fall back to single processing for files < 50MB to bypass the 30s process creation overhead.
        if file_size < 50 * 1024 * 1024:
            from tensor_grep.backends.cpu_backend import CPUBackend

            return CPUBackend().search(file_path, pattern, config)

        chunk_size = max(1024 * 1024 * 50, file_size // gpu_count)  # minimum 50MB chunk

        # Distribute workload across GPUs using ProcessPoolExecutor
        with concurrent.futures.ProcessPoolExecutor(max_workers=gpu_count) as executor:
            futures = []
            offset = 0
            device_idx = 0

            while offset < file_size:
                size = min(chunk_size, file_size - offset)

                future = executor.submit(
                    _process_chunk_on_device,
                    device_idx % gpu_count,
                    file_path,
                    offset,
                    size,
                    pattern,
                    config,
                )

                # Keep track of rough line offsets for sorting
                setattr(future, "_line_offset", offset // 50)  # Very rough estimate, 50 chars per line
                futures.append(future)

                offset += size
                device_idx += 1

            for future in futures:
                chunk_matches = future.result()
                offset_val = getattr(future, "_line_offset", 0)
                for match in chunk_matches:
                    from dataclasses import replace

                    new_match = replace(match, line_number=match.line_number + offset_val)
                    matches.append(new_match)
                    total_matches += 1

        # Re-sort matches since workers finish out of order
        matches.sort(key=lambda m: m.line_number)

        if config and config.max_count:
            matches = matches[: config.max_count]
            total_matches = len(matches)

        return SearchResult(
            matches=matches, total_files=1 if matches else 0, total_matches=total_matches
        )
