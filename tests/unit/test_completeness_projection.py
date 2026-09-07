"""Parity + mutation-control tests for AGT-07's typed completeness record.

Every row runs through BOTH the existing ``unified_incomplete_envelope`` projection and the new
``CompletenessEvidence`` record's round-trip (``project(payload).to_legacy_dict()``), asserting
they agree byte-for-byte -- proving the new typed layer is additive, not a silent behavior change.
"""

from __future__ import annotations

from tensor_grep.cli.incompleteness import unified_incomplete_envelope
from tensor_grep.core.completeness import (
    Cause,
    CompletenessEvidence,
    RetryKind,
    ScanState,
    from_legacy_envelope,
    project,
)

# Frozen truth table (docs/plans/2026-09-07-agentic-quality-simplification.md Task 09),
# expressed as raw payload fixtures a real producer would emit.
TRUTH_TABLE: list[tuple[str, dict[str, object]]] = [
    ("complete_empty_scan", {}),
    ("display_cap_only", {"result_incomplete": False}),
    (
        "file_scan_cap",
        {
            "scan_limit": {
                "possibly_truncated": True,
                "truncation_cause": "scan_limit",
                "budget_remediable": True,
            }
        },
    ),
    (
        "deadline",
        {"incomplete_reason": "deadline", "result_incomplete": True, "budget_remediable": True},
    ),
    ("unreadable_path", {"unreadable_paths": {"count": 2}}),
    (
        "mixed_roots_one_partial",
        {"partial": True, "results_by_root": {"a": {"incomplete": {"status": True}}}},
    ),
    (
        "unknown_legacy_evidence",
        {"result_incomplete": True, "incomplete_reason": "some_future_cause_not_yet_classified"},
    ),
]


def test_truth_table_rows_round_trip_byte_for_byte() -> None:
    for name, payload in TRUTH_TABLE:
        legacy = unified_incomplete_envelope(payload)
        via_record = project(payload).to_legacy_dict()
        assert via_record == legacy, f"row {name!r} diverged: {via_record!r} != {legacy!r}"


def test_complete_empty_scan_is_complete_with_no_cause() -> None:
    evidence = project({})
    assert evidence.scan_state is ScanState.COMPLETE
    assert evidence.cause is Cause.NONE
    assert evidence.retry_kind is RetryKind.NOT_APPLICABLE


def test_display_cap_only_stays_complete_distinct_from_scan_cap() -> None:
    # unified_incomplete_envelope carries no output-cap signal today (display capping is a
    # separate, downstream concern from the incompleteness envelope) -- so output_capped is
    # constructed directly here rather than round-tripped through project(), which this slice
    # deliberately does not change. The point under test is that COMPLETE+output_capped and
    # INCOMPLETE+scan_limit stay distinct states, never collapsed into one boolean.
    display_cap = CompletenessEvidence(
        scan_state=ScanState.COMPLETE,
        cause=Cause.NONE,
        retry_kind=RetryKind.NOT_APPLICABLE,
        output_capped=True,
    )
    scan_cap = project(
        {
            "scan_limit": {
                "possibly_truncated": True,
                "truncation_cause": "scan_limit",
                "budget_remediable": True,
            }
        }
    )
    assert display_cap.scan_state is ScanState.COMPLETE
    assert display_cap.output_capped is True
    assert scan_cap.scan_state is ScanState.INCOMPLETE
    assert scan_cap.cause is Cause.SCAN_LIMIT


def test_unreadable_path_is_incomplete_and_not_budget_remediable() -> None:
    evidence = project({"unreadable_paths": {"count": 1}})
    assert evidence.scan_state is ScanState.INCOMPLETE
    assert evidence.cause is Cause.UNREADABLE_PATH
    assert evidence.retry_kind is RetryKind.NOT_REMEDIABLE


def test_mixed_roots_one_partial_cannot_claim_complete() -> None:
    evidence = project({"results_by_root": {"a": {"incomplete": {"status": True}}}})
    assert evidence.scan_state is ScanState.INCOMPLETE
    assert evidence.cause is Cause.NESTED_INCOMPLETE


def test_unknown_legacy_evidence_preserves_unknown_never_manufactures_complete() -> None:
    evidence = project(
        {"result_incomplete": True, "incomplete_reason": "brand_new_unclassified_cause"}
    )
    assert evidence.scan_state is ScanState.INCOMPLETE
    assert evidence.cause is Cause.UNKNOWN


def test_mutation_control_forcing_unreadable_path_to_budget_remediable_fails() -> None:
    """Mutation control per Task 09: force an unreadable-path cause to budget-remediable --
    the test must fail. Asserted here by constructing the mutated record directly and checking
    it disagrees with the real projection (proving the real projection does NOT produce this
    shape on its own)."""
    real = project({"unreadable_paths": {"count": 1}})
    mutated = CompletenessEvidence(
        scan_state=ScanState.INCOMPLETE,
        cause=Cause.UNREADABLE_PATH,
        retry_kind=RetryKind.BUDGET_REMEDIABLE,  # wrong: unreadable_path is never remediable
    )
    assert real.retry_kind is RetryKind.NOT_REMEDIABLE
    assert real != mutated


def test_mutation_control_forcing_nested_partial_to_complete_fails() -> None:
    """Force a nested partial result to complete -- the test must fail."""
    real = project({"results_by_root": {"a": {"incomplete": {"status": True}}}})
    mutated = CompletenessEvidence(
        scan_state=ScanState.COMPLETE,  # wrong: a nested-incomplete root cannot be COMPLETE
        cause=Cause.NONE,
        retry_kind=RetryKind.NOT_APPLICABLE,
    )
    assert real.scan_state is ScanState.INCOMPLETE
    assert real != mutated


def test_incomplete_with_no_cause_round_trips_to_none_not_unknown() -> None:
    """Codex Luna audit finding: status=True with no cause set anywhere in the payload used to
    collapse to Cause.UNKNOWN, which round-trips to the string "unknown" -- fabricating a cause
    the legacy envelope never actually reported. It must round-trip to None."""
    legacy = unified_incomplete_envelope({"result_incomplete": True})
    assert legacy == {"status": True, "cause": None, "budget_remediable": False}
    assert project({"result_incomplete": True}).to_legacy_dict() == legacy


def test_from_legacy_envelope_is_pure_inverse_of_to_legacy_dict() -> None:
    for name, payload in TRUTH_TABLE:
        legacy = unified_incomplete_envelope(payload)
        assert from_legacy_envelope(legacy).to_legacy_dict() == legacy, name
