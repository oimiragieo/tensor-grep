"""Regression tests for M6: tg classify must emit distinct debug/trace labels.

Before the fix, lines containing a DEBUG or TRACE level token were collapsed
into the 'info' label by the heuristic fallback.  After the fix they get their
own labels so an agent can separate debug/trace noise from real info/warn/error
signal.
"""

from __future__ import annotations

import pytest

from tensor_grep.sidecar import _heuristic_classify_lines

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_single(line: str) -> dict:
    results = _heuristic_classify_lines([line])
    assert len(results) == 1
    return results[0]


# ---------------------------------------------------------------------------
# M6 — debug and trace labels must be distinct from info
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "2024-01-01 DEBUG entering connection pool loop",
        "[DEBUG] scheduler tick",
        "debug: attempt 3",
        "DEBUG  some message here",
    ],
)
def test_debug_line_gets_debug_label(line: str) -> None:
    result = _classify_single(line)
    assert result["label"] == "debug", (
        f"Expected 'debug', got {result['label']!r} for line: {line!r}"
    )
    assert result["confidence"] == pytest.approx(0.80)


@pytest.mark.parametrize(
    "line",
    [
        "2024-01-01 TRACE processing packet id=42",
        "[TRACE] enter function foo",
        "trace: raw bytes 0xdeadbeef",
        "TRACE  detail level output",
    ],
)
def test_trace_line_gets_trace_label(line: str) -> None:
    result = _classify_single(line)
    assert result["label"] == "trace", (
        f"Expected 'trace', got {result['label']!r} for line: {line!r}"
    )
    assert result["confidence"] == pytest.approx(0.80)


def test_info_line_still_gets_info_label() -> None:
    result = _classify_single("2024-01-01 INFO application started")
    assert result["label"] == "info"
    assert result["confidence"] == pytest.approx(0.80)


def test_error_line_label_unchanged() -> None:
    result = _classify_single("2024-01-01 ERROR failed to open socket: connection refused")
    assert result["label"] == "error"
    assert result["confidence"] == pytest.approx(0.95)


def test_warn_line_label_unchanged() -> None:
    result = _classify_single("2024-01-01 WARNING disk usage at 90%")
    assert result["label"] == "warn"
    assert result["confidence"] == pytest.approx(0.85)


def test_all_five_labels_in_one_batch() -> None:
    """A mixed log produces exactly the five expected labels in order."""
    lines = [
        "INFO starting application",
        "DEBUG entering loop",
        "TRACE packet id=42",
        "ERROR connection refused",
        "WARNING high disk usage",
    ]
    results = _heuristic_classify_lines(lines)
    labels = [r["label"] for r in results]
    assert labels == ["info", "debug", "trace", "error", "warn"]


def test_debug_not_collapsed_to_info() -> None:
    """Explicit regression: DEBUG must NOT produce label 'info'."""
    result = _classify_single("DEBUG this was previously classified as info")
    assert result["label"] != "info"


def test_trace_not_collapsed_to_info() -> None:
    """Explicit regression: TRACE must NOT produce label 'info'."""
    result = _classify_single("TRACE this was previously classified as info")
    assert result["label"] != "info"


# ---------------------------------------------------------------------------
# Priority ordering: error/warn still win over trace/debug keywords
# ---------------------------------------------------------------------------


def test_error_keyword_beats_debug() -> None:
    """A line with both ERROR and debug tokens classifies as error."""
    result = _classify_single("ERROR debug session failed")
    assert result["label"] == "error"


def test_warn_keyword_beats_trace() -> None:
    """A line with both warn and trace tokens classifies as warn."""
    result = _classify_single("WARNING trace buffer degraded")
    assert result["label"] == "warn"


def test_trace_beats_debug_keyword_order() -> None:
    """trace check comes before debug in the chain; a line with 'trace' → 'trace'."""
    result = _classify_single("TRACE debug-level detail")
    assert result["label"] == "trace"
