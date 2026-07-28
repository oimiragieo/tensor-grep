"""Two zero-config agents must not be treated as ONE agent by the ledger.

`_find_overlaps` suppressed entries whose `agent_id` equalled the claimant's, with the docstring
"self-overlap is not interesting -- an agent claiming two overlapping things is not a coordination
conflict". That reasoning is correct, and it REQUIRES the id to identify an agent.

The default id is the literal sentinel ``"anonymous"`` (``_DEFAULT_AGENT_ID``) -- the ABSENCE of an
identity, not one. So two genuinely different agents, both running zero-config, suppressed each
other: the second claimant saw NO overlap on a symbol the first had already claimed. tg's
multi-agent coordination pillar silently no-opped in exactly the configuration most callers run.

Measured before the fix, with a control that discriminates:

    two anonymous agents, same symbol -> second sees 0 overlaps   (BLIND)
    named agent, same symbol          -> sees 2 overlaps          (mechanism works)

The mechanism was never broken; the sentinel disabled it. Found by an independent strategic review
(2026-07-28) that read the suppression and asked what the default id actually is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _fresh_repo(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "a.py").write_text("def target():\n    pass\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _zero_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run exactly as a fresh agent does: no identity in the environment.

    `delenv` is deliberate here and safe: this variable is read directly, not filled by a
    `load_dotenv` on absence. (The repo's `delenv`-does-not-disable trap applies to vars a
    dotenv loader repopulates; this is not one. Asserted below rather than assumed.)
    """
    monkeypatch.delenv("TG_LEDGER_AGENT_ID", raising=False)
    assert os.environ.get("TG_LEDGER_AGENT_ID") is None


def test_two_anonymous_agents_see_each_other(tmp_path: Path) -> None:
    """THE DEFECT: the second anonymous claimant saw zero overlaps."""
    from tensor_grep.cli.ledger_store import submit_claim

    root = _fresh_repo(tmp_path, "anon")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit A")
    second = submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit B")

    overlaps = second.get("overlaps") or []
    assert overlaps, (
        "a second zero-config agent claiming the SAME symbol sees no overlap -- the coordination "
        "pillar no-ops in its default configuration"
    )


def test_a_named_agent_still_does_not_see_its_own_claims(tmp_path: Path) -> None:
    """CONTROL ARM: the suppression the docstring describes must survive.

    Without this, 'report every overlap unconditionally' would satisfy the test above while
    breaking the behaviour that was deliberately designed -- an agent's own two overlapping
    claims are not a conflict.
    """
    from tensor_grep.cli.ledger_store import submit_claim

    root = _fresh_repo(tmp_path, "named_self")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="one", agent_id="agent-x")
    again = submit_claim(
        str(root), symbols=["target"], files=["a.py"], intent="two", agent_id="agent-x"
    )
    assert (again.get("overlaps") or []) == [], "a named agent must not overlap with itself"


def test_a_named_agent_still_sees_a_different_agents_claim(tmp_path: Path) -> None:
    """CONTROL ARM: cross-agent detection for NAMED ids is untouched by the fix."""
    from tensor_grep.cli.ledger_store import submit_claim

    root = _fresh_repo(tmp_path, "named_cross")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="one", agent_id="agent-x")
    other = submit_claim(
        str(root), symbols=["target"], files=["a.py"], intent="two", agent_id="agent-y"
    )
    assert other.get("overlaps"), "a named agent must still see a different agent's claim"


def test_an_anonymous_agent_sees_a_named_agents_claim(tmp_path: Path) -> None:
    """The mixed case: one side configured, the other not. Both directions must detect."""
    from tensor_grep.cli.ledger_store import submit_claim

    root = _fresh_repo(tmp_path, "mixed")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="named", agent_id="agent-x")
    anon = submit_claim(str(root), symbols=["target"], files=["a.py"], intent="anon")
    assert anon.get("overlaps"), "an anonymous agent must see a named agent's claim"


def test_the_suppression_still_keys_on_the_sentinel(tmp_path: Path) -> None:
    """PREMISE: the fix compares against `_DEFAULT_AGENT_ID`. If that constant is renamed or
    revalued without this file being updated, the suppression silently returns and every
    assertion above starts passing for the wrong reason.

    Pinned by BEHAVIOUR, not by a tautology: two claims made under the CURRENT sentinel value must
    see each other, and two under any other shared id must not. An earlier cut of this test ended
    in `assert ... or True` -- a check that cannot fail, in the one file whose subject is checks
    that cannot fail.
    """
    from tensor_grep.cli.ledger_store import _DEFAULT_AGENT_ID, submit_claim

    assert _DEFAULT_AGENT_ID == "anonymous"

    sentinel_root = _fresh_repo(tmp_path, "sentinel")
    submit_claim(str(sentinel_root), symbols=["target"], files=["a.py"], intent="one")
    under_sentinel = submit_claim(
        str(sentinel_root), symbols=["target"], files=["a.py"], intent="two"
    )
    assert under_sentinel.get("overlaps"), "claims under the sentinel must NOT suppress each other"

    named_root = _fresh_repo(tmp_path, "not_sentinel")
    submit_claim(
        str(named_root), symbols=["target"], files=["a.py"], intent="one", agent_id="real-id"
    )
    under_named = submit_claim(
        str(named_root), symbols=["target"], files=["a.py"], intent="two", agent_id="real-id"
    )
    assert (under_named.get("overlaps") or []) == [], (
        "claims under a REAL shared id must still suppress -- if this fails, the fix stopped "
        "discriminating and is now reporting every overlap unconditionally"
    )
