"""Offline tests for scripts/mcp_maintenance_probe.py (W2-c).

All arms use an in-memory fixture payload; no network call is made by this module. The one
network-calling function, ``fetch_pypi_mcp_json``, is exercised nowhere in this file -- it belongs
to the post-release sweep, not to pytest (see ``.claude/skills/tensor-grep-release-drift-check``).

Each classification branch (MAINTAINED / STALE / EXPIRED / CANNOT_MEASURE) gets its own
perturbation arm, observed RED against a mutated fixture before the fix, per the plan's stated
obligation. The RED observations are recorded in the PR body, not re-run here (a fixture cannot
assert its own past).
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from mcp_maintenance_probe import (
    VERDICT_CANNOT_MEASURE,
    VERDICT_EXPIRED,
    VERDICT_MAINTAINED,
    VERDICT_STALE,
    classify_mcp_maintenance,
)

FLOOR = "1.27.2"
REVALIDATE_BY = "2027-02-20"
TODAY = _dt.date(2026, 8, 20)


def _payload(releases: dict) -> dict:
    return {"releases": releases}


def _release(version: str, iso_date: str) -> dict:
    return {version: [{"upload_time_iso_8601": iso_date}]}


def _multi_release(*pairs: tuple[str, str]) -> dict:
    releases: dict = {}
    for version, iso_date in pairs:
        releases.update(_release(version, iso_date))
    return releases


# ---------------------------------------------------------------------------
# MAINTAINED
# ---------------------------------------------------------------------------


def test_maintained_when_latest_release_at_floor() -> None:
    """The real 2026-08-20 fixture: latest 1.x == the pyproject floor, well inside the window."""
    payload = _payload(
        _multi_release(
            ("1.27.1", "2026-05-08T16:50:10Z"),
            ("1.27.2", "2026-05-29T17:16:02Z"),
        )
    )
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_MAINTAINED
    assert verdict.latest_v1_version == "1.27.2"


def test_maintained_when_latest_release_newer_than_floor() -> None:
    payload = _payload(
        _multi_release(
            ("1.27.2", "2026-05-29T17:16:02Z"),
            ("1.29.0", "2026-07-28T13:41:40Z"),
        )
    )
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_MAINTAINED
    assert verdict.latest_v1_version == "1.29.0"


def test_maintained_ignores_2x_releases_when_checking_the_1x_line() -> None:
    """A 2.0.0 release existing (the real-world case) must not be misread as the 1.x head."""
    payload = _payload(
        _multi_release(
            ("1.27.2", "2026-05-29T17:16:02Z"),
            ("2.0.0", "2026-08-01T00:00:00Z"),
        )
    )
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_MAINTAINED
    assert verdict.latest_v1_version == "1.27.2"


# ---------------------------------------------------------------------------
# STALE
# ---------------------------------------------------------------------------


def test_stale_when_no_recent_1x_release_and_below_floor() -> None:
    """Floor points at a release that is itself far older than the maintenance window, and no
    fresher 1.x has landed -- reported as STALE, not failing, because revalidate_by has not
    elapsed."""
    payload = _payload(_release("1.20.0", "2025-10-01T00:00:00Z"))
    verdict = classify_mcp_maintenance(
        payload,
        floor_version="1.27.2",  # floor ahead of what upstream shows -- stale snapshot
        revalidate_by=REVALIDATE_BY,
        today=TODAY,
        maintenance_window_days=120,
    )
    assert verdict.verdict == VERDICT_STALE
    assert "not yet elapsed" in verdict.reason


def test_stale_perturbation_arm_flips_to_maintained_inside_window() -> None:
    """Perturbation control: move the same release's upload date inside the window -> MAINTAINED.
    Proves the STALE branch is reachable and its boundary is the window, not a hardcoded verdict.
    """
    payload = _payload(_release("1.20.0", "2026-07-01T00:00:00Z"))
    verdict = classify_mcp_maintenance(
        payload,
        floor_version="1.27.2",
        revalidate_by=REVALIDATE_BY,
        today=TODAY,
        maintenance_window_days=120,
    )
    assert verdict.verdict == VERDICT_MAINTAINED


# ---------------------------------------------------------------------------
# EXPIRED
# ---------------------------------------------------------------------------


def test_expired_when_revalidate_by_has_elapsed() -> None:
    """Perturbation arm (i) from the plan: set revalidate_by to a past date -> EXPIRED."""
    payload = _payload(_release("1.29.0", "2026-07-28T13:41:40Z"))
    verdict = classify_mcp_maintenance(
        payload,
        floor_version=FLOOR,
        revalidate_by="2025-01-01",  # past
        today=TODAY,
    )
    assert verdict.verdict == VERDICT_EXPIRED
    assert "elapsed" in verdict.reason


def test_expired_on_revalidate_by_boundary_day_itself() -> None:
    """today == revalidate_by is EXPIRED (>=, not >) -- the decision record's stated re-look date
    is not a grace day."""
    payload = _payload(_release("1.29.0", "2026-07-28T13:41:40Z"))
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by="2026-08-20", today=_dt.date(2026, 8, 20)
    )
    assert verdict.verdict == VERDICT_EXPIRED


def test_expired_when_no_1x_release_exists_at_all() -> None:
    """A maintenance-end signal: the payload parses but carries no 1.x release (e.g. the whole
    line was yanked)."""
    payload = _payload(_release("2.0.0", "2026-08-01T00:00:00Z"))
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_EXPIRED
    assert "maintenance-end" in verdict.reason


def test_expired_revalidate_by_dominates_even_with_fresh_release() -> None:
    """T6 wins over T1: even a brand-new 1.x release cannot rescue an elapsed revalidate_by."""
    payload = _payload(_release("1.30.0", "2026-08-19T00:00:00Z"))
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by="2026-01-01", today=TODAY
    )
    assert verdict.verdict == VERDICT_EXPIRED


# ---------------------------------------------------------------------------
# CANNOT_MEASURE
# ---------------------------------------------------------------------------


def test_cannot_measure_on_missing_payload() -> None:
    """Perturbation arm (ii) from the plan: an unreachable host means no payload -- CANNOT_MEASURE,
    never MAINTAINED."""
    verdict = classify_mcp_maintenance(
        None, floor_version=FLOOR, revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_CANNOT_MEASURE
    assert verdict.verdict != VERDICT_MAINTAINED


def test_cannot_measure_on_malformed_payload_missing_releases_key() -> None:
    verdict = classify_mcp_maintenance(
        {"info": {"version": "2.0.0"}},  # no 'releases' key
        floor_version=FLOOR,
        revalidate_by=REVALIDATE_BY,
        today=TODAY,
    )
    assert verdict.verdict == VERDICT_CANNOT_MEASURE


def test_cannot_measure_on_releases_wrong_type() -> None:
    verdict = classify_mcp_maintenance(
        {"releases": "not-a-dict"},
        floor_version=FLOOR,
        revalidate_by=REVALIDATE_BY,
        today=TODAY,
    )
    assert verdict.verdict == VERDICT_CANNOT_MEASURE


def test_cannot_measure_on_unparseable_revalidate_by() -> None:
    payload = _payload(_release("1.29.0", "2026-07-28T13:41:40Z"))
    verdict = classify_mcp_maintenance(
        payload, floor_version=FLOOR, revalidate_by="not-a-date", today=TODAY
    )
    assert verdict.verdict == VERDICT_CANNOT_MEASURE


def test_cannot_measure_on_unparseable_floor_version() -> None:
    payload = _payload(_release("1.29.0", "2026-07-28T13:41:40Z"))
    verdict = classify_mcp_maintenance(
        payload, floor_version="not-a-version", revalidate_by=REVALIDATE_BY, today=TODAY
    )
    assert verdict.verdict == VERDICT_CANNOT_MEASURE


# ---------------------------------------------------------------------------
# Revert / round-trip control
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("releases", "floor", "revalidate_by", "today", "expected"),
    [
        (
            _multi_release(("1.27.2", "2026-05-29T17:16:02Z")),
            "1.27.2",
            REVALIDATE_BY,
            TODAY,
            VERDICT_MAINTAINED,
        ),
        (
            _release("1.20.0", "2025-10-01T00:00:00Z"),
            "1.27.2",
            REVALIDATE_BY,
            TODAY,
            VERDICT_STALE,
        ),
        (
            _release("1.29.0", "2026-07-28T13:41:40Z"),
            FLOOR,
            "2025-01-01",
            TODAY,
            VERDICT_EXPIRED,
        ),
        (None, FLOOR, REVALIDATE_BY, TODAY, VERDICT_CANNOT_MEASURE),
    ],
)
def test_all_four_verdicts_are_reachable_from_realistic_inputs(
    releases: dict | None, floor: str, revalidate_by: str, today: _dt.date, expected: str
) -> None:
    """Revert control: re-running the un-perturbed classification for each labelled verdict
    returns the real verdict every time -- byte-identical behaviour, not a one-off assertion."""
    payload = None if releases is None else _payload(releases)
    verdict = classify_mcp_maintenance(
        payload, floor_version=floor, revalidate_by=revalidate_by, today=today
    )
    assert verdict.verdict == expected
