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
    weights: Sequence[float] | None = None,
) -> list[int]:
    """Fuse per-leg chunk-index rankings into one ranking via Reciprocal Rank Fusion.

    Each element of ``rankings`` is an ordered sequence of chunk indices for one retrieval leg
    (best first). A chunk missing from a leg's ranking contributes 0 to that leg's term. Ties in
    the fused score break by ascending chunk index (mirrors ``retrieval_bm25.py``'s tie-break),
    so the result is fully deterministic.

    ``weights`` (optional) is a per-leg multiplier parallel to ``rankings`` -- leg ``i``'s
    contribution becomes ``weight[i] / (k + rank)`` instead of the plain ``1 / (k + rank)``, so a
    channel can be trusted more (>1.0) or less (<1.0) than the others without changing how it is
    ranked internally. ``weights=None`` (the default) is a byte-identical no-op: every leg is
    implicitly weighted 1.0, reproducing the un-weighted fusion exactly (multiplying by 1.0 is an
    exact IEEE-754 operation). Raises :class:`ValueError` if ``weights`` is given and its length
    does not match ``rankings``.

    Returns the fused chunk indices ordered best-first. Chunks that appear in NO leg are absent
    from the input entirely and therefore never appear in the output.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if weights is not None and len(weights) != len(rankings):
        raise ValueError(
            f"weights length ({len(weights)}) must match rankings length ({len(rankings)})"
        )

    scores: dict[int, float] = {}
    for leg_index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[leg_index]
        for rank, chunk_index in enumerate(ranking, start=1):
            scores[chunk_index] = scores.get(chunk_index, 0.0) + (1.0 / (k + rank)) * weight

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [chunk_index for chunk_index, _ in ordered]
