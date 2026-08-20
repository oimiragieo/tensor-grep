"""Recurring re-derivation probe for MCP SDK 1.x maintenance status.

Wired into the ``tensor-grep-release-drift-check`` post-release sweep as trigger T1
(``upstream_maintenance_end``) and, by the calendar, T6 (``time_bounded_revalidation``) from
``docs/design/2026-08-20-mcp-2-0-exposure-decision.md``. Replaces the r1 SDK-constant tripwire,
which the council rejected because it read a constant out of an *installed* 1.x SDK and would
essentially never observe the ecosystem moving past it (see plan ``W2.5``).

Design: classification is a **pure function** (``classify_mcp_maintenance``) over an
already-fetched PyPI payload, so it is testable offline with fixtures. The network call lives in
``fetch_pypi_mcp_json`` and is never exercised by pytest -- the sweep script, not the test suite,
performs the live fetch.

Four labelled verdicts, and never a bare zero:

- ``MAINTAINED``   -- the maintained 1.x line is at least as recent as tg's pinned floor, or a
                       1.x release landed within the maintenance window.
- ``STALE``        -- no new 1.x release within the maintenance window, but
                       ``revalidate_by`` has not yet elapsed. Reported, not failing.
- ``EXPIRED``       -- ``revalidate_by`` has elapsed, or no 1.x release exists at all (treated as
                       an upstream maintenance-end signal).
- ``CANNOT_MEASURE`` -- the payload could not be fetched or parsed. This is a distinct, loud
                       outcome and must never be conflated with MAINTAINED.

Honest limitation (stated once, matching the decision record): this watches T1 and T6 only. It
does not observe T2 (client incompatibility) or T3 (Task 2C clearing) -- those remain
human-discovered.
"""

from __future__ import annotations

import datetime as _dt
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

PYPI_MCP_URL = "https://pypi.org/pypi/mcp/json"

# Roughly upstream's observed 1.x release cadence (see the decision record's re-derived
# 1.27.1 -> 1.29.0 timeline, ~3-8 weeks apart); wide enough to avoid false STALE on a normal gap.
DEFAULT_MAINTENANCE_WINDOW_DAYS = 120

VERDICT_MAINTAINED = "MAINTAINED"
VERDICT_STALE = "STALE"
VERDICT_EXPIRED = "EXPIRED"
VERDICT_CANNOT_MEASURE = "CANNOT_MEASURE"

ALL_VERDICTS = (VERDICT_MAINTAINED, VERDICT_STALE, VERDICT_EXPIRED, VERDICT_CANNOT_MEASURE)


@dataclass(frozen=True)
class MaintenanceVerdict:
    verdict: str
    reason: str
    latest_v1_version: str | None = None
    latest_v1_uploaded: str | None = None  # ISO 8601 date string


def _latest_v1_release(releases: dict[str, object]) -> tuple[Version, _dt.date] | None:
    """Return (version, upload_date) of the newest non-prerelease 1.x release, or None."""
    best: tuple[Version, _dt.date] | None = None
    for raw_version, files in releases.items():
        if not isinstance(files, list):
            continue
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.major != 1 or parsed.is_prerelease or parsed.is_devrelease:
            continue
        upload_times_raw = [
            f.get("upload_time_iso_8601") or f.get("upload_time")
            for f in files
            if isinstance(f, dict)
        ]
        upload_times: list[str] = [t for t in upload_times_raw if isinstance(t, str)]
        if not upload_times:
            continue
        try:
            uploaded = _dt.datetime.fromisoformat(min(upload_times).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, uploaded)
    return best


