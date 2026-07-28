"""Four emitters that DID disclose, but AFTER the numbers they qualify (task #329).

These are the mild half of the disclosure class -- unlike the thirteen silent commands (#837) or
``docs-coverage --stale`` (#840), each of these already told the truth. It just arrived late:

* ``codemap``      -- ``PARTIAL:`` printed below ``folders=/files=/symbols=``
* ``inventory``    -- ``[!] truncated ...`` printed below ``inventory: N files, X MB``
* ``route-test``   -- ``partial=true`` printed below the ``agreement=`` VERDICT
* ``session open`` -- the cap notice printed below ``files=/symbols=``

``codemap`` is the instructive one: it was the ONLY command in its family that disclosed at all,
and that is exactly why its ordering went unexamined. "It discloses" read as "it is fine".

REGISTER decides whether the old line moves or stays. ``codemap`` and ``inventory`` emit PROSE --
same register as a leading banner -- so those move. ``route-test`` emits ``partial=true
agreement_basis=...``, a structured field in a key=value listing that something may parse, so it
moves POSITION but keeps its FORM. Same call made for ``prepare``; the opposite call was made for
``inventory`` in #837, where adding a banner beside its prose would have said it twice.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.inventory import render_inventory_text


def _inventory(**scan: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "path": "/repo",
        "totals": {"files": 812, "bytes": 4_300_000},
        "binary": {"files": 0, "bytes": 0},
        "languages": [],
        "categories": [],
        "scan_limit": {"possibly_truncated": False, "truncation_cause": None, "max_files": 512},
    }
    if scan:
        base["scan_limit"].update(scan)
    return base


_TOTALS = "inventory: 812 files"


def test_inventory_truncation_leads_its_totals() -> None:
    out = render_inventory_text(
        _inventory(possibly_truncated=True, truncation_cause="project-files")
    )
    # Premise: the totals really rendered, so the ordering assertion is not vacuous.
    assert _TOTALS in out
    assert out.splitlines()[0].lstrip().startswith("[!]")
    assert out.index("[!]") < out.index(_TOTALS)


def test_inventory_unreadable_path_still_refuses_budget_advice_after_the_move() -> None:
    # Task #284's wrong-knob guard must survive the extraction: no --max-files value makes a
    # denied path readable, so this arm must not name a cap.
    out = render_inventory_text(
        _inventory(possibly_truncated=True, truncation_cause="unreadable-path")
    )
    assert "will NOT help" in out
    assert "truncated at max_files" not in out


def test_inventory_deadline_cause_survives_the_move() -> None:
    out = render_inventory_text(_inventory(possibly_truncated=True, truncation_cause="deadline"))
    assert "cause=deadline" in out
    assert out.index("[!]") < out.index(_TOTALS)


def test_a_complete_inventory_is_byte_identical_to_before() -> None:
    # CONTROL ARM: without it, a renderer that always led with a banner would satisfy every
    # assertion above while changing output on every healthy run.
    out = render_inventory_text(_inventory())
    assert out.splitlines()[0].startswith(_TOTALS)
    assert "[!]" not in out
