import logging
import re
import warnings
from pathlib import Path

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

logger = logging.getLogger(__name__)


class CPUBackend(ComputeBackend):
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
            return SearchResult(matches=[], total_files=0, total_matches=0)

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

            with open(path, "rb") as f:
                for line_idx, line_bytes in enumerate(f, 1):
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
                        # Flush before context
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
            total_files=1 if matches else 0,
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
        )