def classify_mcp_maintenance(
    payload: dict[str, object] | None,
    *,
    floor_version: str,
    revalidate_by: str,
    today: _dt.date,
    maintenance_window_days: int = DEFAULT_MAINTENANCE_WINDOW_DAYS,
) -> MaintenanceVerdict:
    """Classify T1/T6 maintenance status from an already-fetched PyPI payload.

    ``payload`` is the parsed JSON body of ``https://pypi.org/pypi/mcp/json``. Pass ``None`` or a
    malformed dict to simulate a fetch failure -- that path returns CANNOT_MEASURE, never
    MAINTAINED.
    """
    try:
        revalidate_date = _dt.date.fromisoformat(revalidate_by)
    except (TypeError, ValueError):
        return MaintenanceVerdict(
            VERDICT_CANNOT_MEASURE, reason=f"unparseable revalidate_by: {revalidate_by!r}"
        )

    try:
        floor = Version(floor_version)
    except InvalidVersion:
        return MaintenanceVerdict(
            VERDICT_CANNOT_MEASURE, reason=f"unparseable floor_version: {floor_version!r}"
        )

    if payload is None or not isinstance(payload, dict):
        return MaintenanceVerdict(VERDICT_CANNOT_MEASURE, reason="no payload (fetch failed)")

    releases = payload.get("releases")
    if not isinstance(releases, dict):
        return MaintenanceVerdict(
            VERDICT_CANNOT_MEASURE, reason="malformed payload: missing/invalid 'releases'"
        )

    # T6 dominates: an elapsed revalidate_by is EXPIRED regardless of what T1 shows -- the
    # decision itself said "come back and re-look" by this date.
    if today >= revalidate_date:
        latest = _latest_v1_release(releases)
        return MaintenanceVerdict(
            VERDICT_EXPIRED,
            reason=f"revalidate_by {revalidate_by} has elapsed (today={today.isoformat()})",
            latest_v1_version=str(latest[0]) if latest else None,
            latest_v1_uploaded=latest[1].isoformat() if latest else None,
        )

    latest = _latest_v1_release(releases)
    if latest is None:
        # No 1.x release found at all in an otherwise-parseable payload: treat as a
        # maintenance-end signal (T1), independent of the revalidate_by calendar.
        return MaintenanceVerdict(
            VERDICT_EXPIRED, reason="no 1.x release found in payload -- treat as maintenance-end"
        )

    latest_version, latest_uploaded = latest
    age_days = (today - latest_uploaded).days

    if latest_version >= floor:
        return MaintenanceVerdict(
            VERDICT_MAINTAINED,
            reason=f"latest 1.x {latest_version} >= floor {floor}",
            latest_v1_version=str(latest_version),
            latest_v1_uploaded=latest_uploaded.isoformat(),
        )

    if age_days <= maintenance_window_days:
        return MaintenanceVerdict(
            VERDICT_MAINTAINED,
            reason=(
                f"latest 1.x {latest_version} below floor {floor} but uploaded {age_days}d ago, "
                f"within the {maintenance_window_days}d window"
            ),
            latest_v1_version=str(latest_version),
            latest_v1_uploaded=latest_uploaded.isoformat(),
        )

    return MaintenanceVerdict(
        VERDICT_STALE,
        reason=(
            f"no new 1.x release within {maintenance_window_days}d "
            f"(latest {latest_version} uploaded {age_days}d ago) and revalidate_by not yet elapsed"
        ),
        latest_v1_version=str(latest_version),
        latest_v1_uploaded=latest_uploaded.isoformat(),
    )


def fetch_pypi_mcp_json(timeout: float = 10.0) -> dict[str, object] | None:
    """Live network fetch -- deliberately NOT exercised by pytest. Returns None on any failure."""
    try:
        with urllib.request.urlopen(PYPI_MCP_URL, timeout=timeout) as resp:
            result: dict[str, object] = json.loads(resp.read())
            return result
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def probe(
    *,
    floor_version: str,
    revalidate_by: str,
    today: _dt.date | None = None,
    maintenance_window_days: int = DEFAULT_MAINTENANCE_WINDOW_DAYS,
) -> MaintenanceVerdict:
    """End-to-end entry point for the post-release sweep: fetch, then classify."""
    payload = fetch_pypi_mcp_json()
    return classify_mcp_maintenance(
        payload,
        floor_version=floor_version,
        revalidate_by=revalidate_by,
        today=today or _dt.date.today(),
        maintenance_window_days=maintenance_window_days,
    )


if __name__ == "__main__":
    import re
    import sys
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'"mcp>=([\d.]+),<2"', pyproject.read_text(encoding="utf-8"))
    if not match:
        print("CANNOT_MEASURE: could not find mcp>= floor in pyproject.toml", file=sys.stderr)
        raise SystemExit(1)
    verdict = probe(floor_version=match.group(1), revalidate_by="2027-02-20")
    print(f"{verdict.verdict}: {verdict.reason}")
    raise SystemExit(0 if verdict.verdict != VERDICT_CANNOT_MEASURE else 1)
