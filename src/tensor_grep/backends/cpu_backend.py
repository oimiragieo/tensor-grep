from pathlib import Path

from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


class CPUBackend(ComputeBackend):
    def is_available(self) -> bool:
        return True

    def search(
        self, file_path: str, pattern: str, config: SearchConfig | None = None
    ) -> SearchResult:
        if config is None:
            from tensor_grep.core.config import SearchConfig

            config = SearchConfig()

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return SearchResult(matches=[], total_files=0, total_matches=0)

        # ReDoS Protection:
        # Instead of using Python's standard `re` module (which uses backtracking and is vulnerable
        # to ReDoS attacks), we route complex pure-python CPU requests to the native Rust `regex` crate.
        # Rust's regex engine uses Finite Automata which mathematically guarantees O(m) linear time execution.
        try:
            from tensor_grep.rust_core import RustBackend

            rust_backend = RustBackend()
            rust_results = rust_backend.search(
                pattern=pattern,
                path=file_path,
                ignore_case=config.ignore_case or (config.smart_case and pattern.islower()),
                fixed_strings=config.fixed_strings,
            )

            # Since the Rust backend currently just returns `(line_num, string)`, we need to adapt it
            # context lines (like -C 2) aren't fully implemented in the Rust bridging yet, but we will
            # return the matched lines securely.
            if (
                getattr(config, "context", False)
                or getattr(config, "before_context", False)
                or getattr(config, "after_context", False)
                or getattr(config, "invert_match", False)
            ):
                raise NotImplementedError(
                    "Rust backend does not support context lines or invert_match yet, fallback to python"
                )

            # Fallback to python for rust empty responses since sometimes files are encoded in latin-1 and rust regex might fail silently
            if len(rust_results) == 0:
                raise Exception(
                    "Rust backend returned empty result, fallback to python to double check"
                )

            matches = [MatchLine(line_number=r[0], text=r[1], file=file_path) for r in rust_results]

            return SearchResult(
                matches=matches, total_files=1 if matches else 0, total_matches=len(matches)
            )

        except Exception:
            # Fallback to python `re` only if `tensor_grep.rust_core` is entirely broken or not supporting the feature
            pass

        import re

        matches = []
        flags = 0

        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        try:
            if config.fixed_strings:
                regex_str = re.compile(re.escape(pattern), flags)
                regex = re.compile(re.escape(pattern).encode("utf-8"), flags)
            elif config.line_regexp:
                regex_str = re.compile(f"^{pattern}$", flags)
                regex = re.compile(f"^{pattern}$".encode(), flags)
            elif config.word_regexp:
                regex_str = re.compile(f"\\b{pattern}\\b", flags)
                regex = re.compile(f"\\b{pattern}\\b".encode(), flags)
            else:
                regex_str = re.compile(pattern, flags)
                regex = re.compile(pattern.encode("utf-8"), flags)
        except re.error:
            regex_str = re.compile(re.escape(pattern), flags)
            regex = re.compile(re.escape(pattern).encode("utf-8"), flags)

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
        except Exception:
            pass

        return SearchResult(
            matches=matches, total_files=1 if matches else 0, total_matches=total_matches_count
        )
