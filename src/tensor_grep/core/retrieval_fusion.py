"""Reciprocal Rank Fusion (RRF) over independent per-leg chunk rankings.

Pure, dependency-free fusion for combining the BM25 lexical leg and the dense embedding leg into
one ranking without ever comparing their raw scores directly -- a BM25 score and a cosine
similarity live on unrelated scales, so normalizing-and-adding them would be an apples-to-oranges
hack that silently drifts as either scorer changes. RRF sidesteps this by fusing on RANK alone:
``fused(c) = sum over legs of 1 / (k + rank_r(c))`` (1-based rank; a chunk absent from a leg
contributes 0 for that leg). This is the standard formulation (Cormack, Clarke & Buettcher 2009).
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT_K: int = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    *,
    k: int = DEFAULT_K,
) -> list[int]:
    """Fuse per-leg chunk-index rankings into one ranking via Reciprocal Rank Fusion.

    Each element of ``rankings`` is an ordered sequence of chunk indices for one retrieval leg
    (best first). A chunk missing from a leg's ranking contributes 0 to that leg's term. Ties in
    the fused score break by ascending chunk index (mirrors ``retrieval_bm25.py``'s tie-break),
    so the result is fully deterministic.

    Returns the fused chunk indices ordered best-first. Chunks that appear in NO leg are absent
    from the input entirely and therefore never appear in the output.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_index in enumerate(ranking, start=1):
            scores[chunk_index] = scores.get(chunk_index, 0.0) + 1.0 / (k + rank)

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [chunk_index for chunk_index, _ in ordered]
