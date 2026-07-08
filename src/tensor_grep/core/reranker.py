"""Re-rank an existing SearchResult by BM25 chunk relevance (and, for the hybrid variant, an
RRF fusion of BM25 + dense embedding [+ opt-in path/filename] relevance).

This is the lightweight post-processing seam for ``tg search --rank`` / ``tg search --semantic``:
the normal backend produces matches in grep order, and this re-orders them by the relevance score
of the chunk that contains each match. Matches whose chunk does not score (or whose file is not in
the corpus) sink to the end. The sort is stable, so equal-score matches keep their original grep
order.
"""

from __future__ import annotations

import dataclasses
import os
from collections import defaultdict

from tensor_grep.core.result import SearchResult
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
from tensor_grep.core.retrieval_dense import DenseIndex
from tensor_grep.core.retrieval_fusion import DEFAULT_K, reciprocal_rank_fusion
from tensor_grep.core.retrieval_lexical import split_terms

# PR-S2 (channelized RRF, sverklo steal-list #2): a third, opt-in fusion leg that ranks chunks by
# filename-token overlap with the query -- a precision signal (a query mentioning "invoice" should
# surface invoice_parser.py's chunks first). DEFAULT-OFF (gated by `_RRF_CHANNELS_ENV`) so this is
# a zero-risk additive change pending a golden-set default-flip in a separate PR. A symbol-name
# channel is DEFERRED to a later phase (it would need a def-scan source and couple this free-file
# module to repo_map).
_RRF_CHANNELS_ENV: str = "TG_RRF_CHANNELS"
PATH_CHANNEL_WEIGHT: float = 1.5


def _rrf_channels_enabled() -> bool:
    return os.environ.get(_RRF_CHANNELS_ENV) == "1"


def _path_channel_ranking(chunks: list[Chunk], query: str) -> list[int]:
    """Rank chunk indices by query-token overlap with their file's stem (basename minus
    extension), best-first. Reuses ``retrieval_lexical.split_terms`` -- the same tokenizer the
    BM25 leg uses -- so "parse_invoice" query terms match an "invoice_parser.py" filename despite
    the different word order. Only chunks with at least one overlapping token are included
    (mirrors ``Bm25Index.query`` excluding zero-score docs, so a non-matching filename contributes
    0 to this leg rather than an arbitrary tie-broken rank). Ties break by ascending chunk index
    for full determinism.
    """
    query_terms = set(split_terms(query))
    if not query_terms:
        return []

    scored: list[tuple[int, int]] = []
    for i, chunk in enumerate(chunks):
        stem = os.path.splitext(os.path.basename(chunk.file_path))[0]
        overlap = len(query_terms & set(split_terms(stem)))
        if overlap > 0:
            scored.append((i, overlap))

    ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
    return [chunk_index for chunk_index, _overlap in ranked]


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


def rerank_hybrid(
    result: SearchResult,
    query: str,
    file_paths: list[str],
    *,
    chunk_size: int = 30,
    overlap: int = 5,
    k: int = DEFAULT_K,
    bm25_index: Bm25Index | None = None,
    dense_index: DenseIndex | None = None,
) -> SearchResult:
    """Return a copy of ``result`` re-sorted by best RRF-fused (BM25 + dense) chunk score (desc).

    Mirrors :func:`rerank_by_bm25`: SAME matches as the input, only the order changes. The BM25
    leg always runs; the dense leg is optional -- the caller owns dense-leg availability and the
    fail-closed BM25-only degrade (see ``core/retrieval_dense.py``), so ``dense_index=None`` here
    simply means "fuse with the BM25 leg alone" (still routed through RRF).

    A third, opt-in PATH channel (see :func:`_path_channel_ranking`) ranks chunks by
    filename-token overlap with the query at ``PATH_CHANNEL_WEIGHT`` (1.5x) vs the BM25/dense
    legs' implicit 1.0x. It is gated behind the ``TG_RRF_CHANNELS=1`` environment variable and is
    DEFAULT-OFF: with the flag unset (the default), fusion runs BM25 [+ dense] with
    ``weights=None`` exactly as before -- a byte-identical no-op (see
    :func:`~tensor_grep.core.retrieval_fusion.reciprocal_rank_fusion`) -- so this is a zero-risk
    additive change pending a golden-set default-flip in a separate PR.

    NOTE (F15): a BM25-only RRF degrade is NOT byte-identical to :func:`rerank_by_bm25` on a BM25
    SCORE TIE -- RRF breaks ties by ascending chunk index, whereas ``rerank_by_bm25``'s stable sort
    preserves grep order. Both are valid orderings; when ``--semantic`` is requested the fused path
    is authoritative, so this is a benign ordering divergence, not a correctness gap.
    """
    if not result.matches:
        return dataclasses.replace(result, matches=list(result.matches))

    if bm25_index is None:
        chunks: list[Chunk] = []
        for path in file_paths:
            chunks.extend(chunk_file(path, chunk_size=chunk_size, overlap=overlap))
        bm25_index = Bm25Index(chunks)
    chunks = bm25_index.chunks

    total = max(1, len(chunks))
    bm25_ranking = [chunk_idx for chunk_idx, _ in bm25_index.query(query, top_k=total)]
    rankings: list[list[int]] = [bm25_ranking]
    if dense_index is not None:
        dense_ranking = [chunk_idx for chunk_idx, _ in dense_index.query(query, top_k=total)]
        rankings.append(dense_ranking)

    weights: list[float] | None = None
    if _rrf_channels_enabled():
        weights = [1.0] * len(rankings)
        path_ranking = _path_channel_ranking(chunks, query)
        if path_ranking:
            rankings.append(path_ranking)
            weights.append(PATH_CHANNEL_WEIGHT)

    fused_order = reciprocal_rank_fusion(rankings, k=k, weights=weights)
    # Position in the fused order is a monotonic proxy for the underlying RRF score: RRF ties are
    # already broken by ascending chunk index before this list is built, so using position
    # preserves the exact fused ordering while giving `match_score` below a single comparable
    # per-chunk number (mirrors `chunk_scores` in `rerank_by_bm25`).
    fused_score: dict[int, float] = {
        chunk_idx: 1.0 / (1 + position) for position, chunk_idx in enumerate(fused_order)
    }

    # file -> [(start_line, end_line, chunk_index)] for line-containment lookup.
    by_file: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for i, chunk in enumerate(chunks):
        by_file[chunk.file_path].append((chunk.start_line, chunk.end_line, i))

    def match_score(match) -> float:  # type: ignore[no-untyped-def]
        best = 0.0
        for start, end, i in by_file.get(match.file, ()):
            if start <= match.line_number <= end:
                best = max(best, fused_score.get(i, 0.0))
        return best

    # Stable sort by descending fused score (Python's sort is stable -> ties keep grep order).
    reranked = sorted(result.matches, key=match_score, reverse=True)
    return dataclasses.replace(result, matches=reranked)
