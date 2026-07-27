"""`not_found` must not assert absence over a scan that never finished (task 327).

Found by an external codex dogfood of the published v1.98.27 wheel:

    tg refs . collect_walked_files --deadline 0.1 --json
    -> {"deadline_limit": {"deadline_exceeded": true, "files_scanned": 0, "files_total": 209},
        "partial": true, "references": [], "not_found": true}   EXIT=2

Zero files were read, and the payload still claimed the symbol was not found. `not_found` answers
"we looked and it is absent"; a scan that was cut off never looked, so it cannot support the
claim. This is the same confident-false-zero class the #276 campaign is about -- surviving in a
field the completeness machinery never covered. The docstring on the stamp site says in as many
words that its purpose is preventing "the dangerous 'confident false zero'".

Exit 2 already fired, so an agent obeying the documented exit-code contract was safe; an agent
reading the field was not. The fix reuses `_scan_incomplete` -- the gate the repo already calls
the place where "the scan-vs-output-cap contract is defined exactly once" -- rather than adding a
second notion of incompleteness that could drift from it.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.main import _symbol_not_found_claim


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"references": []}
    payload.update(overrides)
    return payload


def test_a_complete_scan_finding_nothing_still_reports_not_found() -> None:
    """CONTROL ARM. Runs first: a guard that suppressed `not_found` unconditionally would pass
    every truncation assertion below while destroying the genuine-absence signal (exit 1), so
    this must hold before any of them count as evidence."""
    assert _symbol_not_found_claim(_payload(), "references") is True


def test_a_deadline_truncated_scan_does_not_claim_not_found() -> None:
    """The exact shape codex reported: nothing read, nothing found, absence asserted."""
    payload = _payload(
        partial=True,
        deadline_limit={"deadline_exceeded": True, "files_scanned": 0, "files_total": 209},
    )

    assert _symbol_not_found_claim(payload, "references") is False


def test_a_scan_cap_truncated_scan_does_not_claim_not_found() -> None:
    payload = _payload(
        scan_limit={"max_repo_files": 10, "scanned_files": 10, "possibly_truncated": True}
    )

    assert _symbol_not_found_claim(payload, "references") is False


def test_a_caller_scan_ceiling_truncated_scan_does_not_claim_not_found() -> None:
    payload = _payload(
        callers=[],
        caller_scan_limit={"ceiling": 512, "files_total": 2000, "possibly_truncated": True},
    )

    assert _symbol_not_found_claim(payload, "callers") is False


def test_an_output_cap_alone_still_reports_not_found() -> None:
    """BOUNDARY, pinned deliberately. An OUTPUT cap is a COMPLETE analysis capped for display
    (docs/CONTRACTS.md: it "stays exit 0; only a SCAN truncation exits 2"), and `_scan_incomplete`
    excludes it on purpose. So the scan DID finish looking and `not_found` remains meaningful.

    Widening the guard to output caps here would silently flip output-cap-only invocations and
    break the output-cap-stays-exit-0 pins. The separate discoverability problem with output-cap
    disclosure is tracked as task 328, and is a docs fix, not a change to this predicate."""
    payload = _payload(output_limit={"possibly_truncated": True})

    assert _symbol_not_found_claim(payload, "references") is True


def test_a_resolver_no_match_on_a_complete_scan_still_reports_not_found() -> None:
    payload = _payload(no_match=True)

    assert _symbol_not_found_claim(payload, "references") is True


def test_a_truncated_scan_that_DID_find_results_reports_not_found_false() -> None:
    """Both inputs point the same way here; included so the truthy-results path is covered and
    the guard cannot be read as "truncation forces False regardless of results"."""
    payload = _payload(references=[{"file": "a.rs", "line": 1}], partial=True)

    assert _symbol_not_found_claim(payload, "references") is False
