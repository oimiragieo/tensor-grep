import re
from pathlib import Path
from cudf_grep.backends.base import ComputeBackend
from cudf_grep.core.result import SearchResult, MatchLine

class CPUBackend(ComputeBackend):
    def is_available(self) -> bool:
        return True

    def search(self, file_path: str, pattern: str) -> SearchResult:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return SearchResult()

        matches = []
        try:
            regex = re.compile(pattern)
        except re.error:
            # If invalid regex, fall back to literal string search or just fail.
            # For simplicity in Phase 0, we'll try literal match.
            regex = re.compile(re.escape(pattern))

        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    if regex.search(line):
                        matches.append(MatchLine(
                            line_number=line_idx + 1,
                            text=line.rstrip("\n"),
                            file=file_path
                        ))
        except UnicodeDecodeError:
            # Fallback for non-utf8 files
            with open(path, "r", encoding="latin-1") as f:
                for line_idx, line in enumerate(f):
                    if regex.search(line):
                        matches.append(MatchLine(
                            line_number=line_idx + 1,
                            text=line.rstrip("\n"),
                            file=file_path
                        ))

        return SearchResult(
            matches=matches,
            total_files=1 if matches else 0,
            total_matches=len(matches)
        )
