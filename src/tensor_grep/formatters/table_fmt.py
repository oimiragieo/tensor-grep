from tensor_grep.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult

class TableFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        lines = ["File\tLine\tMatch"]
        for match in result.matches:
            lines.append(f"{match.file}\t{match.line_number}\t{match.text}")
        return "\n".join(lines)
