"""Task #307-C: every CLI incompleteness cause carries a machine-branchable remediability flag.

The knowledge already existed -- but only inside
`tests/unit/test_truncation_cause_vocabulary_ratchet.py`, so CI could branch on it and the SHIPPED
CLI could not. `budget_remediable` was emitted by exactly ONE surface (the MCP `scan_limit` object,
task #283) while every CLI route stamped a cause with no "is a retry worth it?" signal. A consumer
that cannot tell `raise --max-repo-files` from `you will never read that directory` either retries
forever or gives up on a fixable scan.

Every assertion here is paired with its opposite arm -- an allow-list that returns True for
everything, or False for everything, is a brick, and a brick passes a one-sided test.
"""

from __future__ import annotations

from typing import Any

from tensor_grep.cli.incompleteness import budget_remediable


def test_budget_remediable_discriminates_between_the_two_cause_families() -> None:
    # TREATMENT: a budget cap or a clock -- raising the knob genuinely fixes these.
    assert budget_remediable("project-files") is True
    assert budget_remediable("max-scan-entries") is True
    assert budget_remediable("deadline") is True
    assert budget_remediable("timeout") is True
    assert budget_remediable("scan_limit") is True

    # CONTROL: no budget value makes a permission-denied path readable. If this arm ever returns
    # True the helper has become a brick that says "just raise the limit" to everything, which is
    # the wrong-knob advice #283 exists to prevent.
    assert budget_remediable("unreadable-path") is False
    assert budget_remediable("unreadable_path") is False


def test_budget_remediable_fails_closed_on_anything_it_was_not_taught() -> None:
    """ALLOW-LIST, not deny-list (#282).

    A deny-list ("return False only for unreadable-path") fails OPEN on every cause a future
    author adds -- the new value would be advertised as budget-remediable purely because nobody
    updated this function. These arms pin the safe direction.
    """
    assert budget_remediable(None) is False
    assert budget_remediable("unknown") is False
    assert budget_remediable("self_verify") is False
    assert budget_remediable("a-cause-nobody-has-invented-yet") is False
    assert budget_remediable("") is False


def _scan_limit(payload: dict[str, Any]) -> dict[str, Any]:
    scan = payload["scan_limit"]
    assert isinstance(scan, dict)
    return scan


def test_inventory_emits_the_flag_only_when_truncated() -> None:
    from tensor_grep.cli import inventory as inventory_mod

    source = inventory_mod.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()

    # PREMISE: the emitter still gates the flag on `possibly_truncated`. If that gate is removed,
    # a COMPLETE inventory starts carrying the key and the byte-identity promise below is void --
    # so this must fail loudly rather than let the claim rot.
    assert '"budget_remediable": budget_remediable(truncation_cause)' in text
    assert "if possibly_truncated" in text

    # CLAIM: the helper is imported from the ONE shared definition, not re-implemented locally.
    # A second copy is how CLI and MCP drift into giving opposite advice for the same cause.
    assert "from tensor_grep.cli.incompleteness import budget_remediable" in text


def test_docs_coverage_emits_the_flag_at_both_scan_limit_sites() -> None:
    from tensor_grep.cli import docs_coverage as docs_mod

    source = docs_mod.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()

    # docs-coverage builds a scan_limit in TWO places (the coverage report and --stale mode). A
    # fix applied to one arm and not its twin is the recurring defect of this whole campaign, so
    # the count is the assertion.
    assert text.count('"budget_remediable": budget_remediable(truncation_cause)') == 2
    assert "from tensor_grep.cli.incompleteness import budget_remediable" in text


def test_the_ratchet_and_the_product_agree_on_which_causes_are_remediable() -> None:
    """The ratchet test owned this fact; the product now does. Pin them together.

    Before #307-C the mapping lived ONLY in the ratchet module, which is why the CLI could not
    branch on it. Now that the product owns it, the ratchet's copy must agree -- otherwise we have
    re-created the drift in the opposite direction.
    """
    # Loaded by PATH, not by `from tests.unit...` -- `tests/` is not a package, so the import form
    # raises ModuleNotFoundError and the cross-check would be quietly lost to a skip or an error.
    import importlib.util
    from pathlib import Path

    ratchet_path = Path(__file__).with_name("test_truncation_cause_vocabulary_ratchet.py")
    assert ratchet_path.is_file(), f"ratchet module moved: {ratchet_path}"
    spec = importlib.util.spec_from_file_location("_ratchet_for_parity", ratchet_path)
    assert spec is not None and spec.loader is not None
    ratchet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ratchet)

    known = ratchet.KNOWN_TRUNCATION_CAUSES
    non_remediable = ratchet.NON_BUDGET_REMEDIABLE
    # PREMISE: both sets are non-empty and the non-remediable set is a real subset. A vacuous loop
    # over an empty set would pass this test while checking nothing.
    assert known, "ratchet's KNOWN_TRUNCATION_CAUSES is empty -- the loop below would be vacuous"
    assert non_remediable and non_remediable < known

    for cause in known:
        expected = cause not in non_remediable
        assert budget_remediable(cause) is expected, (
            f"{cause!r}: ratchet says budget_remediable={expected}, product says "
            f"{budget_remediable(cause)} -- the two definitions have drifted"
        )
