"""The backlog must not contradict ITSELF.

No gate this repo owns compares a document to itself. That gap let `docs/BACKLOG.md`
carry, simultaneously and for a full day:

* a reconcile table recording **#859** SHIPPED with four merged PR receipts, and
* an "Active / buildable" list presenting **#859** as ready work, 24 lines below it.

Two further rows were in the same state and nobody had noticed at all -- CPU-BACKEND and
REF-CALL-REGISTRY were listed as buildable while their own completion receipts sat in the
table above. A session trusting the queue would have rebuilt finished code, and the
citations in the stale rows all resolve perfectly, so no anchor- or drift-checking gate
can catch this class.

This test compares the two sections against each other. It deliberately checks ONE
property -- an item recorded SHIPPED must not also be offered as buildable -- because a
gate that tries to validate the whole document ends up asserting nothing.

STRIKETHROUGH IS THE ESCAPE HATCH. A row kept for legibility (`- ~~**#859**~~ ... SHIPPED`)
is not an offer of work and is skipped. That is why the corrections above read as
strikethrough rather than deletions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKLOG = Path(__file__).resolve().parents[2] / "docs" / "BACKLOG.md"

_RECONCILE_HEADING = "### RECONCILED"
_ACTIVE_HEADING = "### Active / buildable"

# `| **#859** class-level atomic-writer census | **SHIPPED** | ... |`
_TABLE_ROW = re.compile(r"^\|\s*\*\*(?P<item>[^*]+)\*\*(?P<rest>.*)\|\s*$")
# `- **#89** — ...`  /  `- ~~**#859** — ...~~`
_BULLET = re.compile(r"^-\s+(?P<struck>~~)?\s*\*\*(?P<item>[^*]+)\*\*")


def _section(text: str, heading: str) -> str:
    """Return the body under `heading`, up to the next same-or-higher-level heading."""
    start = text.find(heading)
    if start == -1:
        pytest.fail(f"BACKLOG.md is missing the {heading!r} section this gate reads")
    body = text[start + len(heading) :]
    nxt = re.search(r"^#{1,3} ", body, re.MULTILINE)
    return body[: nxt.start()] if nxt else body


def _normalise(item: str) -> str:
    return item.strip().rstrip(":").split()[0].strip().upper()


def _shipped_items(text: str) -> set[str]:
    """Items the reconcile table records as fully SHIPPED.

    An item with ANY row that is not plain SHIPPED (e.g. F7, which has one SHIPPED row
    for Task 10 and one OPEN row for Task 11) is NOT counted -- it legitimately still
    has open work, and flagging it would train people to ignore this gate.
    """
    shipped: set[str] = set()
    not_fully: set[str] = set()
    for line in _section(text, _RECONCILE_HEADING).splitlines():
        m = _TABLE_ROW.match(line.strip())
        if not m:
            continue
        item = _normalise(m.group("item"))
        rest = m.group("rest")
        cells = [c.strip() for c in rest.split("|")]
        state = cells[1] if len(cells) > 1 else ""
        if state.strip("* ").upper() == "SHIPPED":
            shipped.add(item)
        else:
            not_fully.add(item)
    return shipped - not_fully


def _offered_items(text: str) -> set[str]:
    """Items the Active/buildable list offers as work (strikethrough rows excluded)."""
    return {
        _normalise(m.group("item"))
        for line in _section(text, _ACTIVE_HEADING).splitlines()
        if (m := _BULLET.match(line.strip())) and not m.group("struck")
    }


def _read() -> str:
    return _BACKLOG.read_text(encoding="utf-8")


def test_positive_control_both_sections_are_populated() -> None:
    """An empty parse must never read as 'no contradictions'.

    Without this, a heading rename silently turns the whole gate into a tautology --
    two empty sets never intersect. This is the control that makes the real assertion
    below mean something.
    """
    text = _read()
    shipped = _shipped_items(text)
    offered = _offered_items(text)
    assert len(shipped) >= 3, (
        f"parsed only {len(shipped)} SHIPPED rows from the reconcile table "
        f"({sorted(shipped)}) -- the parser is broken, so an empty intersection "
        f"below would prove nothing"
    )
    assert offered, (
        "parsed zero offered rows from the Active/buildable list -- the parser is "
        "broken, so an empty intersection below would prove nothing"
    )


def test_shipped_items_are_not_also_offered_as_buildable() -> None:
    text = _read()
    contradictions = _shipped_items(text) & _offered_items(text)
    assert not contradictions, (
        "docs/BACKLOG.md contradicts itself: "
        f"{sorted(contradictions)} are recorded SHIPPED in the reconcile table AND "
        "offered as buildable work in the Active/buildable list. Fix the stale row "
        "(strike it through with ~~...~~ to keep it legible), do not delete the "
        "receipt."
    )


_BOARD = Path(__file__).resolve().parents[2] / "docs" / "TASK_BOARD.md"
_BOARD_READY_RE = re.compile(
    r"^- \[ \] \*\*(?P<id>[#A-Z0-9-]+)\*\* — Status: READY;",
    re.MULTILINE,
)


def _board_ready_ids() -> set[str]:
    return {m.group("id").upper() for m in _BOARD_READY_RE.finditer(_BOARD.read_text(encoding="utf-8"))}


def test_board_ready_rows_do_not_contradict_backlog_shipped_receipts() -> None:
    """A board READY row must not also be a fully-SHIPPED reconcile receipt.

    The 2026-08-05 wave left TASK_BOARD saying READY while BACKLOG's reconcile table already
    recorded SHIPPED — the dispatch hazard the enterprise readiness census named. This gate
    compares the two committed artifacts with no network.
    """
    assert _BOARD.is_file(), f"{_BOARD} is missing"
    ready = _board_ready_ids()
    shipped = _shipped_items(_read())
    board_text = _BOARD.read_text(encoding="utf-8")
    assert "Status: READY;" in board_text or "Start-now READY set: EMPTY" in board_text, (
        "board has neither a READY row nor an explicit empty-READY stamp — the READY scanner "
        "cannot be validated"
    )
    assert shipped, "reconcile table parsed zero SHIPPED rows — cross-doc gate is inert"
    contradictions = ready & shipped
    assert not contradictions, (
        "docs/TASK_BOARD.md marks READY while docs/BACKLOG.md reconcile marks SHIPPED: "
        f"{sorted(contradictions)}. Flip the board row (or strike the false SHIPPED receipt)."
    )

