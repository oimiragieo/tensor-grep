from cudf_grep.formatters.base import OutputFormatter
from cudf_grep.core.result import SearchResult

class RipgrepFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        lines = []
        for match in result.matches:
            # Basic ripgrep-like output: file:line:text
            lines.append(f"{match.file}:{match.line_number}:{match.text}")
        return "\n".join(lines)
