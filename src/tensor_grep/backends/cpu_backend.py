import re
from typing import Optional
from pathlib import Path
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

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    matched = bool(regex.search(line))
                    if config.invert_match:
                        matched = not matched
                        
                    if matched:
                        matches.append(MatchLine(
                            line_number=line_idx + 1,
                            text=line.rstrip("\n"),
                            file=file_path
                        ))
                        if config.max_count and len(matches) >= config.max_count:
                            break
        except UnicodeDecodeError:
            with open(path, "r", encoding="latin-1") as f:
                for line_idx, line in enumerate(f):
                    matched = bool(regex.search(line))
                    if config.invert_match:
                        matched = not matched
                        
                    if matched:
                        matches.append(MatchLine(
                            line_number=line_idx + 1,
                            text=line.rstrip("\n"),
                            file=file_path
                        ))
                        if config.max_count and len(matches) >= config.max_count:
                            break

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches)
        )
