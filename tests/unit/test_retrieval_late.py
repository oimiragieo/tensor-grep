"""Tests for the late-interaction (MaxSim) rerank foundation.

Foundation increment only (design doc "T0-T2",
docs/plans/design-tensor-grep-late-rerank-2026-07-09.md): pure MaxSim math plus the
``LateReranker`` contract against an INJECTED stub encoder. There is no ONNX model at this stage
-- T3 wires a real encoder behind the ``rerank`` extra; these tests never need it installed.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from tensor_grep.core.retrieval_late import LateReranker, maxsim_scores, rank_by_maxsim


def test_maxsim_hand_computed_values() -> None:
    # 3 query tokens, D=2, all already unit-length and axis-aligned so every dot product is
    # trivially 0 or 1 by hand. doc_a shares an axis with every query token (perfect match each
    # time); doc_b only matches the middle query token.
    query_matrix = np.array(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 0.0],
        ],
        dtype=np.float32,
    )
    doc_a = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    doc_b = np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32)

    # doc_a: max_j(q0.d_j)=1 (d0) + max_j(q1.d_j)=1 (d1) + max_j(q2.d_j)=1 (d0) = 3.0
    # doc_b: max_j(q0.d_j)=0        + max_j(q1.d_j)=1 (d0 or d1) + max_j(q2.d_j)=0        = 1.0
    scores = maxsim_scores(query_matrix, [doc_a, doc_b])

    assert scores == pytest.approx([3.0, 1.0])


def test_maxsim_empty_doc_scores_zero() -> None:
    # A doc with zero tokens has nothing to compare against -- must score 0.0, not raise (numpy's
    # max(axis=1) on a zero-size reduction would otherwise blow up deep inside the module).
    query_matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    empty_doc = np.zeros((0, 2), dtype=np.float32)
    real_doc = np.array([[1.0, 0.0]], dtype=np.float32)

    scores = maxsim_scores(query_matrix, [empty_doc, real_doc])

    assert scores == pytest.approx([0.0, 1.0])


def test_maxsim_ties_break_by_ascending_index() -> None:
    # Two equal scores at indices 10 and 3 must resolve ascending by INDEX VALUE, not by position
    # in the input lists -- index 3 is listed second but must still rank before index 10.
    scores = [1.0, 1.0, 2.0]
    indices = [10, 3, 7]

    assert rank_by_maxsim(scores, indices) == [7, 3, 10]


def _stub_encoder(vectors: dict[str, np.ndarray]) -> Callable[[str], np.ndarray]:
    """A deterministic dict-lookup encoder: maps exact text -> a pre-built (T, D) token matrix."""

    def encode(text: str) -> np.ndarray:
        return vectors[text]

    return encode


def test_rerank_returns_permutation_never_drops() -> None:
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "chunk-a": np.array([[1.0, 0.0]], dtype=np.float32),
        "chunk-b": np.array([[0.0, 1.0]], dtype=np.float32),
        "chunk-c": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)
    indices = [42, 7, 100]

    result = reranker.rerank("query", ["chunk-a", "chunk-b", "chunk-c"], indices)

    # A permutation: same multiset of indices, no adds, no drops, no duplicates.
    assert sorted(result) == sorted(indices)
    assert len(result) == len(indices)


def test_rerank_orders_by_maxsim_desc() -> None:
    # chunk "match" (index 9) is parallel to the query -> MaxSim 1.0; chunk "nomatch" (index 5) is
    # orthogonal -> MaxSim 0.0. Indices are given in ASCENDING order (5 before 9), so a
    # passthrough (non-reordering) implementation would wrongly return [5, 9].
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "nomatch": np.array([[0.0, 1.0]], dtype=np.float32),
        "match": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)

    result = reranker.rerank("query", ["nomatch", "match"], [5, 9])

    assert result == [9, 5]


def test_rerank_ties_break_by_ascending_original_index() -> None:
    # Both chunks encode to the SAME vector -> tied MaxSim score. Indices are given
    # out-of-ascending-order (8 before 2), so the tie-break must still resolve to [2, 8] -- ascending
    # by the ORIGINAL index value, not by position in the input lists.
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "same": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)

    result = reranker.rerank("query", ["same", "same"], [8, 2])

    assert result == [2, 8]


def test_rerank_empty_pool_returns_empty() -> None:
    def _unreachable(text: str) -> np.ndarray:
        raise AssertionError("encode must not be called for an empty pool")

    reranker = LateReranker(_unreachable)

    assert reranker.rerank("query", [], []) == []
