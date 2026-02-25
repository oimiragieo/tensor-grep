import re
from typing import Optional
from pathlib import Path
from collections import deque
from tensor_grep.backends.base import ComputeBackend
from tensor_grep.core.result import SearchResult, MatchLine
from tensor_grep.core.config import SearchConfig

class CPUBackend(ComputeBackend):
    def is_available(self) -> bool:
        return True

    def search(self, file_path: str, pattern: str, config: Optional[SearchConfig] = None) -> SearchResult:
        if config is None:
            config = SearchConfig()
            
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return SearchResult()

        matches = []
        flags = 0
        
        if config.ignore_case or config.smart_case and pattern.islower():
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

        before_lines = getattr(config, "before_context", 0) or 0
        after_lines = getattr(config, "after_context", 0) or 0
        if getattr(config, "context", None):
            before_lines = config.context
            after_lines = config.context
            
        context_after_remaining = 0
        before_queue = deque(maxlen=before_lines)

        def process_lines(f):
            nonlocal context_after_remaining
            for line_idx, line in enumerate(f):
                matched = bool(regex.search(line))
                if config.invert_match:
                    matched = not matched
                    
                if matched:
                    # Flush before_queue
                    while before_queue:
                        b_idx, b_line = before_queue.popleft()
                        matches.append(MatchLine(
                            line_number=b_idx + 1,
                            text=b_line.rstrip("\n"),
                            file=file_path
                        ))
                        
                    matches.append(MatchLine(
                        line_number=line_idx + 1,
                        text=line.rstrip("\n"),
                        file=file_path
                    ))
                    context_after_remaining = after_lines
                    if config.max_count and len([m for m in matches if bool(regex.search(m.text)) != config.invert_match]) >= config.max_count:
                        break
                elif context_after_remaining > 0:
                    matches.append(MatchLine(
                        line_number=line_idx + 1,
                        text=line.rstrip("\n"),
                        file=file_path
                    ))
                    context_after_remaining -= 1
                else:
                    if before_lines > 0:
                        before_queue.append((line_idx, line))

        try:
            with open(path, "r", encoding="utf-8") as f:
                process_lines(f)
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as f:
                process_lines(f)

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches)
        )
