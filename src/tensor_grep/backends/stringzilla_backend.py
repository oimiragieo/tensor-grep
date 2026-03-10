import hashlib
import json
import os
from pathlib import Path
from typing import ClassVar

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class StringZillaBackend(ComputeBackend):
    """
    A backend utilizing the StringZilla native C++/SIMD library.
    It specializes in ultra-fast exact string matching and line splitting,
    avoiding standard Python regex overhead completely for simple literal searches.
    """

    _shared_index_cache: ClassVar[
        dict[tuple[str, bool], tuple[tuple[int, int], list[str], dict[str, list[int]]]]
    ] = {}

    @classmethod
    def _clear_shared_caches(cls) -> None:
        cls._shared_index_cache.clear()

    def is_available(self) -> bool:
        try:
            import importlib.util

            return importlib.util.find_spec("stringzilla") is not None
        except ImportError:
            return False

    def _is_index_enabled(self) -> bool:
        return os.environ.get("TENSOR_GREP_STRING_INDEX", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    def _build_file_signature(self, file_path: str) -> tuple[int, int]:
        stat_result = os.stat(file_path)
        return stat_result.st_mtime_ns, stat_result.st_size

    def _get_index_cache_dir(self) -> Path:
        override = os.environ.get("TENSOR_GREP_STRING_INDEX_DIR")
        if override:
            return Path(override).expanduser().resolve()
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                return Path(local_appdata) / "tensor-grep" / "string-index"
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home) / "tensor-grep" / "string-index"
        return Path.home() / ".cache" / "tensor-grep" / "string-index"

    def _get_index_cache_path(self, file_path: str, ignore_case: bool) -> Path:
        digest = hashlib.sha256(
            f"{Path(file_path).resolve()}::{int(ignore_case)}".encode()
        ).hexdigest()
        return self._get_index_cache_dir() / f"{digest}.json"

    def _build_line_trigram_index(self, lines: list[str]) -> dict[str, list[int]]:
        index: dict[str, set[int]] = {}
        for line_idx, line in enumerate(lines):
            if len(line) < 3:
                continue
            for start in range(len(line) - 2):
                trigram = line[start : start + 3]
                index.setdefault(trigram, set()).add(line_idx)
        return {trigram: sorted(line_numbers) for trigram, line_numbers in index.items()}

    def _extract_trigrams(self, pattern: str) -> list[str]:
        return [pattern[i : i + 3] for i in range(len(pattern) - 2)]

    def _load_cached_index(
        self, file_path: str, ignore_case: bool
    ) -> tuple[list[str], dict[str, list[int]]] | None:
        cache_key = (file_path, ignore_case)
        cache_signature = self._build_file_signature(file_path)
        cached = self._shared_index_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            return cached[1], cached[2]

        if not self._is_index_enabled():
            return None

        cache_path = self._get_index_cache_path(file_path, ignore_case)
        if not cache_path.exists():
            return None

        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("file_signature") != list(cache_signature):
            return None

        raw_lines = payload.get("lines")
        raw_index = payload.get("trigram_index")
        if not isinstance(raw_lines, list) or not isinstance(raw_index, dict):
            return None

        lines = [str(line) for line in raw_lines]
        trigram_index: dict[str, list[int]] = {}
        for trigram, values in raw_index.items():
            if not isinstance(trigram, str) or not isinstance(values, list):
                return None
            trigram_index[trigram] = [int(v) for v in values]

        self._shared_index_cache[cache_key] = (cache_signature, lines, trigram_index)
        return lines, trigram_index

    def _persist_index(
        self,
        file_path: str,
        ignore_case: bool,
        lines: list[str],
        trigram_index: dict[str, list[int]],
    ) -> None:
        cache_signature = self._build_file_signature(file_path)
        self._shared_index_cache[(file_path, ignore_case)] = (cache_signature, lines, trigram_index)
        if not self._is_index_enabled():
            return

        cache_path = self._get_index_cache_path(file_path, ignore_case)
        payload = {
            "file_signature": list(cache_signature),
            "lines": lines,
            "trigram_index": trigram_index,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return

    def _search_with_index(
        self, file_path: str, pattern: str, ignore_case: bool
    ) -> SearchResult | None:
        if len(pattern) < 3:
            return None

        cached = self._load_cached_index(file_path, ignore_case)
        routing_reason = "stringzilla_fixed_strings_index_cache"
        if cached is None:
            with open(file_path, encoding="utf-8") as f_obj:
                source_lines = f_obj.read().splitlines()
            normalized_lines = (
                [line.lower() for line in source_lines] if ignore_case else source_lines
            )
            trigram_index = self._build_line_trigram_index(normalized_lines)
            self._persist_index(file_path, ignore_case, source_lines, trigram_index)
            routing_reason = "stringzilla_fixed_strings_index"
        else:
            source_lines, trigram_index = cached

        candidate_sets = []
        normalized_pattern = pattern.lower() if ignore_case else pattern
        for trigram in self._extract_trigrams(normalized_pattern):
            line_numbers = trigram_index.get(trigram)
            if not line_numbers:
                return SearchResult(
                    matches=[],
                    total_files=0,
                    total_matches=0,
                    routing_backend="StringZillaBackend",
                    routing_reason=routing_reason,
                    routing_distributed=False,
                    routing_worker_count=1,
                )
            candidate_sets.append(set(line_numbers))

        candidate_line_indexes = sorted(set.intersection(*candidate_sets)) if candidate_sets else []
        matches = []
        for line_idx in candidate_line_indexes:
            line = source_lines[line_idx]
            haystack = line.lower() if ignore_case else line
            if normalized_pattern in haystack:
                matches.append(MatchLine(line_number=line_idx + 1, text=line, file=file_path))

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
            routing_backend="StringZillaBackend",
            routing_reason=routing_reason,
            routing_distributed=False,
            routing_worker_count=1,
        )

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        import stringzilla as sz

        try:
            ignore_case = bool(config and config.ignore_case)
            if config and config.fixed_strings:
                indexed = self._search_with_index(file_path, pattern, ignore_case)
                if indexed is not None:
                    return indexed

            # Read file via normal python IO for now, wrap in sz.Str
            # In a real implementation we might memory-map directly.
            with open(file_path, encoding="utf-8") as f_obj:
                content = f_obj.read()

            sz_str = sz.Str(content)

            # Since StringZilla 4.x, we can split by lines extremely fast
            lines = sz_str.splitlines()
            matches = []

            # Evaluate using stringzilla's native find
            for i, line in enumerate(lines):
                haystack = str(line).lower() if ignore_case else line
                needle = pattern.lower() if ignore_case else pattern
                if haystack.find(needle) != -1:
                    matches.append(MatchLine(line_number=i + 1, text=str(line), file=file_path))

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="StringZillaBackend",
                routing_reason="stringzilla_fixed_strings",
                routing_distributed=False,
                routing_worker_count=1,
            )

        except Exception as e:
            raise e
