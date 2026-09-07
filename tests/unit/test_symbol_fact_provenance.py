"""P14 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 13) checkbox: 'Remove
first-to-market assertions unless a documented competitor census substantiates the exact
narrow claim.'

docs/BACKLOG.md's own research-corrections note (line 28) already concedes this: "P14's
entire-market/first-to-market claim is unsupported." The P14 entry's Objective bullet
nonetheless asserted "Be first-to-market ... across the ENTIRE code-intelligence MCP
market" -- an unhedged superiority claim the repo's own audit says it cannot back. This
test pins that the phrase does not silently reappear once corrected, mirroring the
docs-comparing-itself pattern in test_backlog_self_consistency.py rather than trusting a
one-time edit to hold.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKLOG_PATH = REPO_ROOT / "docs" / "BACKLOG.md"

_BANNED_UNHEDGED_PHRASES = (
    "be first-to-market",
    "first to market on",
)


def _p14_entry_text() -> str:
    text = BACKLOG_PATH.read_text(encoding="utf-8")
    start = text.index("P14 — Fact-Level Confidence/Freshness/Provenance Envelope")
    # The next top-level backlog item ("- **[ ] P15") bounds the entry; fall back to the
    # first "---" doc-section break if P15 is ever renamed/reordered.
    end_markers = ["\n- **[ ] P15", "\n---"]
    end = min((text.index(m, start) for m in end_markers if m in text[start:]), default=len(text))
    return text[start:end]


def test_p14_entry_exists() -> None:
    assert "P14 — Fact-Level Confidence/Freshness/Provenance Envelope" in BACKLOG_PATH.read_text(
        encoding="utf-8"
    )


def test_p14_entry_has_no_unhedged_first_to_market_claim() -> None:
    entry = _p14_entry_text().lower()
    for phrase in _BANNED_UNHEDGED_PHRASES:
        assert phrase not in entry, (
            f"docs/BACKLOG.md P14 entry re-asserts an unhedged claim ({phrase!r}) that the "
            "backlog's own research-corrections note (line 28) already says is unsupported. "
            "Task 13's last checkbox requires this be removed or backed by a documented "
            "competitor census -- see docs/design/2026-09-07-p14-provenance-census.md."
        )


def test_p14_entry_scope_is_still_intact() -> None:
    # The fix must hedge the CLAIM, not delete the actual scope/acceptance criteria.
    entry = _p14_entry_text()
    assert "provenance" in entry.lower()
    assert "defs" in entry and "refs" in entry and "callers" in entry
