"""Tests for pure Reciprocal Rank Fusion (RRF) -- the fusion primitive behind `tg search --semantic`
(Path B Stage 1, roadmap #27). No deps, no I/O: fixed-input, fixed-output determinism only."""

from __future__ import annotations

import pytest

from tensor_grep.core.retrieval_fusion import DEFAULT_K, reciprocal_rank_fusion

# --- Legacy SUM-combine regression pins ---------------------------------------------------------
# Accuracy-leg campaign (max-fusion default flip, #711-class): `combine` defaults to "max" as of
# this change, so every test below pins `combine="sum"` EXPLICITLY -- these tests exist to prove
# the SUM path (the ORIGINAL, pre-flip behavior) stays byte-identical forever, for any future call
# site that must keep it (see `retrieval_fusion.py`'s own docstring). Before this change none of
# these calls passed `combine` at all (there was no such parameter); pinning it here is what makes
# "byte-identical to today" a checked fact rather than a claim.


def test_default_k_is_60() -> None:
    assert DEFAULT_K == 60


def test_identity_single_leg_preserves_order() -> None:
    ranking = [3, 1, 2]
    assert reciprocal_rank_fusion([ranking], combine="sum") == ranking


def test_empty_rankings_returns_empty() -> None:
    assert reciprocal_rank_fusion([], combine="sum") == []
    assert reciprocal_rank_fusion([[]], combine="sum") == []


def test_chunk_absent_from_a_leg_contributes_zero() -> None:
    # chunk 2 is ranked last by leg_a but ALSO ranked (first) by leg_b -- it should out-rank
    # chunks that only appear in leg_a, since RRF SUMS contributions across legs.
    leg_a = [0, 1, 2]
    leg_b = [2]
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=60, combine="sum")
    assert fused[0] == 2
    assert set(fused) == {0, 1, 2}


def test_two_legs_combine_by_reciprocal_rank() -> None:
    leg_a = [0, 1, 2]
    leg_b = [1, 0, 2]
    # score(0) = 1/(1+1) + 1/(1+2) = 0.8333...; score(1) = 1/(1+2) + 1/(1+1) = 0.8333... (tie)
    # score(2) = 1/(1+3) + 1/(1+3) = 0.5 -- last regardless of the leg_a/leg_b tie-break above.
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=1, combine="sum")
    assert fused == [0, 1, 2]  # tie between 0 and 1 broken by ascending chunk index


def test_ties_break_by_ascending_chunk_index() -> None:
    # Each leg ranks a single, distinct chunk at position 1 -> identical fused score for both.
    fused = reciprocal_rank_fusion([[5], [3]], k=60, combine="sum")
    assert fused == [3, 5]


def test_k_is_a_tunable_smoothing_parameter() -> None:
    # A single leg's order is preserved for ANY positive k (k only rescales magnitude, never
    # reorders a single ranking).
    ranking = [4, 2, 0, 1]
    for k in (1, 10, 60, 1000):
        assert reciprocal_rank_fusion([ranking], k=k, combine="sum") == ranking


def test_invalid_k_raises() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], k=0)
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], k=-1)


def test_deterministic_repeated_calls() -> None:
    rankings = [[2, 0, 1], [1, 2, 0]]
    assert reciprocal_rank_fusion(rankings, combine="sum") == reciprocal_rank_fusion(
        rankings, combine="sum"
    )


def test_chunks_absent_from_every_leg_never_appear() -> None:
    fused = reciprocal_rank_fusion([[0, 1], [1, 0]], combine="sum")
    assert 2 not in fused
    assert set(fused) == {0, 1}


# --- PR-S2: channelized RRF (`weights` param) -------------------------------------------------
# ADDITIVE: `weights=None` (the default) must reproduce today's output bit-for-bit. Every test
# above this marker calls `reciprocal_rank_fusion` without `weights` and continues to pass
# UNMODIFIED after this change -- that is itself the strongest byte-identical no-op proof.
# (All now pinned to combine="sum" too, per the note above.)


