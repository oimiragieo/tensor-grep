import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path
from typing import ClassVar

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

_STRING_INDEX_CACHE_MAX_ENTRIES_ENV = "TENSOR_GREP_STRING_INDEX_CACHE_MAX_ENTRIES"
_DEFAULT_STRING_INDEX_CACHE_MAX_ENTRIES = 512


class StringZillaBackend(ComputeBackend):
    """
    A backend utilizing the StringZilla native C++/SIMD library.
    It specializes in ultra-fast exact string matching and line splitting,
    avoiding standard Python regex overhead completely for simple literal searches.
    """

    _shared_index_cache: ClassVar[
        OrderedDict[
            tuple[str, bool, bool],
            tuple[tuple[int, int], list[str], dict[str, list[int]]],
        ]
    ] = OrderedDict()

    @classmethod
    def _clear_shared_caches(cls) -> None:
        cls._shared_index_cache.clear()

    @staticmethod
    def _configured_positive_int(env_var: str, default: int) -> int:
        raw_value = os.environ.get(env_var)
        if raw_value is None:
            return default
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @classmethod
    def _index_cache_max_entries(cls) -> int:
        return cls._configured_positive_int(
            _STRING_INDEX_CACHE_MAX_ENTRIES_ENV,
            _DEFAULT_STRING_INDEX_CACHE_MAX_ENTRIES,
        )

    @classmethod
    def _remember_index(
        cls,
        cache_key: tuple[str, bool, bool],
        cache_entry: tuple[tuple[int, int], list[str], dict[str, list[int]]],
    ) -> None:
        cls._shared_index_cache.pop(cache_key, None)
        cls._shared_index_cache[cache_key] = cache_entry
        while len(cls._shared_index_cache) > cls._index_cache_max_entries():
            cls._shared_index_cache.popitem(last=False)

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

    @staticmethod
    def _should_search_binary_as_text(config: SearchConfig | None) -> bool:
        return bool(config and (config.text or config.binary))

    @staticmethod
    def _load_searchable_text(file_path: str, *, treat_binary_as_text: bool) -> str | None:
        if treat_binary_as_text:
            raw = Path(file_path).read_bytes()
            return raw.decode("utf-8", errors="replace")

        with open(file_path, "rb") as binary_handle:
            if b"\x00" in binary_handle.read(4096):
                return None

        try:
            with open(file_path, encoding="utf-8") as text_handle:
                return text_handle.read()
        except UnicodeDecodeError:
            return None

    def _get_index_cache_path(
        self, file_path: str, ignore_case: bool, treat_binary_as_text: bool
    ) -> Path:
        digest = hashlib.sha256(
            f"{Path(file_path).resolve()}::{int(ignore_case)}::{int(treat_binary_as_text)}".encode()
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

    @staticmethod
    def _compress_line_indexes(line_indexes: list[int]) -> list[list[int]]:
        if not line_indexes:
            return []

        ranges: list[list[int]] = []
        start = line_indexes[0]
        end = line_indexes[0]
        for line_index in line_indexes[1:]:
            if line_index == end + 1:
                end = line_index
                continue
            ranges.append([start, end])
            start = line_index
            end = line_index
        ranges.append([start, end])
        return ranges

    @staticmethod
    def _decompress_line_indexes(encoded_ranges: list[list[int]]) -> list[int]:
        line_indexes: list[int] = []
        for start, end in encoded_ranges:
            line_indexes.extend(range(start, end + 1))
        return line_indexes

    @staticmethod
    def _intersect_sorted_line_indexes(postings: list[list[int]]) -> list[int]:
        if not postings:
            return []
        shared = postings[0]
        for posting in postings[1:]:
            left_index = 0
            right_index = 0
            intersection: list[int] = []
            while left_index < len(shared) and right_index < len(posting):
                left = shared[left_index]
                right = posting[right_index]
                if left == right:
                    intersection.append(left)
                    left_index += 1
                    right_index += 1
                elif left < right:
                    left_index += 1
                else:
                    right_index += 1
            if not intersection:
                return []
            shared = intersection
        return shared

    def _extract_trigrams(self, pattern: str) -> list[str]:
        return [pattern[i : i + 3] for i in range(len(pattern) - 2)]

    def _load_cached_index(
        self, file_path: str, ignore_case: bool, treat_binary_as_text: bool
    ) -> tuple[list[str], dict[str, list[int]]] | None:
        cache_key = (file_path, ignore_case, treat_binary_as_text)
        cache_signature = self._build_file_signature(file_path)
        cached = self._shared_index_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            self._shared_index_cache.move_to_end(cache_key)
            return cached[1], cached[2]
        if cached:
            self._shared_index_cache.pop(cache_key, None)

        if not self._is_index_enabled():
            return None

        cache_path = self._get_index_cache_path(file_path, ignore_case, treat_binary_as_text)
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

        self._remember_index(cache_key, (cache_signature, lines, trigram_index))
        return lines, trigram_index

    def _persist_index(
        self,
        file_path: str,
        ignore_case: bool,
        treat_binary_as_text: bool,
        lines: list[str],
        trigram_index: dict[str, list[int]],
    ) -> None:
        cache_signature = self._build_file_signature(file_path)
        self._remember_index(
            (file_path, ignore_case, treat_binary_as_text),
            (
                cache_signature,
                lines,
                trigram_index,
            ),
        )
        if not self._is_index_enabled():
            return

        cache_path = self._get_index_cache_path(file_path, ignore_case, treat_binary_as_text)
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
        self, file_path: str, pattern: str, config: SearchConfig | None, ignore_case: bool
    ) -> SearchResult | None:
        if len(pattern) < 3:
            return None

        if config and config.invert_match:
            # H5: the trigram index only answers "which lines contain every trigram
            # of the pattern" -- there is no cheap index-based way to enumerate the
            # lines that DON'T contain the pattern. Fall through to the full-scan
            # path in search(), which honors invert_match directly, rather than
            # silently returning the (wrong, non-inverted) indexed result.
            return None

        treat_binary_as_text = self._should_search_binary_as_text(config)
        cached = self._load_cached_index(file_path, ignore_case, treat_binary_as_text)
        routing_reason = "stringzilla_fixed_strings_index_cache"
        if cached is None:
            content = self._load_searchable_text(
                file_path, treat_binary_as_text=treat_binary_as_text
            )
            if content is None:
                return SearchResult(
                    matches=[],
                    total_files=0,
                    total_matches=0,
                    routing_backend="StringZillaBackend",
                    routing_reason="stringzilla_fixed_strings_skipped_binary",
                    routing_distributed=False,
                    routing_worker_count=1,
                )
            source_lines = content.splitlines()
            normalized_lines = (
                [line.lower() for line in source_lines] if ignore_case else source_lines
            )
            trigram_index = self._build_line_trigram_index(normalized_lines)
            self._persist_index(
                file_path,
                ignore_case,
                treat_binary_as_text,
                source_lines,
                trigram_index,
            )
            routing_reason = "stringzilla_fixed_strings_index"
        else:
            source_lines, trigram_index = cached

        postings: list[list[int]] = []
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
            postings.append(line_numbers)

        candidate_line_indexes = self._intersect_sorted_line_indexes(postings)
        matches = []
        max_count = config.max_count if config else None
        for line_idx in candidate_line_indexes:
            line = source_lines[line_idx]
            haystack = line.lower() if ignore_case else line
            if normalized_pattern in haystack:
                matches.append(MatchLine(line_number=line_idx + 1, text=line, file=file_path))
                # H6: cap to config.max_count, matching cpu_backend's per-file cap
                # semantics -- never return every match once the cap is reached.
                if max_count and len(matches) >= max_count:
                    break

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
        # audit D3: removed the outer `try/except Exception: raise e` wrapper — it only
        # obscured the original traceback without providing any fallback behaviour.
        import stringzilla as sz

        ignore_case = bool(config and config.ignore_case)
        if config and config.fixed_strings:
            indexed = self._search_with_index(file_path, pattern, config, ignore_case)
            if indexed is not None:
                return indexed

        content = self._load_searchable_text(
            file_path,
            treat_binary_as_text=self._should_search_binary_as_text(config),
        )
        if content is None:
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="StringZillaBackend",
                routing_reason="stringzilla_fixed_strings_skipped_binary",
                routing_distributed=False,
                routing_worker_count=1,
            )

        sz_str = sz.Str(content)

        # Since StringZilla 4.x, we can split by lines extremely fast
        lines = sz_str.splitlines()
        # Unlike Python's str.splitlines(), StringZilla's Str.splitlines() emits an
        # extra trailing empty entry when the source text ends with a line
        # terminator (e.g. "a\n" -> ["a", ""] instead of ["a"]). Uncorrected, that
        # phantom empty "line" spuriously matches under invert_match (it never
        # contains the pattern) and shifts every subsequent line number. Trim it so
        # line numbering and invert_match semantics match cpu_backend/rg.
        if lines and str(lines[-1]) == "" and content.endswith(("\n", "\r")):
            lines = lines[:-1]
        matches = []
        invert_match = bool(config and config.invert_match)
        max_count = config.max_count if config else None

        # Evaluate using stringzilla's native find
        for i, line in enumerate(lines):
            haystack = str(line).lower() if ignore_case else line
            needle = pattern.lower() if ignore_case else pattern
            found = haystack.find(needle) != -1
            # H5: honor invert_match -- a matching line under invert_match is one
            # where the pattern is ABSENT, the complement of the normal result.
            matched = (not found) if invert_match else found
            if matched:
                matches.append(MatchLine(line_number=i + 1, text=str(line), file=file_path))
                # H6: cap to config.max_count, matching cpu_backend's per-file cap
                # semantics -- never return every match once the cap is reached.
                if max_count and len(matches) >= max_count:
                    break

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches),
            routing_backend="StringZillaBackend",
            routing_reason="stringzilla_fixed_strings",
            routing_distributed=False,
            routing_worker_count=1,
        )
