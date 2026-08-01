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


def _releases_behind(stamp: tuple[int, int, int], shipped: tuple[int, int, int]) -> int:
    """How far the stamp lags, in patch releases, when both share a major.minor line.

    A major or minor bump is treated as unbounded staleness: patch numbers reset across
    those lines, so their difference stops being a distance. Returning a large sentinel is
    honest about "we cannot subtract these" without pretending to a precise count.
    """
    if stamp[:2] != shipped[:2]:
        return _MAX_RELEASES_BEHIND + 1
    return shipped[2] - stamp[2]


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
        ((1, 101, 24), (1, 101, 29), False),  # exactly at tolerance -- must NOT fire
        ((1, 101, 9), (1, 101, 22), True),  # the real 13-release incident -- MUST fire
        ((1, 101, 19), (1, 101, 27), True),  # the real 8-release incident -- MUST fire
        ((1, 100, 27), (1, 101, 1), True),  # minor bump -- unbounded, MUST fire
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