def test_weights_omitted_and_weights_none_are_identical() -> None:
    rankings = [[2, 0, 1], [1, 2, 0], [0, 1, 2]]
    assert reciprocal_rank_fusion(rankings, k=17, combine="sum") == reciprocal_rank_fusion(
        rankings, k=17, weights=None, combine="sum"
    )


def test_weights_all_ones_is_bit_identical_to_none() -> None:
    # Multiplying by 1.0 is an exact IEEE-754 no-op, so all-ones weights must reproduce the
    # unweighted fused order exactly -- not just an equivalent order, the SAME order.
    rankings = [[0, 1, 2], [2, 1, 0], [1, 0, 2]]
    assert reciprocal_rank_fusion(
        rankings, k=10, weights=None, combine="sum"
    ) == reciprocal_rank_fusion(rankings, k=10, weights=[1.0, 1.0, 1.0], combine="sum")


def test_weights_changes_fused_order_in_expected_direction() -> None:
    # leg_a puts chunk 0 first, leg_b puts chunk 1 first -- symmetric, so equal weights tie
    # (broken by ascending chunk index -> chunk 0 wins). Upweighting leg_b should flip the winner
    # to chunk 1.
    leg_a = [0, 1]
    leg_b = [1, 0]
    equal = reciprocal_rank_fusion([leg_a, leg_b], k=10, combine="sum")
    assert equal[0] == 0

    weighted = reciprocal_rank_fusion([leg_a, leg_b], k=10, weights=[1.0, 2.0], combine="sum")
    assert weighted[0] == 1


def test_weights_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1], [1, 0]], weights=[1.0])
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], weights=[1.0, 2.0])


def test_deterministic_repeated_calls_with_weights() -> None:
    rankings = [[2, 0, 1], [1, 2, 0]]
    weights = [1.5, 0.5]
    assert reciprocal_rank_fusion(
        rankings, weights=weights, combine="sum"
    ) == reciprocal_rank_fusion(rankings, weights=weights, combine="sum")


# --- Accuracy-leg campaign: max-combine (best-rank-wins), the NEW default ----------------------
# `combine="max"` lifts the frozen golden-set `rrf` arm's ndcg@10 from 0.3047 to 0.4953 (+62.6%)
# by construction: a chunk's fused score is the BEST single-leg contribution it earns, never the
# sum of all legs -- so a weak/near-floor leg (bm25 on a vocabulary-mismatched NL query) can only
# ever HELP a doc's rank (if it ranks the doc even higher than the strong leg did) and can never
# DRAG a strong leg's pick down by simply failing to rank it. See docs/PAPER.md for the full
# golden-set evidence; these tests pin the MECHANISM in isolation.


def test_default_combine_is_max() -> None:
    """DEFAULT = "max": omitting `combine` entirely must be byte-identical to passing
    combine="max" explicitly -- this is what makes the default change actually move every
    existing call site (reranker.py's `rank_chunks`, `tg find`, the eval harness's `rrf`/
    `rrf_shipped` arms) with ZERO call-site edits. And it must NOT reproduce the old sum default
    on a scenario where the two provably diverge (see test_chunk_absent_from_a_leg_contributes_zero
    above, the sum-pinned twin of this exact scenario)."""
    leg_a, leg_b = [0, 1, 2], [2]
    assert reciprocal_rank_fusion([leg_a, leg_b], k=60) == reciprocal_rank_fusion(
        [leg_a, leg_b], k=60, combine="max"
    )
    assert reciprocal_rank_fusion([leg_a, leg_b], k=60) != reciprocal_rank_fusion(
        [leg_a, leg_b], k=60, combine="sum"
    )


