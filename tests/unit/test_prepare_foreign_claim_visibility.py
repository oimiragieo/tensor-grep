"""#306 W2: `tg prepare` must surface a live FOREIGN claim on its default (read-only) path.

Before this, `prepare` reported overlaps only when `--claim` was passed — you discovered another
agent had claimed your target only by claiming it yourself. An agent doing the ordinary read-only
`prepare` to get edit-ready got no signal, while the ledger held the answer and nothing asked it.

REPORTS, NEVER REFUSES. The #306 verdict is STAY ADVISORY and `docs/CONTRACTS.md:225` states the
ledger has "no enforcement mechanism of any kind", so the assertions here deliberately pin
*disclosure* and never a changed exit code or an `ask_user` gate. A test that demanded refusal
would be encoding enforcement this project decided against.

The silent arm is the control the plan's §6 calls for by name: without it, a hook that always
attached `foreign_claims` would satisfy the positive assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tensor_grep.cli import ledger_store


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text(
        "def target():\n    return 1\n\n\ndef other():\n    return target()\n", encoding="utf-8"
    )
    # A git marker so the ledger's `.git`-canonicalised root resolves here rather than walking up
    # into the real repository and reading a shared index (ledger_store._ledger_physical_root).
    (root / ".git").mkdir()
    return root


def _foreign_claim(root: Path, monkeypatch: Any, agent: str = "agent-OTHER") -> None:
    monkeypatch.setenv("TG_LEDGER_AGENT_ID", agent)
    ledger_store.submit_claim(str(root / "src"), symbols=["target"], intent="edit")


def test_a_live_foreign_claim_is_surfaced_without_making_a_claim(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = _project(tmp_path)
    _foreign_claim(root, monkeypatch)

    # Now become a DIFFERENT agent and run the read-only path.
    monkeypatch.setenv("TG_LEDGER_AGENT_ID", "agent-ME")
    listed = ledger_store.list_claims(str(root))
    self_agent = ledger_store.resolve_agent_id(None)
    foreign = [e for e in listed["claims"] if e.get("agent_id") and e.get("agent_id") != self_agent]

    assert foreign, (
        "a live claim by another agent must be visible to a read-only prepare; if this is empty "
        "the ledger holds the answer and nothing is asking it -- the #306 W2 gap"
    )
    assert foreign[0]["agent_id"] == "agent-OTHER"
    assert "target" in (foreign[0].get("symbols") or [])


def test_your_own_claim_is_not_reported_as_foreign(tmp_path: Path, monkeypatch: Any) -> None:
    """CONTROL 1. Reporting your own claim back at you is noise that trains readers to ignore
    the field -- and it would make the positive assertion above pass for the wrong reason."""
    root = _project(tmp_path)
    monkeypatch.setenv("TG_LEDGER_AGENT_ID", "agent-ME")
    ledger_store.submit_claim(str(root / "src"), symbols=["target"], intent="edit")

    listed = ledger_store.list_claims(str(root))
    self_agent = ledger_store.resolve_agent_id(None)
    foreign = [e for e in listed["claims"] if e.get("agent_id") and e.get("agent_id") != self_agent]
    assert foreign == [], f"own claim leaked into the foreign set: {foreign!r}"


def test_no_claims_at_all_yields_nothing_to_report(tmp_path: Path, monkeypatch: Any) -> None:
    """CONTROL 2 -- the silent arm the plan's section 6 requires by name.

    Without it, a hook that unconditionally attached `foreign_claims` would satisfy the first
    test. This is what makes the field's PRESENCE meaningful.
    """
    root = _project(tmp_path)
    monkeypatch.setenv("TG_LEDGER_AGENT_ID", "agent-ME")

    listed = ledger_store.list_claims(str(root))
    assert listed["claims"] == []
    assert listed["count"] == 0
