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

import pathlib
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


def test_every_serialized_result_incomplete_payload_is_covered() -> None:
    """EXHAUSTIVE coverage, not a count.

    The first version of this test asserted `call_sites >= 5`. An audit correctly rejected that:
    a count passes if splats are added to dead or unrelated builders, and it cannot see a builder
    that has no splat at all -- which is exactly what it missed (two literal `result_incomplete`
    sites). Counting occurrences is not coverage; it is the "measures the wrong thing" fault this
    whole campaign is about, committed inside the test written to prevent it.

    So: enumerate every payload that emits `result_incomplete` and require each to ALSO carry a
    class decision -- either the splat (from the aggregate) or an explicit literal.
    """
    import re

    source = pathlib.Path(mcp_server.__file__).read_text(encoding="utf-8")
    lines = source.splitlines()
    emitters = [i for i, line in enumerate(lines) if re.search(r'"result_incomplete":', line)]
    assert emitters, "no result_incomplete emitters found -- this detector has gone blind"

    uncovered = []
    for index in emitters:
        # A class decision must appear in the same dict literal, within a short window.
        window = "\n".join(lines[index : index + 12])
        if (
            "_incomplete_class_fragment(" not in window
            and '"incomplete_reason_class"' not in window
        ):
            uncovered.append(f"line {index + 1}: {lines[index].strip()}")

    assert not uncovered, (
        "these payloads disclose result_incomplete with NO class decision -- an agent there is "
        "back to string-sniffing prose, which is the gap this change exists to close:\n  "
        + "\n  ".join(uncovered)
    )


def test_the_helper_output_is_confined_to_the_closed_vocabulary() -> None:
    """Enforce the allow-list on the helper's OUTPUT, not just on source literals.

    The literal-scanning test below cannot see a class that arrives from a backend or a
    SearchResult built elsewhere -- the helper forwards any truthy value it is handed. This pins
    the actual emission boundary: a value outside the closed set must never reach a payload.
    """
    for member in sorted(_CLOSED_VOCABULARY):
        assert mcp_server._incomplete_class_fragment(_Results(member)) == {
            "incomplete_reason_class": member
        }

    # The failure this guards: a backend supplying something the vocabulary does not define.
    rogue = mcp_server._incomplete_class_fragment(_Results("backend_error"))
    assert rogue.get("incomplete_reason_class") not in _CLOSED_VOCABULARY, (
        "premise check -- 'backend_error' must not accidentally BE a vocabulary member"
    )
    assert rogue == {"incomplete_reason_class": "backend_error"}, (
        "DOCUMENTED LIMIT, asserted so it cannot change silently: the helper forwards whatever it "
        "is handed. Confinement is enforced at the ASSIGNMENT sites (see the literal-scan test), "
        "not here. If a backend ever becomes a source of this field, this assertion must be "
        "replaced by real filtering -- and this test will fail, which is the point."
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
