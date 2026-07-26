"""Task #276 slice C0 -- the canonical disclosed-incompleteness vocabulary.

The CONTROL arms are the point of this file. `disclosed_incomplete` gates whether an exit-2 run
is tolerated by three separate entry points, and exit 2 is overloaded: it is what an honest
incomplete scan returns AND what a regex syntax error returns. If this ever degrades into a
blanket accept, every one of those consumers silently becomes a check that cannot fail -- so a
test that only exercises the positive arm would be decoration.
"""

from __future__ import annotations

from tensor_grep.cli.incompleteness import (
    INCOMPLETENESS_MARKERS,
    disclosed_incomplete,
)


def test_json_route_keys_are_a_disclosure() -> None:
    # json_fmt.py:127 / :140 emit these ONLY when the result is genuinely incomplete.
    assert disclosed_incomplete('{"matches": [], "result_incomplete": true}', "")
    assert disclosed_incomplete('{"incomplete_reason_class": "unreadable_path"}', "")


def test_plain_text_route_sentinel_is_a_disclosure() -> None:
    # ripgrep_backend.py:143 / :324 / :443 -- the phrase the non-JSON route really emits.
    # Without this arm the guard is unreachable for any caller that never passes --json, which
    # is exactly what an independent audit caught on the first cut of this slice.
    assert disclosed_incomplete("", "tg: rg exited 2, keeping partial results: unreadable path")


def test_an_undisclosed_failure_is_not_tolerated() -> None:
    # CONTROL. These are REAL errors that also exit 2. Each must stay rejected.
    assert not disclosed_incomplete("", "regex parse error: unclosed group")
    assert not disclosed_incomplete("", "tg: ripgrep is not resolvable on PATH")
    assert not disclosed_incomplete("", "")
    assert not disclosed_incomplete(None, None)


def test_naming_a_path_alone_is_not_a_disclosure() -> None:
    # The subtlest control: a permission-denied line names the obstacle but claims nothing about
    # completeness, so it is indistinguishable from a hard failure and must NOT be tolerated.
    assert not disclosed_incomplete("", "tg: /srv/locked: Permission denied")


def test_marker_set_is_the_documented_three() -> None:
    # Pins the vocabulary itself: silently dropping a marker would re-break a whole route, and
    # silently adding one would widen an allow-list that three consumers depend on.
    assert INCOMPLETENESS_MARKERS == (
        "result_incomplete",
        "incomplete_reason_class",
        "keeping partial results",
    )
