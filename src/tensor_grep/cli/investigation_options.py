"""Shared option validation and final find evidence enrichment."""

from __future__ import annotations

import time

from tensor_grep.core.dependency_grounding import dependency_grounding, validate_grounding
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult


def validate_investigation_options(grounding: str, rerank: str) -> None:
    validate_grounding(grounding)
    if rerank not in {"off", "auto", "cross-encoder"}:
        raise ConfigurationError("rerank must be off, auto, or cross-encoder")


def finish_find(
    result: SearchResult,
    query: str,
    path: str,
    grounding: str,
    rerank: str,
    *,
    deadline_monotonic: float | None,
    max_tokens: int,
) -> SearchResult:
    from tensor_grep.core.cross_encoder import apply_cross_encoder

    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        if rerank != "off":
            raise ConfigurationError("rerank cannot run after the shared deadline")
    result = apply_cross_encoder(result, query, rerank, deadline_monotonic=deadline_monotonic)
    tokens_used = sum(max(1, len(match.text) // 4) for match in result.matches)
    result.dependency_grounding = dependency_grounding(
        path,
        grounding,
        deadline_monotonic=deadline_monotonic,
        max_tokens=max(0, max_tokens - tokens_used) if max_tokens > 0 else None,
    )
    return result
