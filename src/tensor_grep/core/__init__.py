from importlib import import_module
from typing import Any

_EXPORTS = {
    "ConfigurationError": "tensor_grep.core.pipeline",
    "MatchLine": "tensor_grep.core.result",
    "Pipeline": "tensor_grep.core.pipeline",
    "QueryAnalysisResult": "tensor_grep.core.query_analyzer",
    "QueryAnalyzer": "tensor_grep.core.query_analyzer",
    "QueryType": "tensor_grep.core.query_analyzer",
    "SearchConfig": "tensor_grep.core.config",
    "SearchResult": "tensor_grep.core.result",
    "nvtx_range": "tensor_grep.core.observability",
}

__all__ = [
    "ConfigurationError",
    "MatchLine",
    "Pipeline",
    "QueryAnalysisResult",
    "QueryAnalyzer",
    "QueryType",
    "SearchConfig",
    "SearchResult",
    "nvtx_range",
]


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(_EXPORTS[name])
    value = getattr(module, name)
    globals()[name] = value
    return value
