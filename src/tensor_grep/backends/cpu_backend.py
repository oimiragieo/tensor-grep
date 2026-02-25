import re
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

        matches = []
        flags = 0

        if config.ignore_case or (config.smart_case and pattern.islower()):
            flags |= re.IGNORECASE

        try:
            if config.fixed_strings:
                regex = re.compile(re.escape(pattern), flags)
            elif config.line_regexp:
                regex = re.compile(f"^{pattern}$", flags)
            elif config.word_regexp:
                regex = re.compile(f"\\b{pattern}\\b", flags)
            else:
                regex = re.compile(pattern, flags)
        except re.error:
            regex = re.compile(re.escape(pattern), flags)

        total_matches_count = 0
        before_lines = getattr(config, "before_context", 0) or 0
        after_lines = getattr(config, "after_context", 0) or 0
        if getattr(config, "context", None):
            before_lines = config.context
            after_lines = config.context

        try:
            from collections import deque

            before_queue = deque(maxlen=before_lines)
            context_after_remaining = 0

            with open(path, encoding="utf-8", errors="replace") as f:
                for line_idx, line in enumerate(f, 1):
                    line_text = line.rstrip("\n")
                    matched = bool(regex.search(line_text))

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
