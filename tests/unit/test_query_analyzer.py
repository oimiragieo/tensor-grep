from tensor_grep.core.query_analyzer import QueryAnalyzer, QueryType


class TestQueryAnalyzer:
    def test_simple_string_is_fast_path(self):
        qa = QueryAnalyzer()
        assert qa.analyze("ERROR").query_type == QueryType.FAST

    def test_regex_is_fast_path(self):
        qa = QueryAnalyzer()
        assert qa.analyze(r"ERROR.*timeout").query_type == QueryType.FAST

    def test_natural_language_is_nlp_path(self, monkeypatch):
        # C11: the keyword auto-route is now opt-in via TG_NLP_KEYWORD_AUTOROUTE
        # (default OFF) -- a literal pattern containing "classify"/"detect" must
        # stay on the FAST path unless the user explicitly opts in.
        monkeypatch.setenv("TG_NLP_KEYWORD_AUTOROUTE", "1")
        qa = QueryAnalyzer()
        assert qa.analyze("classify ssh brute force attempts").query_type == QueryType.NLP

    def test_keyword_triggers_nlp(self, monkeypatch):
        # C11: same opt-in as above -- this now covers the opt-IN route.
        monkeypatch.setenv("TG_NLP_KEYWORD_AUTOROUTE", "1")
        qa = QueryAnalyzer()
        for kw in ["classify", "detect", "extract entities", "anomaly"]:
            assert qa.analyze(kw).query_type == QueryType.NLP
