from cudf_grep.formatters.ripgrep_fmt import RipgrepFormatter
from cudf_grep.core.result import SearchResult, MatchLine

class TestRipgrepFormatter:
    def test_should_format_lines(self):
        fmt = RipgrepFormatter()
        match = MatchLine(line_number=2, text="ERROR test", file="test.log")
        result = SearchResult(matches=[match], total_files=1, total_matches=1)
        output = fmt.format(result)
        assert output == "test.log:2:ERROR test"
