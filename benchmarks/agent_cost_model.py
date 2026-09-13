"""Cache-aware token cost for agent-workflow benchmarks.

WHY THIS EXISTS. Every agent comparison in this repo reports RAW token counts, and raw counts
misprice agent work badly enough to invert a result. Under prompt caching a cached read costs
a fraction of a fresh input token while writing the cache costs MORE than one, so two runs
with identical total tokens can differ several-fold in money. A retrieval strategy that front-
loads a large bundle once and then re-reads it cheaply looks expensive by token count and is
cheap in practice; an agent that issues many small uncached calls looks cheap and is not.

The multipliers are expressed RELATIVE to one base input token, so this module never hardcodes
a dollar price -- per-model prices change, the cache ratios are a property of the caching
mechanism. Callers supply the base price when they want currency; the default output is in
base-token-equivalents, which is what a benchmark should compare.

SCOPE, stated rather than implied: this prices TOKENS under a documented cache multiplier. It
is not a billing oracle. It does not know about minimum charges, batch discounts, per-model
price tiers, or a provider changing its ratios, and it cannot tell you whether a cache hit was
possible -- it prices the hits a run REPORTS. A number from here is comparable BETWEEN ARMS of
one benchmark run; it is not an invoice.
"""

from __future__ import annotations

from dataclasses import dataclass

# Relative to one base input token. These are the documented Anthropic prompt-cache ratios:
# reading a cache hit is much cheaper than a fresh input token, and writing the cache costs a
# premium over one. They are CONSTANTS OF THE MECHANISM, not of any model's price.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25
BASE_INPUT_MULTIPLIER = 1.0


@dataclass(frozen=True)
class TokenUsage:
    """One run's reported token usage.

    Field names mirror the provider's own usage block so a caller is not silently mapping
    `input_tokens` onto a cache field -- the single easiest way to get this wrong is to count
    cached reads twice by also including them in `input_tokens`. Providers report them
    SEPARATELY, and this type assumes that same separation.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def total_raw_tokens(self) -> int:
        """The naive number most benchmarks report. Kept so a report can show BOTH and make
        the mispricing visible rather than asserting it.
        """
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )


def input_cost_in_base_tokens(usage: TokenUsage, *, output_multiplier: float) -> float:
    """Cache-weighted INPUT-side cost, in base-input-token equivalents.

    `output_multiplier` is required rather than defaulted: output pricing is a per-model ratio
    (commonly several times input), and a default here would silently bless one model's ratio
    for every model. A caller that does not know it does not have a cost model.
    """
    if output_multiplier < 0:
        raise ValueError("output_multiplier must be non-negative")
    for name, value in (
        ("input_tokens", usage.input_tokens),
        ("output_tokens", usage.output_tokens),
        ("cache_read_tokens", usage.cache_read_tokens),
        ("cache_write_tokens", usage.cache_write_tokens),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative, got {value}")

    return (
        usage.input_tokens * BASE_INPUT_MULTIPLIER
        + usage.cache_read_tokens * CACHE_READ_MULTIPLIER
        + usage.cache_write_tokens * CACHE_WRITE_MULTIPLIER
        + usage.output_tokens * output_multiplier
    )


def mispricing_factor(usage: TokenUsage, *, output_multiplier: float) -> float:
    """How badly a RAW token count misprices this run: raw / cache-weighted.

    Reported so a benchmark can state the size of the correction instead of quietly applying
    it. A factor near 1.0 means raw counts were fine for that run and the cache model changed
    nothing -- which is worth printing, because a correction that never moves a number is one
    nobody should trust on the run where it finally does.

    Returns 1.0 for an empty run rather than dividing by zero: no tokens cannot be mispriced.
    """
    weighted = input_cost_in_base_tokens(usage, output_multiplier=output_multiplier)
    if weighted <= 0:
        return 1.0
    return usage.total_raw_tokens() / weighted
