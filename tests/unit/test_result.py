from tensor_grep.core.result import MatchLine, SearchResult


class TestSearchResult:
    def test_should_create_result_with_matches(self):
        match = MatchLine(line_number=2, text="ERROR Connection timeout", file="test.log")
        result = SearchResult(matches=[match], total_files=1, total_matches=1)
        assert result.total_matches == 1
        assert result.matches[0].line_number == 2

    def test_should_report_empty_when_no_matches(self):
        result = SearchResult(matches=[], total_files=1, total_matches=0)
        assert result.is_empty is True
