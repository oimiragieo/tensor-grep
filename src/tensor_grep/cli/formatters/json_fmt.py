import json

from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult


class JsonFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        data = {
            "total_matches": result.total_matches,
            "total_files": result.total_files,
            "matches": [
                {"file": m.file, "line_number": m.line_number, "text": m.text}
                for m in result.matches
            ],
        }
        return json.dumps(data)
