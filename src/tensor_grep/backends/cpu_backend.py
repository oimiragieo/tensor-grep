import hashlib
import json
import logging
import os
import re
import warnings
from pathlib import Path
from typing import ClassVar

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

logger = logging.getLogger(__name__)


class CPUBackend(ComputeBackend):
    _shared_literal_index_cache: ClassVar[
        dict[tuple[str, bool], tuple[tuple[int, int], list[str], dict[str, list[int]]]]
    ] = {}

    @classmethod
    def _clear_shared_caches(cls) -> None:
        cls._shared_literal_index_cache.clear()

    @staticmethod
    def _build_file_signature(file_path: str) -> tuple[int, int]:
        stat_result = Path(file_path).stat()
        return stat_result.st_mtime_ns, stat_result.st_size

    @staticmethod
    def _is_persistent_prefilter_enabled() -> bool:
        return os.environ.get("TENSOR_GREP_CPU_REGEX_INDEX", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }

    @staticmethod
    def _get_prefilter_cache_dir() -> Path:
        override = os.environ.get("TENSOR_GREP_CPU_REGEX_INDEX_DIR")
        if override:
            return Path(override).expanduser().resolve()
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                return Path(local_appdata) / "tensor-grep" / "cpu-regex-index"
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home) / "tensor-grep" / "cpu-regex-index"
        return Path.home() / ".cache" / "tensor-grep" / "cpu-regex-index"

    @classmethod
    def _get_prefilter_cache_path(cls, file_path: str, ignore_case: bool) -> Path:
        key = f"{Path(file_path).resolve()}::{int(ignore_case)}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        return cls._get_prefilter_cache_dir() / f"{digest}.json"

    @staticmethod
    def _build_line_trigram_index(lines: list[str]) -> dict[str, list[int]]:
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
        start = prev = line_indexes[0]
        for line_idx in line_indexes[1:]:
            if line_idx == prev + 1:
                prev = line_idx
                continue
            ranges.append([start, prev])
            start = prev = line_idx
        ranges.append([start, prev])
        return ranges

    @staticmethod
    def _decompress_line_indexes(encoded_ranges: list[list[int]]) -> list[int] | None:
        line_indexes: list[int] = []
        for item in encoded_ranges:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], int)
                or not isinstance(item[1], int)
            ):
                return None
            start, end = item
            if end < start:
                return None
            line_indexes.extend(range(start, end + 1))
        return line_indexes

    @staticmethod
    def _extract_required_literal(pattern: str) -> str | None:
        if any(token in pattern for token in ("|", "(", ")", "[", "]", "{", "}", "?", "+", "\\")):
            return None

        literals: list[str] = []
        current: list[str] = []
        for ch in pattern:
            if ch in {".", "*", "^", "$"}:
                if current:
                    literals.append("".join(current))
                    current = []
                continue
            current.append(ch)

        if current:
            literals.append("".join(current))

        literal = max(literals, key=len, default="")
        return literal if len(literal) >= 3 else None

    def _load_literal_index(
        self, file_path: str, ignore_case: bool
    ) -> tuple[list[str], dict[str, list[int]]] | None:
        cache_key = (file_path, ignore_case)
        cache_signature = self._build_file_signature(file_path)
        cached = self._shared_literal_index_cache.get(cache_key)
        if cached and cached[0] == cache_signature:
            return cached[1], cached[2]
        if not self._is_persistent_prefilter_enabled():
            return None
        cache_path = self._get_prefilter_cache_path(file_path, ignore_case)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("file_signature") != list(cache_signature):
            return None
        raw_index = payload.get("trigram_index")
        raw_compact_index = payload.get("trigram_index_ranges")
        if not isinstance(raw_index, dict):
            if not isinstance(raw_compact_index, dict):
                return None
            raw_index = raw_compact_index
        lines = Path(file_path).read_text(encoding="utf-8", errors="replace").splitlines()
        trigram_index: dict[str, list[int]] = {}
        for trigram, values in raw_index.items():
            if not isinstance(trigram, str) or not isinstance(values, list):
                return None
            decoded = self._decompress_line_indexes(values)
            if decoded is None:
                try:
                    decoded = [int(v) for v in values]
                except (TypeError, ValueError):
                    return None
            trigram_index[trigram] = decoded
        self._shared_literal_index_cache[cache_key] = (cache_signature, lines, trigram_index)
        return lines, trigram_index
        return None

    def _store_literal_index(
        self,
        file_path: str,
        ignore_case: bool,
        lines: list[str],
        trigram_index: dict[str, list[int]],
    ) -> None:
        cache_signature = self._build_file_signature(file_path)
        self._shared_literal_index_cache[(file_path, ignore_case)] = (
            cache_signature,
            lines,
            trigram_index,
        )
        if not self._is_persistent_prefilter_enabled():
            return
        cache_path = self._get_prefilter_cache_path(file_path, ignore_case)
        payload = {
            "file_signature": list(cache_signature),
            "trigram_index_ranges": {
                trigram: self._compress_line_indexes(line_indexes)
                for trigram, line_indexes in trigram_index.items()
            },
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return

    @classmethod
    def _candidate_line_indexes(
        cls, trigram_index: dict[str, list[int]], literal: str
    ) -> list[int]:
        trigrams = [literal[i : i + 3] for i in range(len(literal) - 2)]
        candidate_sets = []
        for trigram in trigrams:
            line_numbers = trigram_index.get(trigram)
            if not line_numbers:
                return []
            candidate_sets.append(set(line_numbers))
        return sorted(set.intersection(*candidate_sets)) if candidate_sets else []

    @staticmethod
    def _compile_regexes(
        pattern: str, flags: int, config: SearchConfig
    ) -> tuple[re.Pattern[str], re.Pattern[bytes]]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                if config.fixed_strings:
                    escaped = re.escape(pattern)
                    return re.compile(escaped, flags), re.compile(escaped.encode("utf-8"), flags)
                if config.line_regexp:
                    wrapped = f"^{pattern}$"
                    return re.compile(wrapped, flags), re.compile(wrapped.encode(), flags)
                if config.word_regexp:
                    wrapped = f"\\b{pattern}\\b"
                    return re.compile(wrapped, flags), re.compile(wrapped.encode(), flags)
                return re.compile(pattern, flags), re.compile(pattern.encode("utf-8"), flags)
            except re.error:
                escaped = re.escape(pattern)
                return re.compile(escaped, flags), re.compile(escaped.encode("utf-8"), flags)

    def is_available(self) -> bool:
        return True

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        routing_reason = "cpu_python_regex"
        if config is None:
            from tensor_grep.core.config import SearchConfig

            config = SearchConfig()

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return SearchResult(
                matches=[],
                total_files=0,
                total_matches=0,
                routing_backend="CPUBackend",
                routing_reason="cpu_missing_file",
                routing_distributed=False,
                routing_worker_count=1,
            )

        if config.ltl:
            result = self._search_ltl(path, pattern, config)
            result.routing_backend = "CPUBackend"
            result.routing_reason = "cpu_ltl_python"
            result.routing_distributed = False
            result.routing_worker_count = 1
            return result

        # ReDoS Protection:
        # Instead of using Python's standard `re` module (which uses backtracking and is vulnerable
        # to ReDoS attacks), we route complex pure-python CPU requests to the native Rust `regex` crate.
        # Rust's regex engine uses Finite Automata which mathematically guarantees O(m) linear time execution.
        try:
            from tensor_grep.rust_core import RustBackend

            rust_backend = RustBackend()
            try:
                rust_results = rust_backend.search(
                    pattern=pattern,
                    path=file_path,
                    ignore_case=config.ignore_case or (config.smart_case and pattern.islower()),
                    fixed_strings=config.fixed_strings,
                    invert_match=config.invert_match,
                )
            except TypeError:
                rust_results = rust_backend.search(
                    pattern=pattern,
                    path=file_path,
                    ignore_case=config.ignore_case or (config.smart_case and pattern.islower()),
                    fixed_strings=config.fixed_strings,
                )

            # If Rust returns no matches on a file that is not valid UTF-8, fall back to Python
            # decoding path (latin-1/replace) for compatibility.
            if not rust_results:
                try:
                    Path(file_path).read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError(
                        "Rust backend UTF-8 decode mismatch, using Python fallback"
                    ) from exc

            # Since the Rust backend currently just returns `(line_num, string)`, we need to adapt it
            # context lines (like -C 2) aren't fully implemented in the Rust bridging yet, but we will
            # return the matched lines securely.
            if (
                getattr(config, "context", False)
                or getattr(config, "before_context", False)
                or getattr(config, "after_context", False)
            ):
                raise NotImplementedError(
                    "Rust backend does not support context lines yet, fallback to python"
                )

            matches = [MatchLine(line_number=r[0], text=r[1], file=file_path) for r in rust_results]

            return SearchResult(
                matches=matches,
                total_files=1 if matches else 0,
                total_matches=len(matches),
                routing_backend="CPUBackend",
                routing_reason="cpu_rust_regex",
                routing_distributed=False,
                routing_worker_count=1,
            )

        except Exception as exc:
            # Fallback to python `re` only if `tensor_grep.rust_core` is entirely broken or not supporting the feature
            logger.warning(
                "Rust backend failed for %s, falling back to Python regex: %s", file_path, exc
            )

        matches = []
        flags = 0

        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        regex_str, regex = self._compile_regexes(pattern=pattern, flags=flags, config=config)
        prefilter_literal = None
        routing_reason = "cpu_python_regex"
        ignore_case = bool(config.ignore_case or (config.smart_case and pattern.islower()))
        source_lines: list[str] | None = None
        candidate_line_indexes: set[int] | None = None
        if not (
            config.fixed_strings
            or config.invert_match
            or config.context
            or config.before_context
            or config.after_context
            or config.line_regexp
            or config.word_regexp
            or config.ltl
        ):
            prefilter_literal = self._extract_required_literal(pattern)
            if prefilter_literal:
                cached_index = self._load_literal_index(file_path, ignore_case)
                if cached_index is None:
                    source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    normalized_lines = (
                        [line.lower() for line in source_lines] if ignore_case else source_lines
                    )
                    trigram_index = self._build_line_trigram_index(normalized_lines)
                    self._store_literal_index(file_path, ignore_case, source_lines, trigram_index)
                    routing_reason = "cpu_python_regex_prefilter"
                else:
                    source_lines, trigram_index = cached_index
                    routing_reason = "cpu_python_regex_prefilter_cache"
                literal = prefilter_literal.lower() if ignore_case else prefilter_literal
                candidate_line_indexes = set(self._candidate_line_indexes(trigram_index, literal))

        total_matches_count = 0
        before_lines = getattr(config, "before_context", 0) or 0
        after_lines = getattr(config, "after_context", 0) or 0
        if getattr(config, "context", None):
            before_lines = config.context
            after_lines = config.context

        try:
            from collections import deque

            before_queue: deque[tuple[int, str]] = deque(maxlen=before_lines)
            context_after_remaining = 0
            if source_lines is not None:
                line_iter = (
                    (idx + 1, f"{line}\n".encode()) for idx, line in enumerate(source_lines)
                )
                for line_idx, line_bytes in line_iter:
                    if (
                        candidate_line_indexes is not None
                        and (line_idx - 1) not in candidate_line_indexes
                    ):
                        continue
                    # Try using python regex to decode byte string, else try the decoded string
                    matched = False
                    try:
                        matched = bool(regex.search(line_bytes))
                    except Exception:
                        pass

                    if not matched:
                        try:
                            line_text = line_bytes.decode("utf-8").rstrip("\n\r")
                            matched = bool(regex_str.search(line_text))
                        except Exception:
                            try:
                                line_text = line_bytes.decode("latin-1").rstrip("\n\r")
                                matched = bool(regex_str.search(line_text))
                            except Exception:
                                pass

                    if config.invert_match:
                        matched = not matched

                    if matched or before_lines > 0 or context_after_remaining > 0:
                        # Decode lazily only what we need to return
                        try:
                            line = line_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            try:
                                line = line_bytes.decode("latin-1")
                            except Exception:
                                line = line_bytes.decode("utf-8", errors="replace")
                        line_text = line.rstrip("\n\r")

                        # Apply python regex search for decoded text to be safe
                        matched = bool(regex_str.search(line_text))

                        if config.invert_match:
                            matched = not matched

                    if matched:
                        while before_queue:
                            b_idx, b_text = before_queue.popleft()
                            matches.append(
                                MatchLine(line_number=b_idx, text=b_text, file=file_path)
                            )

                        matches.append(
                            MatchLine(line_number=line_idx, text=line_text, file=file_path)
                        )
                        total_matches_count += 1
                        context_after_remaining = after_lines

                        if config.max_count and total_matches_count >= config.max_count:
                            break
                    elif context_after_remaining > 0:
                        matches.append(
                            MatchLine(line_number=line_idx, text=line_text, file=file_path)
                        )
                        context_after_remaining -= 1
                    else:
                        if before_lines > 0:
                            before_queue.append((line_idx, line_text))
            else:
                with open(path, "rb") as f:
                    for line_idx, line_bytes in enumerate(f, 1):
                        if (
                            candidate_line_indexes is not None
                            and (line_idx - 1) not in candidate_line_indexes
                        ):
                            continue
                        # Try using python regex to decode byte string, else try the decoded string
                        matched = False
                        try:
                            matched = bool(regex.search(line_bytes))
                        except Exception:
                            pass

                        if not matched:
                            try:
                                line_text = line_bytes.decode("utf-8").rstrip("\n\r")
                                matched = bool(regex_str.search(line_text))
                            except Exception:
                                try:
                                    line_text = line_bytes.decode("latin-1").rstrip("\n\r")
                                    matched = bool(regex_str.search(line_text))
                                except Exception:
                                    pass

                        if config.invert_match:
                            matched = not matched

                        if matched or before_lines > 0 or context_after_remaining > 0:
                            # Decode lazily only what we need to return
                            try:
                                line = line_bytes.decode("utf-8")
                            except UnicodeDecodeError:
                                try:
                                    line = line_bytes.decode("latin-1")
                                except Exception:
                                    line = line_bytes.decode("utf-8", errors="replace")
                            line_text = line.rstrip("\n\r")

                            # Apply python regex search for decoded text to be safe
                            matched = bool(regex_str.search(line_text))

                            if config.invert_match:
                                matched = not matched

                        if matched:
                            while before_queue:
                                b_idx, b_text = before_queue.popleft()
                                matches.append(
                                    MatchLine(line_number=b_idx, text=b_text, file=file_path)
                                )

                            matches.append(
                                MatchLine(line_number=line_idx, text=line_text, file=file_path)
                            )
                            total_matches_count += 1
                            context_after_remaining = after_lines

                            if config.max_count and total_matches_count >= config.max_count:
                                break
                        elif context_after_remaining > 0:
                            matches.append(
                                MatchLine(line_number=line_idx, text=line_text, file=file_path)
                            )
                            context_after_remaining -= 1
                        else:
                            if before_lines > 0:
                                before_queue.append((line_idx, line_text))
        except Exception as exc:
            raise RuntimeError(f"CPU backend search failed for {file_path}: {exc}") from exc

        return SearchResult(
            matches=matches,
            total_files=1 if total_matches_count > 0 else 0,
            total_matches=total_matches_count,
            routing_backend="CPUBackend",
            routing_reason=routing_reason,
            routing_distributed=False,
            routing_worker_count=1,
        )

    @staticmethod
    def _decode_line(line_bytes: bytes) -> str:
        try:
            return line_bytes.decode("utf-8").rstrip("\n\r")
        except UnicodeDecodeError:
            try:
                return line_bytes.decode("latin-1").rstrip("\n\r")
            except Exception:
                return line_bytes.decode("utf-8", errors="replace").rstrip("\n\r")

    @staticmethod
    def _compile_ltl(pattern: str, flags: int) -> tuple[re.Pattern[str], re.Pattern[str]]:
        # Supported grammar (minimal v1): A -> eventually B
        ltl_match = re.match(r"^\s*(.+?)\s*->\s*eventually\s+(.+?)\s*$", pattern, re.IGNORECASE)
        if ltl_match is None:
            raise ValueError("Unsupported LTL query. Use: 'A -> eventually B'")
        left_expr, right_expr = ltl_match.group(1), ltl_match.group(2)
        return re.compile(left_expr, flags), re.compile(right_expr, flags)

    def _search_ltl(self, path: Path, pattern: str, config: SearchConfig) -> SearchResult:
        flags = 0
        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        left_regex, right_regex = self._compile_ltl(pattern, flags)
        lines: list[tuple[int, str]] = []
        with open(path, "rb") as file_obj:
            for line_idx, line_bytes in enumerate(file_obj, 1):
                lines.append((line_idx, self._decode_line(line_bytes)))

        matches: list[MatchLine] = []
        sequence_count = 0

        for idx, (left_line_no, left_text) in enumerate(lines):
            if left_regex.search(left_text) is None:
                continue
            right_match_idx = None
            for probe in range(idx + 1, len(lines)):
                if right_regex.search(lines[probe][1]) is not None:
                    right_match_idx = probe
                    break
            if right_match_idx is None:
                continue

            right_line_no, right_text = lines[right_match_idx]
            matches.append(MatchLine(line_number=left_line_no, text=left_text, file=str(path)))
            matches.append(MatchLine(line_number=right_line_no, text=right_text, file=str(path)))
            sequence_count += 1

            if config.max_count and sequence_count >= config.max_count:
                break

        return SearchResult(
            matches=matches,
            total_files=1 if sequence_count > 0 else 0,
            total_matches=sequence_count,
            routing_backend="CPUBackend",
            routing_reason="cpu_ltl_python",
            routing_distributed=False,
            routing_worker_count=1,
        )
