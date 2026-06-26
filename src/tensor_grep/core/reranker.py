"""Re-rank an existing SearchResult by BM25 chunk relevance.

This is the lightweight post-processing seam for ``tg search --rank``: the normal backend produces
matches in grep order, and this re-orders them by the BM25 score of the chunk that contains each
match. Matches whose chunk does not score (or whose file is not in the corpus) sink to the end. The
sort is stable, so equal-score matches keep their original grep order.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict

from tensor_grep.core.result import SearchResult
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk, chunk_file


def rerank_by_bm25(
    result: SearchResult,
    query: str,
    file_paths: list[str],
    *,
    chunk_size: int = 30,
    overlap: int = 5,
    index: Bm25Index | None = None,
) -> SearchResult:
    """Return a copy of ``result`` with matches re-sorted by best BM25 chunk score (desc)."""
    if not result.matches:
        return dataclasses.replace(result, matches=list(result.matches))

    if index is None:
        chunks: list[Chunk] = []
        for path in file_paths:
            chunks.extend(chunk_file(path, chunk_size=chunk_size, overlap=overlap))
        index = Bm25Index(chunks)

    # Best score per chunk index for this query.
    chunk_scores: dict[int, float] = dict(index.query(query, top_k=max(1, len(index.chunks))))

    # file -> [(start_line, end_line, chunk_index)] for line-containment lookup.
    by_file: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for i, chunk in enumerate(index.chunks):
        by_file[chunk.file_path].append((chunk.start_line, chunk.end_line, i))

    def match_score(match) -> float:  # type: ignore[no-untyped-def]
        best = 0.0
        for start, end, i in by_file.get(match.file, ()):
            if start <= match.line_number <= end:
                best = max(best, chunk_scores.get(i, 0.0))
        return best

    # Stable sort by descending score (Python's sort is stable -> ties keep grep order).
    reranked = sorted(result.matches, key=match_score, reverse=True)
    return dataclasses.replace(result, matches=reranked)
