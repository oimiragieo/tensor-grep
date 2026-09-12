from enum import Enum, auto

from tensor_grep.cli.runtime_paths import env_flag_enabled

# Opt-in gate for the speculative NLP keyword scan below (C11). Default OFF:
# the keywords substring-match common code identifiers in the user's LITERAL
# search pattern, which silently rerouted literal searches to the NLP backend
# (and, with cybert absent and --gpu-device-ids set, hard-failed them).
_NLP_KEYWORD_AUTOROUTE_ENV = "TG_NLP_KEYWORD_AUTOROUTE"


class QueryType(Enum):
    FAST = auto()
    NLP = auto()
    AST = auto()


class QueryAnalysisResult:
    def __init__(self, query_type: QueryType):
        self.query_type = query_type


class QueryAnalyzer:
    def analyze(self, query: str) -> QueryAnalysisResult:
        # Note: In practice, --ast flag will forcefully override this analyzer,
        # but for future NLP-to-AST heuristics we leave this here.
        nlp_keywords = ["classify", "detect", "extract entities", "anomaly"]
        query_lower = query.lower()
        if env_flag_enabled(_NLP_KEYWORD_AUTOROUTE_ENV) and any(
            kw in query_lower for kw in nlp_keywords
        ):
            return QueryAnalysisResult(QueryType.NLP)
        return QueryAnalysisResult(QueryType.FAST)
