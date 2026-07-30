"""A zero-config `tg prepare --claim` must say it is UNATTRIBUTABLE, not just suggest a env var.

Backlog #23. Reported by four consecutive live dogfoods as "default --claim -> anonymous unless
env/--agent-id (hint helps; still easy to misuse)".

WHAT WAS *NOT* DONE, AND WHY -- this is the load-bearing half of the item.

The obvious fix is to auto-derive a stable id. An adversarial audit rejected it with a receipt:
`_find_overlaps` suppresses an entry when ``new.agent_id != _DEFAULT_AGENT_ID and
entry.agent_id == new.agent_id``. Two zero-config agents today both resolve to the sentinel, the
first conjunct is False, suppression is skipped, and they see each other -- that IS the #845 fix.
Under a per-checkout derived id they would share one non-sentinel id, suppression WOULD fire, and
each would silently drop the other's overlaps: #845 reproduced by a new mechanism, in the ledger's
primary use case.

Nor can any derivation escape it. `tg` is a CLI: every invocation is a fresh process. A
per-CHECKOUT id conflates agents; a per-PROCESS id is not stable across one agent's calls. Agent
identity is not derivable from the environment -- only the caller knows it.

So the sentinel stays and the SIGNAL gets louder. Refusing outright was rejected too: it breaks
every existing caller and doc example for a problem the hint covers.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _zero_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run exactly as a fresh agent does. `setenv("")` rather than `delenv`: an absent var is what
    a stray `load_dotenv` refills, while a PRESENT-but-empty one cannot be."""
    monkeypatch.setenv("TG_LEDGER_AGENT_ID", "")
    monkeypatch.setenv("TG_EVIDENCE_AGENT_ID", "")


def test_zero_config_still_resolves_to_the_sentinel() -> None:
    """THE DECISION, pinned. If this ever returns a derived id, #845 is silently re-broken."""
    from tensor_grep.cli.ledger_store import _DEFAULT_AGENT_ID, resolve_agent_id

    assert resolve_agent_id(None) == _DEFAULT_AGENT_ID, (
        "resolve_agent_id now derives an identity. That re-breaks #845: two zero-config agents in "
        "one checkout would share a non-sentinel id, _find_overlaps would suppress, and each "
        "would silently drop the other's overlaps. See the docstring for the full receipt."
    )


def test_an_explicit_id_still_wins() -> None:
    """CONTROL ARM: the sentinel is the FALLBACK, not the only answer.

    Without this, a change that hard-coded the sentinel would satisfy the test above while
    destroying the caller's ability to identify itself at all.
    """
    from tensor_grep.cli.ledger_store import resolve_agent_id

    assert resolve_agent_id("agent-x") == "agent-x"

    os.environ["TG_LEDGER_AGENT_ID"] = "from-env"
    try:
        assert resolve_agent_id(None) == "from-env"
    finally:
        os.environ["TG_LEDGER_AGENT_ID"] = ""


def test_two_zero_config_agents_still_see_each_other(tmp_path) -> None:
    """The #845 pin, restated here because THIS item is the one most likely to break it.

    A future contributor reading "make --claim identify itself" will reach for a derived id. This
    test is what stops that change from landing green.
    """
    from tensor_grep.cli.ledger_store import submit_claim

    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "a.py").write_text("def target():\n    pass\n", encoding="utf-8")

    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit A")
    second = submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit B")

    assert second.get("overlaps"), (
        "a second zero-config agent no longer sees the first's claim -- the coordination pillar "
        "has silently no-opped in its default configuration (#845)"
    )


def test_the_hint_says_what_is_LOST_not_just_what_to_set() -> None:
    """The actual #23 change: the old hint was present-but-easy-to-miss for four releases.

    Pinned by source at the emission site -- reaching it needs a full `prepare` run against a real
    repo. The assertion is about CONTENT, not existence: "set TG_LEDGER_AGENT_ID for a stable
    identity" is a suggestion; "this claim is NOT attributable to you" is a consequence.
    """
    import inspect

    from tensor_grep.cli import main as cli_main

    source = inspect.getsource(cli_main)
    marker = 'claim_hook["agent_id_hint"]'
    assert marker in source, "the anonymous-claim hint was removed"

    window = source.split(marker, 1)[1][:400]
    assert "NOT attributable" in window, (
        "the hint still only suggests an env var. Four dogfoods reported that as insufficient; it "
        "must state the consequence -- the claim cannot be attributed to the caller."
    )
    assert 'claim_hook["agent_id_is_anonymous"]' in source, (
        "no machine-branchable sibling field; a harness would have to string-match the prose"
    )
