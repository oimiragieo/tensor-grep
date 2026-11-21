from tensor_grep.cli.formatters.base import OutputFormatter
from tensor_grep.core.result import SearchResult


def _escape_tsv_cell(value: str) -> str:
    return value.replace("\t", r"\t").replace("\r", r"\r").replace("\n", r"\n")


class TableFormatter(OutputFormatter):
    def format(self, result: SearchResult) -> str:
        lines = ["File\tLine\tMatch"]
        for match in result.matches:
            lines.append(
                f"{_escape_tsv_cell(match.file)}\t{match.line_number}\t"
                f"{_escape_tsv_cell(match.text)}"
            )
        return "\n".join(lines)