def test_max_combine_absent_leg_floors_at_zero_not_added() -> None:
    """Mirrors test_chunk_absent_from_a_leg_contributes_zero's SUM scenario exactly, but pins the
    MAX outcome: chunk 2 is worst-ranked (#3) in leg_a yet #1 in leg_b. Under sum this SUMS to the
    top score (see the sum-pinned twin above); under max, chunk 2's score is just its BEST single
    term (``max(1/(k+3), 1/(k+1)) == 1/(k+1)``) -- which exactly TIES chunk 0 (leg_a's own #1,
    absent from leg_b, floored at +0.0 there: ``max(1/(k+1), 0.0) == 1/(k+1)``). The tie resolves
    to ascending chunk index, so chunk 0 -- not chunk 2 -- now leads."""
    leg_a = [0, 1, 2]
    leg_b = [2]
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=60, combine="max")
    assert fused[0] == 0, "chunk 0 and chunk 2 tie at 1/(k+1) under max; ascending index wins"
    assert set(fused) == {0, 1, 2}


def test_max_combine_uses_best_rank_not_sum() -> None:
    """The hand-computed fixture: a doc ranked #1 by one leg and #50 by another must fuse to
    EXACTLY that leg's #1 contribution under combine="max" (``1/(k+1)``) -- NOT the sum of both
    contributions (``1/(k+1) + 1/(k+50)``). Proven black-box (no internal score access) via a
    second doc that is ALSO rank #1, but in a THIRD leg `x` never appears in: under combine="max"
    the two docs' scores are bit-identical (both exactly ``1/(k+1)``), so only the ascending-index
    tie-break can order them; under combine="sum" `x`'s extra leg_b contribution makes it strictly
    higher, so `x` wins outright regardless of index order. `y`'s index is deliberately LOWER than
    `x`'s so the two combine modes visibly disagree on the winner."""
    k = 60
    x, y = 100, 0
    leg_a = [x]  # x: rank 1
    leg_b = [*range(1, 50), x]  # x: rank 50 (49 unrelated fillers ranked ahead of it)
    leg_c = [y]  # y: rank 1, in a leg x never appears in

    fused_sum = reciprocal_rank_fusion([leg_a, leg_b, leg_c], k=k, combine="sum")
    assert fused_sum.index(x) < fused_sum.index(y), (
        "sum: x's extra leg_b contribution must make it strictly outrank y"
    )

    fused_max = reciprocal_rank_fusion([leg_a, leg_b, leg_c], k=k, combine="max")
    assert fused_max.index(y) < fused_max.index(x), (
        "max: x's leg_b rank-50 contribution must be DISCARDED (leg_a's rank-1 term already "
        "wins x's own max) -- x and y both score exactly 1/(k+1), so the ascending tie-break "
        "(lower index y=0 first) decides, proving leg_b was never summed in"
    )


def test_max_combine_weights_apply_before_max() -> None:
    """A per-leg weight multiplies that leg's OWN term BEFORE the max is taken across legs --
    mirrors reranker.py's `dense_weight` composing with combine="max" (the production `tg find` /
    `--semantic` path, and the `rrf_shipped` eval arm). leg_a ranks chunk 0 at #1 (weight 1.0 ->
    term ``1/(k+1)``); leg_b ranks chunk 1 at #1 too but weighted 3x (term ``3/(k+1)``) -- under
    max, chunk 1's weighted term dominates."""
    leg_a = [0]
    leg_b = [1]
    k = 10
    fused = reciprocal_rank_fusion([leg_a, leg_b], k=k, weights=[1.0, 3.0], combine="max")
    assert fused[0] == 1, "leg_b's 3x-weighted rank-1 term must beat leg_a's unweighted rank-1 term"


def test_max_combine_deterministic_repeated_calls() -> None:
    rankings = [[2, 0, 1], [1, 2, 0]]
    assert reciprocal_rank_fusion(rankings, combine="max") == reciprocal_rank_fusion(
        rankings, combine="max"
    )


def test_invalid_combine_raises() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([[0, 1]], combine="average")  # type: ignore[arg-type]
