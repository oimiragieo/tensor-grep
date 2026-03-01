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

    def __init__(self) -> None:
        self.device_detector = DeviceDetector()

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

        device = torch.device("cuda:0")

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
        pattern_tensor = torch.tensor(list(pattern_bytes), dtype=torch.uint8, device=device)

        for line_number, line in enumerate(lines, 1):
            compare_line = (
                line.lower() if cfg.ignore_case or (cfg.smart_case and pattern.islower()) else line
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
                if cfg.max_count and len(matches) >= cfg.max_count:
                    break

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
        )
