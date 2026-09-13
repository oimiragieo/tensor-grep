"""Cache-aware token cost for agent benchmarks.

The point of this module is that a RAW token count can invert a benchmark result, so the tests
that matter are the ones showing the two metrics actually disagree -- a cost model that always
agrees with the naive number is decoration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
if str(_BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS))

from agent_cost_model import (  # noqa: E402
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    TokenUsage,
    input_cost_in_base_tokens,
    mispricing_factor,
)

_OUT = 5.0  # a plausible output:input ratio; every test states its own rather than defaulting


def test_a_cached_read_costs_a_fraction_of_a_fresh_input_token() -> None:
    fresh = TokenUsage(input_tokens=1000)
    cached = TokenUsage(cache_read_tokens=1000)

    fresh_cost = input_cost_in_base_tokens(fresh, output_multiplier=_OUT)
    cached_cost = input_cost_in_base_tokens(cached, output_multiplier=_OUT)

    assert fresh_cost == 1000.0
    assert cached_cost == pytest.approx(1000 * CACHE_READ_MULTIPLIER)
    assert cached_cost < fresh_cost


def test_writing_the_cache_costs_more_than_a_fresh_token() -> None:
    """The half everyone forgets. A strategy that front-loads a big bundle pays a PREMIUM on
    the first call; pricing cache writes at 1.0 would make front-loading look free.
    """
    written = TokenUsage(cache_write_tokens=1000)
    cost = input_cost_in_base_tokens(written, output_multiplier=_OUT)

    assert cost == pytest.approx(1000 * CACHE_WRITE_MULTIPLIER)
    assert cost > 1000.0


def test_raw_token_count_and_cache_weighted_cost_can_rank_two_arms_OPPOSITELY() -> None:
    """THE reason this module exists, as an executable claim rather than a comment.

    Arm A front-loads a bundle once and re-reads it cheaply. Arm B issues many small uncached
    calls. A ranks as the EXPENSIVE one by raw token count and the CHEAP one by real cost.
    If this ever stops holding, the cost model has stopped doing anything.
    """
    # Written ONCE (4k), then re-read across many turns (60k) -- the shape a front-loaded
    # bundle actually has. The first parameterisation of this test used a 20k write and did
    # NOT invert (31000 vs 30000); the test refused it, which is the whole point of asserting
    # the inversion rather than describing it in a comment.
    front_loaded = TokenUsage(cache_write_tokens=4_000, cache_read_tokens=60_000)
    many_small = TokenUsage(input_tokens=30_000)

    assert front_loaded.total_raw_tokens() > many_small.total_raw_tokens()

    front_cost = input_cost_in_base_tokens(front_loaded, output_multiplier=_OUT)
    small_cost = input_cost_in_base_tokens(many_small, output_multiplier=_OUT)

    assert front_cost < small_cost, (
        f"the ranking must INVERT: raw says {front_loaded.total_raw_tokens()} > "
        f"{many_small.total_raw_tokens()} while cost says {front_cost} < {small_cost}"
    )


def test_mispricing_factor_reports_the_size_of_the_correction() -> None:
    usage = TokenUsage(cache_read_tokens=10_000)
    factor = mispricing_factor(usage, output_multiplier=_OUT)

    # All cached reads at 0.1x: raw over-states cost by 10x.
    assert factor == pytest.approx(10.0)


def test_mispricing_factor_is_one_when_the_cache_model_changes_nothing() -> None:
    """CONTROL for the test above. If the factor were always >1 it would carry no information;
    a run with no cache activity must report exactly 1.0, so a reader can tell the correction
    apart from a constant.
    """
    usage = TokenUsage(input_tokens=5_000)
    assert mispricing_factor(usage, output_multiplier=_OUT) == pytest.approx(1.0)


def test_an_empty_run_cannot_be_mispriced_and_does_not_divide_by_zero() -> None:
    assert mispricing_factor(TokenUsage(), output_multiplier=_OUT) == 1.0
    assert input_cost_in_base_tokens(TokenUsage(), output_multiplier=_OUT) == 0.0


def test_output_multiplier_is_required_not_defaulted() -> None:
    """Output pricing is a PER-MODEL ratio. A default would silently bless one model's ratio
    for every model, which is how a cost model becomes confidently wrong.
    """
    with pytest.raises(TypeError):
        input_cost_in_base_tokens(TokenUsage(output_tokens=1))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "usage",
    [
        TokenUsage(input_tokens=-1),
        TokenUsage(output_tokens=-1),
        TokenUsage(cache_read_tokens=-1),
        TokenUsage(cache_write_tokens=-1),
    ],
    ids=["input", "output", "cache_read", "cache_write"],
)
def test_negative_usage_fails_closed_rather_than_producing_a_cheaper_number(usage) -> None:
    """A negative token count is a broken harness, and silently summing it would make a run
    look CHEAPER than free -- the failure direction that flatters whoever is measuring.
    """
    with pytest.raises(ValueError):
        input_cost_in_base_tokens(usage, output_multiplier=_OUT)


def test_a_negative_output_multiplier_is_refused() -> None:
    with pytest.raises(ValueError):
        input_cost_in_base_tokens(TokenUsage(output_tokens=10), output_multiplier=-1.0)
