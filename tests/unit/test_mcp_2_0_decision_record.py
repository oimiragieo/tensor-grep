"""Structural checker for the MCP 2.0 exposure decision record (`W2-a`).

``docs/design/2026-08-20-mcp-2-0-exposure-decision.md`` carries a structured YAML front-block
naming six distinct reopen triggers. r1 of the closeout plan used a bare ``grep -c 'REOPEN
TRIGGER'`` heading count, which three copies of the same heading text could satisfy without any
of the triggers actually being distinct. This test parses the ``id:``/``type:`` pairs instead, so
a repeated heading -- or six triggers sharing one type -- fails it.

See ``docs/plans/2026-08-20-worldclass-closeout-plan.md`` section W2.3 for the acceptance
criteria this test encodes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RECORD = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "design"
    / "2026-08-20-mcp-2-0-exposure-decision.md"
)

_EXPECTED_IDS = {"T1", "T2", "T3", "T4", "T5", "T6"}


def _load() -> str:
    assert _RECORD.exists(), f"decision record missing: {_RECORD}"
    return _RECORD.read_text(encoding="utf-8")


def _extract(text: str) -> tuple[set[str], list[str]]:
    ids = set(re.findall(r"id:\s*(T[1-6])", text))
    types = re.findall(r"type:\s*(\w+)", text)
    return ids, types


def test_decision_record_declares_all_six_distinct_triggers() -> None:
    text = _load()
    ids, types = _extract(text)

    assert ids == _EXPECTED_IDS, f"expected exactly {_EXPECTED_IDS}, found {sorted(ids)}"
    assert len(set(types)) == 6, (
        "all six triggers must carry distinct `type:` values "
        f"(found {len(set(types))} distinct types among {types})"
    )
    assert "revalidate_by:" in text
    assert "monitoring_owner:" in text
    assert "decision: PIN_AND_DEFER" in text


def test_decision_record_names_the_mcp_contract_version_distinction() -> None:
    """The record must not conflate tg's own MCP tool-surface contract version with the wire
    protocol version -- W2.1 flags this as a real drift risk (the research receipt's own line
    citation for ``_TG_MCP_SERVER_CONTRACT_VERSION`` had drifted through an earlier refactor)."""
    text = _load()
    assert "_TG_MCP_SERVER_CONTRACT_VERSION" in text
    assert "unrelated to" in text or "not the" in text


@pytest.mark.parametrize(
    "mutated_text",
    [
        # Perturbation 1: three copies of the same heading text, no distinct ids -- the exact
        # loophole the plan calls out (`grep -c 'REOPEN TRIGGER'` style checks would pass this).
        "\n".join(["## REOPEN TRIGGER"] * 3),
        # Perturbation 2: six ids present but all sharing one type -- must still fail.
        "\n".join(f"  - id: T{n}  type: upstream_maintenance_end" for n in range(1, 7)),
        # Perturbation 3: only five distinct ids.
        "\n".join(f"  - id: T{n}  type: kind_{n}" for n in range(1, 6)),
    ],
)
def test_checker_rejects_the_repeated_heading_loophole(mutated_text: str) -> None:
    """Proves the checker actually discriminates: feed it text shaped like the rejected r1
    mechanism and confirm it does NOT satisfy the six-distinct-trigger contract."""
    ids, types = _extract(mutated_text)
    ok = ids == _EXPECTED_IDS and len(set(types)) == 6
    assert not ok, "checker must reject a perturbation with repeated headings or shared types"
