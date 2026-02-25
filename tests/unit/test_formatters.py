import csv
import io
import json

from tensor_grep.core.result import MatchLine, SearchResult
from tensor_grep.formatters.csv_fmt import CsvFormatter
from tensor_grep.formatters.json_fmt import JsonFormatter
from tensor_grep.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.formatters.table_fmt import TableFormatter


class TestFormatters:
    def setup_method(self):
        match = MatchLine(line_number=2, text="ERROR test", file="test.log")
        self.result = SearchResult(matches=[match], total_files=1, total_matches=1)

    def test_should_format_lines(self):
        fmt = RipgrepFormatter()
        output = fmt.format(self.result)
        assert output == "test.log:2:ERROR test"

    def test_json_output_is_valid_json(self):
        fmt = JsonFormatter()
        output = fmt.format(self.result)
        parsed = json.loads(output)
        assert parsed["total_matches"] == 1
        assert parsed["matches"][0]["text"] == "ERROR test"

    def test_table_output_has_headers(self):
        fmt = TableFormatter()
        output = fmt.format(self.result)
        lines = output.splitlines()
        assert lines[0] == "File\tLine\tMatch"
        assert lines[1] == "test.log\t2\tERROR test"

    def test_csv_output_is_parseable(self):
        fmt = CsvFormatter()
        output = fmt.format(self.result)
        reader = csv.reader(io.StringIO(output))
        rows = list(reader)
        assert rows[0] == ["file", "line_number", "text"]
        assert rows[1] == ["test.log", "2", "ERROR test"]
