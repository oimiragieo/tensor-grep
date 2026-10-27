import csv
import io

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult


def _safe_cell(value: str) -> str:
    formula_markers = ("=", "+", "-", "@", "\uff1d", "\uff0b", "\uff0d", "\uff20")
    return (
        "'" + value
        if value.startswith(("\t", "\r", "\n")) or value.lstrip()[:1] in formula_markers
        else value
    )


class CsvFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["file", "line_number", "text"])
        for match in result.matches:
            writer.writerow([_safe_cell(match.file), match.line_number, _safe_cell(match.text)])
        return output.getvalue().strip()
