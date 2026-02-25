from enum import Enum, auto

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
        if any(kw in query_lower for kw in nlp_keywords):
            return QueryAnalysisResult(QueryType.NLP)
        return QueryAnalysisResult(QueryType.FAST)
