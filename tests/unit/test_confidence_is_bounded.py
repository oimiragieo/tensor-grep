"""`confidence.overall` must always be a real number in [0.0, 1.0].

WHY THIS EXISTS
---------------
An adversarial security review of the shipped degraded-confidence guard (PR #1105, released
v1.113.2) raised a MED: a non-finite `overall` supplied through
`edit_plan_seed.confidence.overall` propagates unclamped, because `min(NaN, ceiling)` does not
bound NaN.

Probing the real function to confirm that found something the review did NOT name, and it is
worse than the NaN case:

    NaN  in -> overall = nan
    inf  in -> overall = inf
    5.0  in -> overall = 5.0     <-- a confidence ABOVE 1.0

The 5.0 case matters most. `confidence.overall` is a 0..1 contract that downstream logic compares
against thresholds (`< 0.75` decides `ask_user_before_editing`). A value of 5.0 is not merely
malformed: it passes every "is this confident enough" test by a wide margin and reads as
maximally certain, while carrying no downgrade reasons to contradict it. NaN is the safer of the
two failures, because NaN loses every comparison rather than winning them.

WHAT THIS ASSERTS
-----------------
One property, over the values that break it: the emitted `overall` is finite and within [0, 1].

WHAT IT DOES NOT CLAIM
----------------------
It says nothing about whether a particular value is CORRECT, and it does not change any
threshold. Bounding an out-of-contract number is not the same as deciding what a degraded result
should score -- that question is filed separately and deliberately left to a design review,
because it changes how often the tool interrupts a human.
"""

from __future__ import annotations

import math

import pytest

from tensor_grep.cli.agent_capsule_confidence import _confidence


def _consistency() -> dict[str, object]:
    return {"primary_file_included": True, "rendered_context_includes_primary": True}


def _emit(raw_overall: object, reasons: list[str] | None = None) -> float:
    payload = {"edit_plan_seed": {"confidence": {"overall": raw_overall}}}
    result = _confidence(payload, [{"path": "a.py"}], list(reasons or []), _consistency())
    return float(result["overall"])


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_non_finite_confidence_is_never_emitted(raw: float) -> None:
    """A non-finite number is not a confidence. `min(NaN, ceiling)` does not bound it."""
    got = _emit(raw)
    assert math.isfinite(got), f"emitted a non-finite confidence: {got!r}"
    assert 0.0 <= got <= 1.0


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(5.0, id="above-one"),
        pytest.param(1.5, id="just-above-one"),
        pytest.param(-3.0, id="below-zero"),
    ],
)
def test_out_of_range_confidence_is_clamped(raw: float) -> None:
    """The dangerous direction is ABOVE 1.0.

    `confidence.overall` is a 0..1 contract compared against thresholds (`< 0.75` gates
    `ask_user_before_editing`). A 5.0 does not merely look wrong -- it wins every such
    comparison and reads as maximally certain.
    """
    got = _emit(raw)
    assert 0.0 <= got <= 1.0, f"emitted an out-of-contract confidence: {got!r}"


def test_a_valid_confidence_is_returned_unchanged() -> None:
    """NEGATIVE CONTROL. Clamping must be a no-op on legitimate values.

    Without this, a bug that pinned every result to a constant would satisfy every assertion
    above while destroying the signal entirely.
    """
    for raw in (0.0, 0.25, 0.5, 0.9, 1.0):
        assert _emit(raw) == raw, f"a valid confidence {raw} was altered"


def test_bounding_does_not_defeat_the_degraded_ceiling() -> None:
    """The two guards must compose: out-of-range AND degraded stays under 1.0.

    A clamp applied in the wrong order could raise a degraded result back to 1.0.
    """
    got = _emit(5.0, reasons=["a reason the caller supplied"])
    assert got < 1.0, f"a degraded result emitted {got!r} -- the ceiling was defeated by clamping"
