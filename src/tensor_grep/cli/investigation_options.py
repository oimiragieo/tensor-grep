"""Shared option validation and final find evidence enrichment."""

from __future__ import annotations

import sys
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
    if result.total_matches == 0:
        result.dependency_grounding = None
        return result
    tokens_used = sum(max(1, len(match.text) // 4) for match in result.matches)
    result.dependency_grounding = dependency_grounding(
        path,
        grounding,
        deadline_monotonic=deadline_monotonic,
        max_tokens=max(0, max_tokens - tokens_used) if max_tokens > 0 else None,
    )
    return result


def parse_gpu_device_ids_or_exit(
    raw: str | None,
    *,
    parse_fn: object,
    json_mode: bool,
    exit_error_fn: object,
) -> list[int] | None:
    import typer

    try:
        return parse_fn(raw)  # type: ignore[operator, no-any-return]
    except typer.BadParameter as exc:
        if json_mode and callable(exit_error_fn):
            exit_error_fn("configuration_error", str(exc), json_mode=True)
        raise


def _set_semantic_rank_fallback_reason(all_results: SearchResult) -> None:
    """Probe dense-leg availability and set ``rank_fallback_reason``."""
    from tensor_grep.core.retrieval_dense import dense_available

    available, unavailable_reason = dense_available()
    if not available:
        all_results.rank_fallback_reason = unavailable_reason
        sys.stderr.write(f"tg: {unavailable_reason}\n")


def _note_late_rerank_degraded(all_results: SearchResult, reason: str) -> None:
    """Append (or set) ``rank_fallback_reason`` for a RECOVERABLE late-rerank degrade."""
    if all_results.rank_fallback_reason:
        all_results.rank_fallback_reason = f"{all_results.rank_fallback_reason}; {reason}"
    else:
        all_results.rank_fallback_reason = reason
    sys.stderr.write(f"tg: {reason}\n")


def apply_semantic_zero_match_bm25_fallback(
    all_results: SearchResult,
    pattern: str,
    candidate_files_ordered: list[str],
    *,
    max_count: int | None,
    corpus_chunk_cap: int,
    set_fallback_reason_fn: object,
) -> None:
    """Populate BM25 lexical hits when ``tg search --semantic`` found 0 literal regex matches."""
    from tensor_grep.core.retrieval_dense import dense_available

    available, _reason = dense_available()
    if available:
        return
    if callable(set_fallback_reason_fn):
        set_fallback_reason_fn(all_results)
    if not candidate_files_ordered:
        return
    from tensor_grep.core.reranker import select_deduplicated_find_matches
    from tensor_grep.core.result import MatchLine
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
    from tensor_grep.core.retrieval_lexical import split_terms

    chunks: list[Chunk] = []
    for file_path in candidate_files_ordered:
        try:
            file_chunks = chunk_file(file_path)
        except RuntimeError:
            continue
        chunks.extend(file_chunks)
        if len(chunks) > corpus_chunk_cap:
            break
    if not chunks:
        return
    query_terms = set(split_terms(pattern))
    if not query_terms:
        return
    top_k = max_count if (max_count is not None and max_count > 0) else 20
    ranked = Bm25Index(chunks).query(pattern, top_k=top_k)
    if not ranked:
        return

    def _rep_line(chunk: Chunk, terms: set[str]) -> tuple[int, str]:
        lines = chunk.text.splitlines() or [""]
        for offset, line in enumerate(lines):
            if terms & set(split_terms(line)):
                return chunk.start_line + offset, line
        return chunk.start_line, lines[0]

    matches, _ = select_deduplicated_find_matches(
        chunks,
        [idx for idx, _ in ranked],
        query_terms,
        limit=top_k,
        why_ranked=False,
        find_representative_line=_rep_line,
    )
    if not matches:
        return
    all_results.matches = [
        MatchLine(m.line_number, m.text, m.file, range=m.range, why_ranked=m.why_ranked)
        for m in matches
    ]
    all_results.total_matches = len(all_results.matches)
    all_results.matched_file_paths = sorted({m.file for m in all_results.matches})
    all_results.total_files = len(all_results.matched_file_paths)
    all_results.match_counts_by_file = {}
    for match in all_results.matches:
        all_results.match_counts_by_file[match.file] = (
            all_results.match_counts_by_file.get(match.file, 0) + 1
        )
    all_results.routing_reason = "semantic_bm25_fallback"
