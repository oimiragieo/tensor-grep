"""Tests for pure Reciprocal Rank Fusion (RRF) -- the fusion primitive behind `tg search --semantic`
(Path B Stage 1, roadmap #27). No deps, no I/O: fixed-input, fixed-output determinism only."""

from __future__ import annotations

import pytest

from tensor_grep.core.retrieval_fusion import DEFAULT_K, reciprocal_rank_fusion


def test_default_k_is_60() -> None:
    assert DEFAULT_K == 60


def test_identity_single_leg_preserves_order() -> None:
    ranking = [3, 1, 2]
    assert reciprocal_rank_fusion([ranking]) == ranking


def test_empty_rankings_returns_empty() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[]]) == []


def test_chunk_absent_from_a_leg_contributes_zero() -> None:
    # chunk 2 is ranked last by leg_a but ALSO ranked (first) by leg_b -- it should out-rank
    # chunks that only appear in leg_a, since RRF sums contributions across legs.
    leg_a = [0, 1, 2]
    leg_b = [2]
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=60)
    assert fused[0] == 2
    assert set(fused) == {0, 1, 2}


def test_two_legs_combine_by_reciprocal_rank() -> None:
    leg_a = [0, 1, 2]
    leg_b = [1, 0, 2]
    # score(0) = 1/(1+1) + 1/(1+2) = 0.8333...; score(1) = 1/(1+2) + 1/(1+1) = 0.8333... (tie)
    # score(2) = 1/(1+3) + 1/(1+3) = 0.5 -- last regardless of the leg_a/leg_b tie-break above.
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=1)
    assert fused == [0, 1, 2]  # tie between 0 and 1 broken by ascending chunk index


def test_ties_break_by_ascending_chunk_index() -> None:
    # Each leg ranks a single, distinct chunk at position 1 -> identical fused score for both.
    fused = reciprocal_rank_fusion([[5], [3]], k=60)
    assert fused == [3, 5]


def test_k_is_a_tunable_smoothing_parameter() -> None:
    # A single leg's order is preserved for ANY positive k (k only rescales magnitude, never
    # reorders a single ranking).
    ranking = [4, 2, 0, 1]
    for k in (1, 10, 60, 1000):
        assert reciprocal_rank_fusion([ranking], k=k) == ranking


def test_invalid_k_raises() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], k=0)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], k=-1)


def test_deterministic_repeated_calls() -> None:
    rankings = [[2, 0, 1], [1, 2, 0]]
    assert reciprocal_rank_fusion(rankings) == reciprocal_rank_fusion(rankings)


def test_chunks_absent_from_every_leg_never_appear() -> None:
    fused = reciprocal_rank_fusion([[0, 1], [1, 0]])
    assert 2 not in fused
    assert set(fused) == {0, 1}
