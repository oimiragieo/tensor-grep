"""Both `docs-coverage` renderers must LEAD with an incomplete-scan disclosure.

Two defects, one shared cause -- each renderer decided for itself what counts as truncation:

* ``--stale`` read NEITHER ``scan_limit`` NOR ``partial``. A truncated run printed
  "No stale references found (every cited path still exists)" -- an ABSENCE claim over a doc set
  that was never fully read. ``--json`` carried the truncation the whole time; only the default
  output dropped it.
* ``--check`` did disclose, but BELOW the counts it qualifies. A reader who has seen
  ``coverage=87%`` has already formed the answer by the time a trailing ``[!]`` lands (task #329).

Neither read ``partial``, so a ``--deadline`` cutoff was invisible on both.

The fix is one shared ``docs_scan_incompleteness_lines`` helper, so the two cannot drift into
different vocabulary or different advice -- and so a THIRD renderer added later inherits it.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.docs_coverage import (
    docs_scan_incompleteness_lines,
    render_docs_coverage_text,
    render_docs_stale_text,
)

_BANNER = "[!]"


def _scan_limit(**over: Any) -> dict[str, Any]:
    base = {"max_files": 512, "possibly_truncated": False, "truncation_cause": None}
    base.update(over)
    return base


def _coverage_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": "/repo",
        "totals": {
            "source_files": 10,
            "covered": 9,
            "uncovered": 1,
            "coverage_pct": 90.0,
            "doc_files": 4,
        },
        "uncovered_files": ["src/a.py"],
        "scan_limit": _scan_limit(),
    }
    payload.update(extra)
    return payload


def _stale_payload(**extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": "/repo",
        "totals": {"doc_files": 4, "references_checked": 20, "stale": 0},
        "stale_references": [],
        "scan_limit": _scan_limit(),
    }
    payload.update(extra)
    return payload


# ------------------------------------------------------------------ the ABSENT case (--stale)


def test_stale_report_discloses_a_truncated_scan_at_all() -> None:
    out = render_docs_stale_text(
        _stale_payload(
            scan_limit=_scan_limit(possibly_truncated=True, truncation_cause="project-files")
        )
    )
    # Premise: the report really rendered its "nothing found" claim, which is what makes the
    # missing disclosure dangerous rather than merely untidy.
    assert "No stale references found" in out
    assert _BANNER in out, "a truncated --stale run claims absence with no disclosure at all"


def test_stale_report_disclosure_leads_the_absence_claim() -> None:
    out = render_docs_stale_text(
        _stale_payload(
            scan_limit=_scan_limit(possibly_truncated=True, truncation_cause="project-files")
        )
    )
    assert out.splitlines()[0].startswith(_BANNER)
    assert out.index(_BANNER) < out.index("No stale references found")


def test_a_complete_stale_report_is_unchanged() -> None:
    # CONTROL ARM: without it, a renderer that always printed a banner would pass every test above.
    out = render_docs_stale_text(_stale_payload())
    assert "Stale doc references for" in out  # premise: it rendered
    assert _BANNER not in out


# -------------------------------------------------------------- the POSITION case (--check)


def test_coverage_report_disclosure_leads_the_counts() -> None:
    out = render_docs_coverage_text(
        _coverage_payload(
            scan_limit=_scan_limit(possibly_truncated=True, truncation_cause="project-files")
        )
    )
    assert "coverage=90.0%" in out  # premise: the counts block rendered
    assert out.splitlines()[0].startswith(_BANNER)
    assert out.index(_BANNER) < out.index("coverage=")


def test_a_complete_coverage_report_is_unchanged() -> None:
    out = render_docs_coverage_text(_coverage_payload())
    assert out.splitlines()[0].startswith("Docs coverage for")
    assert _BANNER not in out


# --------------------------------------------------------------------- causes and their knobs


def test_an_unreadable_path_is_not_answered_with_budget_advice() -> None:
    # Task #284's cause-awareness, preserved through the extraction. No --max-files value makes a
    # denied path readable, so naming the cap would be advice that cannot work.
    lines = docs_scan_incompleteness_lines(
        _coverage_payload(
            scan_limit=_scan_limit(possibly_truncated=True, truncation_cause="unreadable-path")
        )
    )
    assert len(lines) == 1
    assert "will NOT help" in lines[0]
    assert "truncated at max_files" not in lines[0]


def test_a_deadline_cutoff_is_disclosed_and_names_the_right_knob() -> None:
    # THIRD cause, read by neither renderer before: the builder sets `partial` WITHOUT
    # `possibly_truncated`, so every branch above it missed a --deadline cutoff entirely.
    lines = docs_scan_incompleteness_lines(_coverage_payload(partial=True))
    assert len(lines) == 1
    assert "--deadline" in lines[0]
    assert "--max-files will NOT help" in lines[0]


def test_a_file_cap_wins_over_the_deadline_branch() -> None:
    # A payload carrying both must name the cap, which is the actionable knob.
    lines = docs_scan_incompleteness_lines(
        _coverage_payload(
            partial=True,
            scan_limit=_scan_limit(possibly_truncated=True, truncation_cause="project-files"),
        )
    )
    assert len(lines) == 1
    assert "truncated at max_files" in lines[0]


def test_a_complete_payload_yields_no_lines() -> None:
    assert docs_scan_incompleteness_lines(_coverage_payload()) == []
