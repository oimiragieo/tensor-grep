"""Risk-tier scoring helpers for diff-impact."""

from __future__ import annotations


def _calculate_risk_tier(
    blast_radius_score: float, affected_files_count: int, callers_count: int
) -> str:
    """Calculate risk tier based on blast radius score, affected file count, and callers count.

    Tiers:
    - critical: score >= 0.7 or affected_files >= 25 or callers >= 50
    - high: score >= 0.4 or affected_files >= 10 or callers >= 20
    - medium: score >= 0.15 or affected_files >= 3 or callers >= 5
    - low: otherwise
    """
    return _calculate_risk_tier_with_reasons(
        blast_radius_score, affected_files_count, callers_count
    )[0]


def _calculate_risk_tier_with_reasons(
    blast_radius_score: float, affected_files_count: int, callers_count: int
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if blast_radius_score >= 0.7:
        reasons.append("score_ge_0_7")
    if blast_radius_score >= 0.4:
        reasons.append("score_ge_0_4")
    if blast_radius_score >= 0.15:
        reasons.append("score_ge_0_15")
    if affected_files_count >= 25:
        reasons.append("affected_files_ge_25")
    if affected_files_count >= 10:
        reasons.append("affected_files_ge_10")
    if affected_files_count >= 3:
        reasons.append("affected_files_ge_3")
    if callers_count >= 50:
        reasons.append("callers_ge_50")
    if callers_count >= 20:
        reasons.append("callers_ge_20")
    if callers_count >= 5:
        reasons.append("callers_ge_5")

    if blast_radius_score >= 0.7 or affected_files_count >= 25 or callers_count >= 50:
        tier = "critical"
    elif blast_radius_score >= 0.4 or affected_files_count >= 10 or callers_count >= 20:
        tier = "high"
    elif blast_radius_score >= 0.15 or affected_files_count >= 3 or callers_count >= 5:
        tier = "medium"
    else:
        tier = "low"
    return tier, sorted(set(reasons))
