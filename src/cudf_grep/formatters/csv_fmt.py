from cudf_grep.formatters.base import OutputFormatter
from cudf_grep.core.result import SearchResult
import csv
import io

class CsvFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["file", "line_number", "text"])
        for match in result.matches:
            writer.writerow([match.file, match.line_number, match.text])
        return output.getvalue().strip()
