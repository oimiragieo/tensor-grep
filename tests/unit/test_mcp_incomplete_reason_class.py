"""MCP must expose the closed incompleteness vocabulary, not just free text.

`mcp_server.py` carried `result_incomplete` at 14 sites and the human-readable
`incomplete_reason`, but ZERO occurrences of `incomplete_reason_class` -- the closed-vocabulary
field `docs/CONTRACTS.md` introduced (#276 slice 1) precisely so an agent could branch on WHY a
result is partial without string-sniffing prose. MCP is the most machine-facing surface tg has,
so it was the one surface where the field mattered most and the one place it never reached.

THE CONTROL CARRIES THE WEIGHT HERE. A field that is always emitted is indistinguishable from one
that works, so `test_an_unclassified_result_emits_no_class_key` is what makes the field's PRESENCE
meaningful -- and it also pins the byte-identity promise: a payload with no classified cause must
stay identical to contract 1.5.0, so existing readers see no new key at all.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli import mcp_server

# The closed vocabulary, per docs/CONTRACTS.md. Deliberately NOT imported from the module under
# test: a test that reads its expectation from the code it checks cannot catch the code changing.
_CLOSED_VOCABULARY = frozenset({"unreadable_path", "scan_limit", "deadline", "timeout"})


class _Results:
    """Minimal stand-in for the SearchResult aggregate the payload builders read."""

    def __init__(self, reason_class: str | None) -> None:
        self.incomplete_reason_class = reason_class


def test_a_classified_result_emits_the_class() -> None:
    fragment = mcp_server._incomplete_class_fragment(_Results("deadline"))
    assert fragment == {"incomplete_reason_class": "deadline"}


def test_an_unclassified_result_emits_no_class_key() -> None:
    """CONTROL. Without it, a helper that always emitted the key would satisfy the test above.

    Also the byte-identity guard: `**{}` contributes nothing, so a payload with no classified
    cause is unchanged from contract 1.5.0. Emitting `null` would look equivalent and is not --
    it teaches readers to skip the key, which is how a disclosure field becomes decoration.
    """
    assert mcp_server._incomplete_class_fragment(_Results(None)) == {}
    assert mcp_server._incomplete_class_fragment(_Results("")) == {}


def test_a_results_object_without_the_attribute_is_tolerated() -> None:
    """Defensive: payload builders are shared, and a caller passing a leaner object must not 500."""
    assert mcp_server._incomplete_class_fragment(object()) == {}


def test_every_class_the_mcp_layer_can_set_is_in_the_closed_vocabulary() -> None:
    """The allow-list rule (#282): a value outside the closed set is worse than no value.

    Reads the SOURCE for literals assigned to `incomplete_reason_class`, so a future site that
    invents `"backend_error"` or copies MCP's hyphenated `truncation_cause` members fails here.
    #293 settled that those two vocabularies must NOT be unified.
    """
    import inspect
    import re

    source = inspect.getsource(mcp_server)
    assigned = set(re.findall(r'incomplete_reason_class\s*=\s*"([^"]+)"', source))
    unexpected = assigned - _CLOSED_VOCABULARY
    assert not unexpected, (
        f"mcp_server assigns incomplete_reason_class values outside the closed vocabulary: "
        f"{sorted(unexpected)}. The vocabulary is an allow-list -- if a cause does not map to one "
        f"of {sorted(_CLOSED_VOCABULARY)}, emit NOTHING rather than inventing a member."
    )
    # PREMISE: if this is empty the test above passes vacuously and proves nothing.
    assert assigned, (
        "no incomplete_reason_class assignment found in mcp_server -- either the field was "
        "removed or this detector stopped matching; either way it is no longer checking anything"
    )


def test_the_contract_version_was_bumped_for_the_additive_field() -> None:
    """An additive field is a minor bump (CHANGELOG.md:1358 precedent, 1.4.0 -> 1.5.0)."""
    major, minor, _patch = mcp_server._TG_MCP_SERVER_CONTRACT_VERSION.split(".")
    assert (int(major), int(minor)) >= (1, 6), (
        "adding incomplete_reason_class to the MCP payload is a wire change and needs a minor "
        f"bump; contract is still {mcp_server._TG_MCP_SERVER_CONTRACT_VERSION}"
    )


def test_payload_builders_actually_splat_the_fragment() -> None:
    """The producer->consumer seam: a helper nothing calls is a helper that cannot disclose.

    Hand-built fixtures cannot catch this -- they exercise the helper directly, which passes
    whether or not any payload builder uses it. Counts real call sites in the source instead.
    """
    import inspect

    source = inspect.getsource(mcp_server)
    call_sites = source.count("**_incomplete_class_fragment(")
    assert call_sites >= 5, (
        f"expected the fragment to be splatted into every result payload builder; found "
        f"{call_sites}. A site that emits result_incomplete without the class leaves an agent "
        f"string-sniffing prose on exactly the surface this change exists to fix."
    )


def test_result_incomplete_and_the_class_are_independent_signals() -> None:
    """Absence of a class must NEVER be read as 'scan complete'.

    Pinned because it is the easy misreading and it fails OPEN: the AST-backend-failure site sets
    `result_incomplete` and deliberately emits no class (a backend bug is none of the four
    members). A consumer that treats a missing class as completeness would call that scan clean.
    """
    incomplete_but_unclassified: dict[str, Any] = {
        "result_incomplete": True,
        "incomplete_reason": "AST backend failed on one or more files",
        **mcp_server._incomplete_class_fragment(_Results(None)),
    }
    assert incomplete_but_unclassified["result_incomplete"] is True
    assert "incomplete_reason_class" not in incomplete_but_unclassified
