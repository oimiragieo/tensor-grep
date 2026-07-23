"""Reciprocal Rank Fusion (RRF) over independent per-leg chunk rankings.

Pure, dependency-free fusion for combining the BM25 lexical leg and the dense embedding leg into
one ranking without ever comparing their raw scores directly -- a BM25 score and a cosine
similarity live on unrelated scales, so normalizing-and-adding them would be an apples-to-oranges
hack that silently drifts as either scorer changes. RRF sidesteps this by fusing on RANK alone,
per-leg term ``1 / (k + rank_r(c))`` (1-based rank; a chunk absent from a leg contributes a 0.0
floor for that leg -- every term is strictly positive, so 0.0 is always a correct lower bound).

Two ways to COMBINE those per-leg terms into one fused score are supported (``combine``):

- ``"max"`` (the DEFAULT, accuracy-leg campaign): ``fused(c) = max over legs of term_r(c)`` --
  best-rank-wins. A chunk's fused score is the single strongest leg contribution it earns, never
  the sum of every leg -- so a weak/near-floor leg (e.g. BM25 on a vocabulary-mismatched NL query)
  can only ever HELP a chunk's rank (by ranking it even higher than the strong leg did) and can
  never DRAG a strong leg's pick down merely by failing to rank it. Measured on the frozen
  golden-set harness (``benchmarks/eval_late_rerank_quality.py``'s ``rrf`` arm): ndcg@10 lifts from
  0.3047 (sum) to 0.4953 (max), +62.6%, uniformly across recall@10 (0.55->0.825) and mrr
  (0.228->0.391) -- re-run ``uv run --no-sync python benchmarks/eval_late_rerank_quality.py`` to
  reproduce.
- ``"sum"`` (the ORIGINAL formulation, Cormack, Clarke & Buettcher 2009): ``fused(c) = sum over
  legs of term_r(c)``. Byte-identical to this module's pre-max-flip behavior; kept for any call
  site that must reproduce it exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

DEFAULT_K: int = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    *,
    k: int = DEFAULT_K,
    weights: Sequence[float] | None = None,
    combine: Literal["sum", "max"] = "max",
) -> list[int]:
    """Fuse per-leg chunk-index rankings into one ranking via Reciprocal Rank Fusion.

    Each element of ``rankings`` is an ordered sequence of chunk indices for one retrieval leg
    (best first). A chunk missing from a leg's ranking contributes a 0.0 floor to that leg's term.
    Ties in the fused score break by ascending chunk index (mirrors ``retrieval_bm25.py``'s
    tie-break), so the result is fully deterministic.

    ``weights`` (optional) is a per-leg multiplier parallel to ``rankings`` -- leg ``i``'s
    contribution becomes ``weight[i] / (k + rank)`` instead of the plain ``1 / (k + rank)``, so a
    channel can be trusted more (>1.0) or less (<1.0) than the others without changing how it is
    ranked internally. This multiplier is applied to a leg's term BEFORE ``combine`` folds the
    legs together (i.e. before the sum, or before the max). ``weights=None`` (the default) is a
    byte-identical no-op: every leg is implicitly weighted 1.0, reproducing the unweighted fusion
    exactly (multiplying by 1.0 is an exact IEEE-754 operation). Raises :class:`ValueError` if
    ``weights`` is given and its length does not match ``rankings``.

    ``combine`` (``"sum"`` or ``"max"``, default ``"max"`` -- see the module docstring for the
    full derivation and the golden-set evidence behind the default): how each chunk's per-leg
    terms are folded into one fused score.

    Returns the fused chunk indices ordered best-first. Chunks that appear in NO leg are absent
    from the input entirely and therefore never appear in the output.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if weights is not None and len(weights) != len(rankings):
        raise ValueError(
            f"weights length ({len(weights)}) must match rankings length ({len(rankings)})"
        )
    if combine not in ("sum", "max"):
        raise ValueError(f"combine must be 'sum' or 'max', got {combine!r}")

    scores: dict[int, float] = {}
    for leg_index, ranking in enumerate(rankings):
        weight = 1.0 if weights is None else weights[leg_index]
        for rank, chunk_index in enumerate(ranking, start=1):
            term = (1.0 / (k + rank)) * weight
            if combine == "max":
                scores[chunk_index] = max(scores.get(chunk_index, 0.0), term)
            else:
                scores[chunk_index] = scores.get(chunk_index, 0.0) + term

    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [chunk_index for chunk_index, _ in ordered]
