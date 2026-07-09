"""Late-interaction (MaxSim / ColBERT-style) rerank foundation for `tg search --semantic`
(roadmap docs/plans/design-tensor-grep-late-rerank-2026-07-09.md).

This module is the FOUNDATION increment only (the design doc's T0-T2): pure MaxSim math plus the
:class:`LateReranker` contract against an INJECTED token encoder. There is no ONNX model here yet
-- that lands in T3 (`late_available()` probe + `load_late_model()`), and the seam wiring into
`rerank_hybrid` lands in T5. Every caller in this increment supplies its own ``encode`` callable
(tests use a deterministic stub; T3+ wires a real ONNX encoder behind the ``rerank`` extra).

Assumption: both :func:`maxsim_scores` inputs are ALREADY L2-normalized per token (each row a unit
vector) -- this module does not normalize them. A plain dot product between two normalized rows IS
cosine similarity, so ``MaxSim(q, d) = sum_i max_j (q_i . d_j)`` is a sum of per-query-token cosine
maxima. Callers (the T3 ONNX encoder, or a test's stub) own normalization.

Fail-closed contract (see AGENTS.md "Backend Fail-Closed Contract" and retrieval_dense.py:9-19):
this increment does NOT yet raise a recoverable-unavailable error or
:class:`~tensor_grep.backends.base.BackendExecutionError` -- that wiring (extra-not-installed
probe, latency-budget enforcement, ONNX load faults) lands in T3/T6. :meth:`LateReranker.rerank`
here assumes its injected ``encode`` callable already succeeded; a raising ``encode`` propagates
raw until then.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


def maxsim_scores(query_matrix: np.ndarray, doc_matrices: Sequence[np.ndarray]) -> list[float]:
    """MaxSim(query, doc) for each doc in ``doc_matrices``, returned in the same order.

    ``query_matrix`` is ``(Tq, D)``; each ``doc_matrices[k]`` is ``(Tdk, D)`` -- both assumed
    already L2-normalized per row (see module docstring). Score = sum over query-token rows of
    that row's max dot product against any doc-token row: ``sum_i max_j (q_i . d_j)``.

    A doc with zero tokens (an empty ``(0, D)`` matrix) scores 0.0 -- there is nothing to compare
    against, and numpy's ``.max(axis=1)`` would otherwise raise on a zero-size reduction.
    """
    import numpy as np

    scores: list[float] = []
    for doc_matrix in doc_matrices:
        if query_matrix.size == 0 or doc_matrix.size == 0:
            scores.append(0.0)
            continue
        similarity = query_matrix @ doc_matrix.T  # (Tq, Td)
        scores.append(float(np.max(similarity, axis=1).sum()))
    return scores


def rank_by_maxsim(scores: Sequence[float], indices: Sequence[int]) -> list[int]:
    """Rank ``indices`` by descending ``scores``; ties break by ascending index VALUE.

    Pure ordering utility -- ``scores[i]`` is the MaxSim score already computed for
    ``indices[i]``. Mirrors the ascending-index tie-break determinism contract used by
    ``Bm25Index.query`` / ``DenseIndex.query`` (retrieval_bm25.py:77, retrieval_dense.py:192),
    except the tie-break key is the ORIGINAL index value rather than list position -- callers pass
    an arbitrary pre-ranked subset (the RRF-fused pool), not the full corpus in index order, so
    position alone would not be a stable, input-order-independent contract.
    """
    paired = zip(indices, scores, strict=True)
    return [idx for idx, _ in sorted(paired, key=lambda pair: (-pair[1], pair[0]))]


class LateReranker:
    """Order-only MaxSim reranker over an already-pooled candidate set.

    The token encoder is INJECTED (``encode: str -> (T, D) ndarray``), so unit tests exercise a
    deterministic stub -- no ONNX model required at this stage (T3 wires the real encoder behind
    the ``rerank`` extra). Mirrors :class:`~tensor_grep.core.retrieval_dense.DenseIndex`'s
    dependency-injection shape (retrieval_dense.py:155, the model is passed in already loaded)
    rather than owning model lifecycle itself.
    """

    def __init__(self, encode: Callable[[str], np.ndarray]) -> None:
        self._encode = encode

    def rerank(self, query: str, chunks: list[str], indices: list[int]) -> list[int]:
        """Return ``indices`` permuted by MaxSim(query, chunk) descending; ties break by
        ascending original index value.

        Output is EXACTLY ``indices`` reordered -- never adds, never drops (the seam this feeds,
        ``rerank_hybrid``, splices this back over a ``fused_order`` head slice and must preserve
        membership; design doc "The seam"). An empty pool returns ``[]`` without invoking
        ``encode`` at all.

        ``chunks[i]`` must correspond to ``indices[i]`` (same length, position-aligned) -- the
        caller's already-pooled candidate set.
        """
        if not indices:
            return []
        query_matrix = self._encode(query)
        doc_matrices = [self._encode(chunk) for chunk in chunks]
        scores = maxsim_scores(query_matrix, doc_matrices)
        return rank_by_maxsim(scores, indices)
