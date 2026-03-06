from concurrent.futures import ThreadPoolExecutor
from typing import Any

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.hardware.device_detect import DeviceDetector
from tensor_grep.core.result import MatchLine, SearchResult

_REGEX_META = set(r".^$*+?()[]{}\|")


class TorchBackend(ComputeBackend):
    """
    CUDA fallback backend for systems without cuDF.
    Uses tensor operations for literal substring matching on GPU.
    """

    def __init__(
        self,
        device_ids: list[int] | None = None,
        chunk_sizes_mb: list[int] | None = None,
    ) -> None:
        self.device_detector = DeviceDetector()
        self.device_ids = device_ids
        self.chunk_sizes_mb = chunk_sizes_mb

    @staticmethod
    def _build_round_robin_shards(
        numbered_lines: list[tuple[int, str]], shard_count: int
    ) -> list[list[tuple[int, str]]]:
        shards: list[list[tuple[int, str]]] = [[] for _ in range(shard_count)]
        for index, item in enumerate(numbered_lines):
            shards[index % shard_count].append(item)
        return shards

    @staticmethod
    def _build_weighted_shards(
        numbered_lines: list[tuple[int, str]], weights: list[int]
    ) -> list[list[tuple[int, str]]]:
        shard_count = len(weights)
        shards: list[list[tuple[int, str]]] = [[] for _ in range(shard_count)]
        if not numbered_lines or shard_count == 0:
            return shards

        safe_weights = [max(0, int(weight)) for weight in weights]
        total_weight = sum(safe_weights)
        if total_weight <= 0:
            return TorchBackend._build_round_robin_shards(numbered_lines, shard_count)

        total_lines = len(numbered_lines)
        targets = [(total_lines * weight) // total_weight for weight in safe_weights]
        remainder = total_lines - sum(targets)
        for slot in range(remainder):
            targets[slot % shard_count] += 1

        cursor = 0
        for slot, target in enumerate(targets):
            next_cursor = cursor + target
            shards[slot] = numbered_lines[cursor:next_cursor]
            cursor = next_cursor
        if cursor < total_lines:
            shards[-1].extend(numbered_lines[cursor:])
        return shards

    def is_available(self) -> bool:
        try:
            import importlib.util

            if not importlib.util.find_spec("torch"):
                return False
            import torch

            if not bool(getattr(torch.cuda, "is_available", lambda: False)()):
                return False
        except Exception:
            return False

        return self.device_detector.get_device_count() > 0

    def _contains_literal_torch(
        self, torch: Any, line: str, pattern_tensor: Any, pattern_len: int, device: Any
    ) -> bool:
        line_bytes = line.encode("utf-8", errors="replace")
        if len(line_bytes) < pattern_len:
            return False

        line_tensor = torch.tensor(list(line_bytes), dtype=torch.uint8, device=device)
        windows = line_tensor.unfold(0, pattern_len, 1)
        return bool((windows == pattern_tensor).all(dim=1).any().item())

    def _search_lines_on_device(
        self,
        *,
        torch: Any,
        numbered_lines: list[tuple[int, str]],
        query: str,
        cfg: SearchConfig,
        file_path: str,
        pattern_tensor: Any,
        pattern_len: int,
        device: Any,
    ) -> list[MatchLine]:
        matches: list[MatchLine] = []
        for line_number, line in numbered_lines:
            compare_line = (
                line.lower() if cfg.ignore_case or (cfg.smart_case and query.islower()) else line
            )
            is_match = self._contains_literal_torch(
                torch=torch,
                line=compare_line,
                pattern_tensor=pattern_tensor,
                pattern_len=pattern_len,
                device=device,
            )
            if cfg.invert_match:
                is_match = not is_match
            if is_match:
                matches.append(MatchLine(line_number=line_number, text=line, file=file_path))
        return matches

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if not self.is_available():
            raise RuntimeError("TorchBackend requires CUDA-enabled PyTorch.")

        cfg = config or SearchConfig()

        pattern_is_regex = any(char in _REGEX_META for char in pattern)
        if pattern_is_regex and not cfg.fixed_strings:
            # Regex execution is delegated to CPU backend until a true GPU regex kernel exists.
            from tensor_grep.backends.cpu_backend import CPUBackend

            return CPUBackend().search(file_path, pattern, cfg)

        import torch

        if self.device_ids is not None:
            resolved_device_ids = self.device_ids
        elif hasattr(self.device_detector, "enumerate_device_ids"):
            resolved_device_ids = list(self.device_detector.enumerate_device_ids())
        else:
            resolved_device_ids = self.device_detector.get_device_ids()
        if not resolved_device_ids:
            resolved_device_ids = [0]
        devices = [torch.device(f"cuda:{device_id}") for device_id in resolved_device_ids]

        with open(file_path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()

        matches: list[MatchLine] = []
        query = (
            pattern.lower()
            if cfg.ignore_case or (cfg.smart_case and pattern.islower())
            else pattern
        )

        pattern_bytes = query.encode("utf-8", errors="replace")
        if not pattern_bytes:
            return SearchResult(matches=[], total_files=0, total_matches=0)
        pattern_len = len(pattern_bytes)
        pattern_tensors = [
            torch.tensor(list(pattern_bytes), dtype=torch.uint8, device=device)
            for device in devices
        ]

        numbered_lines = list(enumerate(lines, 1))
        if len(devices) > 1:
            if self.chunk_sizes_mb and len(self.chunk_sizes_mb) == len(devices):
                shards = self._build_weighted_shards(numbered_lines, self.chunk_sizes_mb)
            else:
                shards = self._build_round_robin_shards(numbered_lines, len(devices))

            with ThreadPoolExecutor(max_workers=len(devices)) as executor:
                futures = []
                for slot, device in enumerate(devices):
                    futures.append(
                        executor.submit(
                            self._search_lines_on_device,
                            torch=torch,
                            numbered_lines=shards[slot],
                            query=query,
                            cfg=cfg,
                            file_path=file_path,
                            pattern_tensor=pattern_tensors[slot],
                            pattern_len=pattern_len,
                            device=device,
                        )
                    )
                for future in futures:
                    matches.extend(future.result())
            matches.sort(key=lambda item: item.line_number)
            if cfg.max_count:
                matches = matches[: cfg.max_count]
        else:
            for line_number, line in numbered_lines:
                is_match = self._contains_literal_torch(
                    torch=torch,
                    line=(
                        line.lower()
                        if cfg.ignore_case or (cfg.smart_case and query.islower())
                        else line
                    ),
                    pattern_tensor=pattern_tensors[0],
                    pattern_len=pattern_len,
                    device=devices[0],
                )
                if cfg.invert_match:
                    is_match = not is_match

                if is_match:
                    matches.append(MatchLine(line_number=line_number, text=line, file=file_path))
                    if cfg.max_count and len(matches) >= cfg.max_count:
                        break

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
            routing_distributed=len(devices) > 1,
            routing_worker_count=len(devices) if len(devices) > 1 else 0,
        )
