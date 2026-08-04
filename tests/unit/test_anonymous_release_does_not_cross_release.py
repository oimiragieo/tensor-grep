"""An anonymous agent must not release ANOTHER anonymous agent's claim by symbol.

`release_claim`'s docstring promises that a symbol release is "scoped to the resolved ``agent_id``'s
OWN live claims only, so a common/guessable symbol name can never release another agent's claim by
accident". That guarantee is expressed as::

    matches_symbol = (
        symbol is not None
        and entry.get("agent_id") == resolved_agent_id
        and symbol in (entry.get("symbols") or [])
    )

which uses `agent_id` equality AS identity. The default id is the literal sentinel
``_DEFAULT_AGENT_ID == "anonymous"`` -- the ABSENCE of an identity, not one. Two zero-config agents
both resolve to it, the equality holds, and agent B's `release --symbol foo` silently releases agent
A's claim.

This is the SAME sentinel-as-identity confusion that `_find_overlaps` was fixed for
(`test_anonymous_claims_are_not_one_agent.py`). That fix guarded the CLAIM path and its MIRROR on the
RELEASE path was never updated -- one defect class, fixed on one of its two sites.

Blast radius is bounded by the ledger's advisory contract: a cross-release drops a coordination
signal, it never loses an edit. That is why this is a correctness/honesty fix and not a security fix
-- but the docstring states a guarantee that is false, and a false stated guarantee is worse than an
absent one, because callers rely on it.

Found by an adversarial design-council seat (2026-08-03) asked whether anonymous claims were already
safe. The claim path was; this mirror was not.
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

    `delenv` is deliberate and safe here: this variable is read directly, not repopulated by a
    `load_dotenv` on absence. Asserted rather than assumed, per the repo's
    delenv-does-not-disable trap.
    """
    monkeypatch.delenv("TG_LEDGER_AGENT_ID", raising=False)
    assert os.environ.get("TG_LEDGER_AGENT_ID") is None


def test_anonymous_release_by_symbol_does_not_release_another_anonymous_claim(
    tmp_path: Path,
) -> None:
    """THE DEFECT: agent B's symbol release took agent A's claim."""
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "anon-release")
    first = submit_claim(str(root), symbols=["target"], files=["a.py"], intent="agent A edit")
    agent_a_claim_id = first["claim"]["claim_id"]

    # Agent B -- a DIFFERENT zero-config agent -- releases "target", which it never claimed.
    result = release_claim(str(root), symbol="target")

    released_ids = [entry.get("claim_id") for entry in (result.get("released") or [])]
    assert agent_a_claim_id not in released_ids, (
        "an anonymous agent released a DIFFERENT anonymous agent's claim by symbol; "
        "`release_claim`'s docstring promises symbol release is scoped to the caller's OWN claims"
    )


def test_named_agent_symbol_release_still_scopes_to_its_own_claims(tmp_path: Path) -> None:
    """CONTROL (mechanism works): a named agent still releases its own claim by symbol.

    Without this arm the fix could be 'symbol release never matches anything', which would pass the
    test above for the wrong reason.
    """
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "named-release")
    first = submit_claim(
        str(root), symbols=["target"], files=["a.py"], intent="edit", agent_id="agent-a"
    )
    own_claim_id = first["claim"]["claim_id"]

    result = release_claim(str(root), symbol="target", agent_id="agent-a")

    released_ids = [entry.get("claim_id") for entry in (result.get("released") or [])]
    assert own_claim_id in released_ids, (
        "a NAMED agent must still be able to release its own claim by symbol -- the fix must scope "
        "the sentinel, not disable symbol release entirely"
    )


def test_named_agent_symbol_release_does_not_take_another_named_agents_claim(
    tmp_path: Path,
) -> None:
    """CONTROL: the pre-existing cross-agent guarantee is unchanged for named agents."""
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "named-cross")
    first = submit_claim(
        str(root), symbols=["target"], files=["a.py"], intent="edit", agent_id="agent-a"
    )
    other_claim_id = first["claim"]["claim_id"]

    result = release_claim(str(root), symbol="target", agent_id="agent-b")

    released_ids = [entry.get("claim_id") for entry in (result.get("released") or [])]
    assert other_claim_id not in released_ids


def test_anonymous_symbol_release_says_why_it_matched_nothing(tmp_path: Path) -> None:
    """Label the zero at the point of reporting.

    Refusing to match is only half the fix. Without a specific reason the caller gets the generic
    "No live claim matched the given --claim-id/--symbol" -- true, but it hides WHY and offers no
    route forward, so an agent reads a deliberate refusal as "already released" and moves on.
    """
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "anon-reason")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="agent A edit")

    result = release_claim(str(root), symbol="target")

    assert (result.get("released") or []) == []
    reason = result.get("unmatched_reason") or ""
    assert "by design" in reason, f"generic reason hides the deliberate refusal: {reason!r}"
    assert "--claim-id" in reason, "the reason must name the route that DOES work"
    assert "TG_LEDGER_AGENT_ID" in reason, "the reason must name the identity env var"


def test_named_agent_zero_match_keeps_the_generic_reason(tmp_path: Path) -> None:
    """CONTROL: the anonymous reason must not leak onto ordinary zero-match releases.

    Without this arm the reason could be emitted unconditionally and the test above would still
    pass -- the classic 'passes in both arms' shape.
    """
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "named-zero")
    submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit", agent_id="agent-a")

    result = release_claim(str(root), symbol="not-a-real-symbol", agent_id="agent-a")

    assert (result.get("released") or []) == []
    reason = result.get("unmatched_reason") or ""
    assert "by design" not in reason, (
        "a named agent's ordinary zero-match must keep the generic reason, not the anonymous one"
    )


def test_anonymous_agent_can_still_release_by_claim_id(tmp_path: Path) -> None:
    """The documented escape route for an anonymous caller must keep working.

    `release_claim`'s docstring states the opaque `claim_id` IS the authorization ("any agent may
    release a claim it knows the opaque id for"), deliberately identity-independent. Scoping the
    sentinel on the SYMBOL path must not touch that, or an anonymous agent would have no way to
    release its own claim at all.
    """
    from tensor_grep.cli.ledger_store import release_claim, submit_claim

    root = _fresh_repo(tmp_path, "anon-by-id")
    first = submit_claim(str(root), symbols=["target"], files=["a.py"], intent="edit")
    claim_id = first["claim"]["claim_id"]

    result = release_claim(str(root), claim_id=claim_id)

    released_ids = [entry.get("claim_id") for entry in (result.get("released") or [])]
    assert claim_id in released_ids, (
        "an anonymous agent must retain claim_id release -- it is the only identity-independent "
        "route it has"
    )
