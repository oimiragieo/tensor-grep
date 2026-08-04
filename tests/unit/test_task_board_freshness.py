"""The TASK_BOARD reconcile stamp may lag the shipped version, but only so far.

WHY THIS EXISTS, and why it is a TOLERANCE rather than an equality check.

`docs/TASK_BOARD.md` has gone stale four times in the same way. Its own header records
three of them and, on 2026-08-01, a campaign found a fourth: **nine** items listed OPEN
were already fixed, refuted, or deliberate-by-design.

That header also records two candidate CI gates and rejects both, correctly:

  1. "Assert the IN FLIGHT table matches `gh pr list`." Needs network and a token inside
     the test run. A rate-limited or offline run reds the build for a reason unrelated to
     the repo, which teaches everyone to reach for `--no-verify` and discredits every other
     gate here.
  2. "Assert the `post-vX.Y.Z` stamp equals `pyproject.toml`'s version." Zero network and
     perfectly deterministic -- and it would fire after EVERY release, several times a day,
     forcing a board edit into every unrelated PR. An over-eager rule is worse than no rule.

Both rejections stand. **What neither considered is a TOLERANCE.** Rejection 2 is aimed at
the strict form; it does not argue against allowing the stamp to lag by a few releases and
failing only on genuine neglect. That is the gap this test fills:

  * ZERO network -- both numbers are read from files in the repo.
  * Deterministic -- no clock, no ordering, no environment.
  * NOT over-eager -- a normal 1-2 release lag passes untouched. It fires only when the
    board has been ignored across many releases.

Sizing `_MAX_RELEASES_BEHIND` from the RECORDED history rather than taste: the board once
read `post-v1.101.9` while the world had "moved 13 releases on", and later sat eight
releases behind. A threshold of 5 catches both of those with room to spare while never
firing on the ordinary cadence of a release or two between reconciles.

WHAT THIS TEST DELIBERATELY DOES NOT DO -- stated so nobody mistakes a green run for a
reconciled board. It proves the stamp is RECENT. It cannot prove the CONTENT is correct: an
item can say OPEN about work that shipped months ago while the stamp is perfectly current.
Only 3 of the board's 24 open items cite a file or symbol at all, so there is no anchor a
citation-style checker could hang on for the other 21. Content-level staleness is not
mechanically detectable here, and the fix for it is the ROUTINE the board's header already
names -- reconcile inside the merge step -- not a test. See
`docs/audits/2026-08-01-task-board-staleness.md`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOARD_PATH = _REPO_ROOT / "docs" / "TASK_BOARD.md"
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
_CHANGELOG_PATH = _REPO_ROOT / "CHANGELOG.md"

# Sized from the board's own recorded staleness (13 releases once, 8 another time), not taste.
_MAX_RELEASES_BEHIND = 5

_STAMP_RE = re.compile(r"post-\*\*v(?P<version>\d+\.\d+\.\d+)\*\*")


def _parse_version(raw: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in raw.split("."))
    return major, minor, patch


def _board_stamp_version() -> tuple[int, int, int]:
    text = _BOARD_PATH.read_text(encoding="utf-8")
    match = _STAMP_RE.search(text)
    assert match is not None, (
        f"{_BOARD_PATH.name} has no `post-**vX.Y.Z**` reconcile stamp. The stamp is the only "
        "thing this gate can read; if the format changed, update _STAMP_RE here in the same "
        "commit rather than deleting the gate."
    )
    return _parse_version(match.group("version"))


def _shipped_version() -> tuple[int, int, int]:
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    return _parse_version(data["project"]["version"])


_CHANGELOG_HEADING_RE = re.compile(r"^## v(?P<version>\d+\.\d+\.\d+)", re.MULTILINE)

# The changelog is long; a parse that silently returns a handful of entries would make every
# distance below meaninglessly small. Sized well under the real count (710 on 2026-08-04) so
# it fails on a broken parse, not on ordinary growth.
_MIN_EXPECTED_RELEASES = 100


class UnknownRelease(LookupError):
    """A version that is not in CHANGELOG.md -- a typo or a hand-edited stamp.

    Deliberately NOT folded into "very stale". An unparseable stamp is a COULD-NOT-MEASURE,
    and collapsing it into a measured verdict is how a broken instrument reports a number.
    """


def _release_order() -> dict[str, int]:
    """Map each released version to its ordinal position, newest first.

    Read from CHANGELOG.md because semantic-release rewrites it in the SAME commit as
    pyproject.toml's version (verified on the v1.103.0 release commit), so the two can never
    disagree about what shipped. Still zero-network, zero-clock, zero-environment -- the
    three properties this module's header sells.
    """
    text = _CHANGELOG_PATH.read_text(encoding="utf-8")
    order = _CHANGELOG_HEADING_RE.findall(text)
    assert len(order) >= _MIN_EXPECTED_RELEASES, (
        f"CHANGELOG.md parsed to only {len(order)} release headings "
        f"(expected >= {_MIN_EXPECTED_RELEASES}). The heading format changed or the file "
        "moved -- every staleness distance computed from this would be wrong and SMALL, "
        "i.e. falsely green."
    )
    return {version: index for index, version in enumerate(order)}


def _releases_behind(stamp: tuple[int, int, int], shipped: tuple[int, int, int]) -> int:
    """How many releases separate the stamp from the shipped version -- a TRUE distance.

    This used to subtract patch numbers and return ``_MAX_RELEASES_BEHIND + 1`` whenever the
    major.minor lines differed, on the reasoning that patch numbers reset so their difference
    "stops being a distance". The reasoning was right about SUBTRACTION and wrong about the
    underlying quantity: releases are totally ordered, and this repo writes that order to disk
    on every release.

    That sentinel caused a real outage on 2026-08-04. v1.103.0 was a MINOR bump (a `feat:`
    title), so every board stamp on the v1.102 line became unboundedly stale the instant it
    published -- including one that was ONE release behind. PR #928's 48 checks were green
    against a base at v1.102.8, and the identical tree reddened main the moment it merged
    (run 30952799876). #930 then showed 7 failing lanes that were this one gate (6331 passed,
    1 failed). Crucially, NO value of _MAX_RELEASES_BEHIND would have prevented any of it:
    the sentinel is `_MAX_RELEASES_BEHIND + 1` by construction, so the assertion fails for
    every tolerance. Widening the constant is not a weak fix here, it is a no-op.

    Ordinal distance makes a minor bump worth exactly one release, which is what it is.
    """
    order = _release_order()
    stamp_key = ".".join(map(str, stamp))
    shipped_key = ".".join(map(str, shipped))
    for key in (stamp_key, shipped_key):
        if key not in order:
            raise UnknownRelease(
                f"v{key} is not in CHANGELOG.md. This is a COULD-NOT-MEASURE, not a "
                "staleness verdict -- fix the version rather than reading it as fresh."
            )
    # newest-first, so the older version carries the LARGER index.
    return order[stamp_key] - order[shipped_key]


def test_stamp_is_parseable_and_the_gate_actually_read_both_files() -> None:
    """Positive control: an empty or unreadable input must never pass as 'fresh'.

    Without this, a renamed heading or a moved file would make every assertion below
    vacuously true -- the scan-never-ran failure this repo keeps re-learning.
    """
    assert _BOARD_PATH.is_file(), f"{_BOARD_PATH} is missing"
    assert _PYPROJECT_PATH.is_file(), f"{_PYPROJECT_PATH} is missing"

    stamp = _board_stamp_version()
    shipped = _shipped_version()

    assert stamp > (0, 0, 0), "parsed a zero stamp -- the regex matched something meaningless"
    assert shipped > (0, 0, 0), "parsed a zero shipped version"


def test_task_board_reconcile_stamp_is_not_many_releases_stale() -> None:
    stamp = _board_stamp_version()
    shipped = _shipped_version()
    behind = _releases_behind(stamp, shipped)

    assert behind <= _MAX_RELEASES_BEHIND, (
        f"docs/TASK_BOARD.md's reconcile stamp is v{'.'.join(map(str, stamp))} while "
        f"pyproject ships v{'.'.join(map(str, shipped))} -- {behind} releases behind "
        f"(tolerance {_MAX_RELEASES_BEHIND}).\n\n"
        "This board has gone stale four times the same way; the last audit found NINE items "
        "listed OPEN that were already fixed. Do not just bump the stamp -- reconcile the "
        "board against reality first, THEN bump it. Derive both numbers, never retype:\n"
        "  gh pr list --state open --json number,title\n"
        '  python -c "import json,urllib.request;'
        "print(json.load(urllib.request.urlopen("
        "'https://pypi.org/pypi/tensor-grep/json'))['info']['version'])\"\n\n"
        "A green run here means the stamp is RECENT, not that the content is correct."
    )


@pytest.mark.parametrize(
    "stamp, shipped, expected_fires",
    [
        ((1, 101, 27), (1, 101, 28), False),  # ordinary one-release lag -- must NOT fire
        ((1, 101, 24), (1, 101, 29), False),  # distance 5, exactly at tolerance -- must NOT fire
        ((1, 101, 9), (1, 101, 22), True),  # the real 13-release incident -- MUST fire
        ((1, 101, 19), (1, 101, 27), True),  # the real 8-release incident -- MUST fire
        # BOTH DIRECTIONS ACROSS A MINOR BUMP. The old implementation tested this axis in
        # ONE direction only ("minor bump -- unbounded, MUST fire"), so it could not fail on
        # the axis where the 2026-08-04 outage lived: a stamp ONE release behind across a
        # minor boundary scored the sentinel and reddened main. These two are the regression.
        ((1, 102, 7), (1, 103, 0), False),  # THE INCIDENT: distance 2 across v1.102->v1.103
        ((1, 102, 4), (1, 103, 0), False),  # distance 5 across a minor bump -- at tolerance
        ((1, 102, 3), (1, 103, 0), True),  # distance 6 across a minor bump -- genuinely stale
    ],
)
def test_tolerance_fires_on_recorded_staleness_and_not_on_normal_lag(
    stamp: tuple[int, int, int],
    shipped: tuple[int, int, int],
    expected_fires: bool,
) -> None:
    """Bidirectional proof of the THRESHOLD itself, using the board's real incidents.

    A tolerance that never fires is decoration, and one that fires constantly gets disabled.
    Both failure modes are pinned here with concrete historical numbers, so a future edit to
    `_MAX_RELEASES_BEHIND` has to confront what it breaks.
    """
    fires = _releases_behind(stamp, shipped) > _MAX_RELEASES_BEHIND
    assert fires is expected_fires


def test_release_order_is_read_and_is_not_a_stub() -> None:
    """Positive control on the INSTRUMENT: a short parse would make every distance falsely small.

    A renamed heading or a moved CHANGELOG.md yields few or zero entries, and every staleness
    distance computed from it collapses toward 0 -- i.e. reports FRESH. That is the
    scan-never-ran failure wearing a green badge, so the parse asserts its own floor.
    """
    order = _release_order()
    assert len(order) >= _MIN_EXPECTED_RELEASES
    shipped = ".".join(map(str, _shipped_version()))
    assert shipped in order, (
        f"pyproject ships v{shipped} but CHANGELOG.md has no heading for it -- "
        "the two files disagree about what was released."
    )
    assert order[shipped] == 0, (
        f"v{shipped} is not the newest CHANGELOG.md entry (index {order[shipped]}). "
        "Either the file is not newest-first or pyproject is behind the changelog."
    )


def test_an_unknown_version_is_a_could_not_measure_not_a_staleness_verdict() -> None:
    """A hand-edited or typo'd stamp must fail LOUDLY, never score 0 and read as fresh.

    v1.100.27 was in this file's own parametrized cases until 2026-08-04 and has never been
    released -- so the previous implementation was scoring a distance for a version that does
    not exist. Under ordinal distance that must raise rather than silently succeed.
    """
    with pytest.raises(UnknownRelease):
        _releases_behind((1, 100, 27), _shipped_version())
