"""The confidence invariant enforced in PRODUCTION, not merely asserted in a test.

WHY THIS EXISTS
---------------
`tests/unit/test_prepare_confidence_invariants.py` already pins

    I1  downgrade_reasons non-empty  =>  confidence.overall < 1.0

over the real emitted payload, and it passes. A second external agent dogfood
(2026-08-23, v1.113.0) nevertheless reported the violation again:
`partial=true`, truncation in `downgrade_reasons`, and `confidence.overall == 1.0`.

Both things are true because the existing file pins the invariant for the cases IT
constructs. It is a test of some paths, not a property of the function. `_confidence`
has a hole those paths do not reach:

  * `overall` is taken verbatim from `edit_plan_seed.confidence.overall` when present,
    so a caller can hand in 1.0;
  * `consistency["confidence_downgraded"]` appends a downgrade reason with NO clamp
    (agent_capsule_confidence.py, the `confidence_downgraded` branch);
  * a caller-supplied non-empty `downgrade_reasons` list is likewise never clamped.

So the emitted capsule can say "here are the reasons this is degraded" and "I am
completely certain" in the same breath -- the single most dangerous shape this tool
emits, because `ask_user_before_editing` keys off confidence and an agent will edit
without asking.

The fix is a GUARD at the return, not another case-by-case clamp. Case-by-case is what
produced the hole: four of the six reason-appending branches clamp and two do not, and
nothing makes a NEW branch clamp. A guard at the single exit makes the invariant hold
for every branch that exists and every branch anyone adds later.

THE CONTROL
-----------
`test_guard_is_not_vacuous` proves the guard can actually fire by driving the exact
production shape that violates it. Without that, a guard that never runs looks identical
to a guard that works.
"""

from __future__ import annotations

from tensor_grep.cli.agent_capsule_confidence import _confidence


def _consistency(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "primary_file_included": True,
        "rendered_context_includes_primary": True,
    }
    base.update(overrides)
    return base


def test_a_reason_supplied_by_the_caller_forces_confidence_below_one() -> None:
    """The plainest form: reasons in, certainty out. Must be impossible."""
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}},
        [{"path": "a.py"}],
        ["a reason the caller already knew about"],
        _consistency(),
    )
    assert result["downgrade_reasons"], "precondition: this case must carry a reason"
    assert result["overall"] < 1.0, (
        f"emitted overall={result['overall']} with downgrade_reasons="
        f"{result['downgrade_reasons']} -- the capsule claims certainty while listing the "
        f"reasons it is degraded, and ask_user_before_editing keys off this number"
    )


def test_consistency_downgrade_forces_confidence_below_one() -> None:
    """The branch with no clamp of its own.

    `confidence_downgraded` appends a reason and returns; nothing lowers `overall`. With a
    caller-supplied 1.0 this emits certain-but-degraded.
    """
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}},
        [{"path": "a.py"}],
        [],
        _consistency(confidence_downgraded=True),
    )
    assert "context consistency downgraded confidence" in result["downgrade_reasons"]
    assert result["overall"] < 1.0, (
        f"the consistency-downgrade branch appended a reason but left overall at "
        f"{result['overall']}"
    )


def test_truncation_forces_confidence_below_one() -> None:
    """The dogfood's own shape: a truncated scan may never be CERTAIN."""
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}, "truncated": True},
        [{"path": "a.py"}],
        [],
        _consistency(),
    )
    assert result["downgrade_reasons"]
    assert result["overall"] < 1.0


def test_a_clean_result_keeps_full_confidence() -> None:
    """NEGATIVE CONTROL. The guard must not be a blanket cap.

    If this ever fails, the guard is lowering confidence on results that carry no reason at
    all, which would make the number meaningless in the other direction.
    """
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}},
        [{"path": "a.py"}],
        [],
        _consistency(),
    )
    assert result["downgrade_reasons"] == [], "precondition: this case must be clean"
    assert result["overall"] == 1.0, (
        "a result with no downgrade reasons was capped anyway -- the guard is over-broad"
    )


def test_guard_is_not_vacuous() -> None:
    """POSITIVE CONTROL: the guard must be reachable from the real production shape.

    A guard that no input can trigger passes every test above for the wrong reason. This
    drives the unclamped branch and asserts the guard -- not an incidental clamp -- is what
    moved the number: `overall` lands exactly at the ceiling the guard imposes, not at one
    of the branch-specific values (0.94, 0.72, 0.55).
    """
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}},
        [{"path": "a.py"}],
        [],
        _consistency(confidence_downgraded=True),
    )
    assert result["overall"] < 1.0
    assert result["overall"] not in (0.94, 0.72, 0.55), (
        f"overall={result['overall']} matches a branch-specific clamp, so this case does not "
        f"prove the exit guard fired"
    )


def test_empty_or_whitespace_downgrade_reasons_do_not_lower_confidence() -> None:
    result = _confidence(
        {"edit_plan_seed": {"confidence": {"overall": 1.0}}},
        [{"path": "a.py"}],
        ["", "   "],
        _consistency(),
    )
    assert result["downgrade_reasons"] == []
    assert result["overall"] == 1.0
