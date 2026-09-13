"""`unreadable_path` must OUTRANK a budget cause when both fire (docs/CONTRACTS.md:24-27).

FIXTURE TRAP -- these shapes are not the obvious ones (cli/incompleteness.py:169-184):
  * `unreadable_paths` must be a DICT with a truthy `count`; a LIST is silently ignored.
  * `scan_limit` keys are `possibly_truncated`/`truncation_cause`/`budget_remediable`.
With the wrong shapes every arm returns cause=None, the combined arm looks benign, and this
finding reads as REFUTED. The isolation controls below are what expose a fixture that never
applied -- do not delete them to "simplify" the file.
"""

from __future__ import annotations

from tensor_grep.cli.incompleteness import unified_incomplete_envelope

UNREADABLE = {"count": 1, "paths": ["/some/unreadable/subtree"]}
SCAN_LIMIT = {
    "possibly_truncated": True,
    "truncation_cause": "project-files",
    "budget_remediable": True,
}


def test_control_unreadable_alone_is_reported():
    """If this fails the fixture never applied; nothing else here is interpretable."""
    out = unified_incomplete_envelope({"unreadable_paths": UNREADABLE})
    assert out["status"] is True
    assert out["cause"] == "unreadable_path"
    assert out["budget_remediable"] is False


def test_control_budget_alone_is_reported():
    """Pins the budget path so the combined arm reads as a PRIORITY defect, not a broken signal."""
    out = unified_incomplete_envelope({"scan_limit": SCAN_LIMIT})
    assert out["status"] is True
    assert out["cause"] == "project-files"
    assert out["budget_remediable"] is True


def test_unreadable_path_outranks_budget_when_both_fire():
    """The defect: the budget cause wins, handing the caller a knob that cannot help."""
    out = unified_incomplete_envelope({"scan_limit": SCAN_LIMIT, "unreadable_paths": UNREADABLE})
    assert out["cause"] == "unreadable_path"
    assert out["budget_remediable"] is False


def test_unreadable_path_outranks_an_explicit_incomplete_reason():
    """`ripgrep_backend.py:200` sets `incomplete_reason` alongside
    `incomplete_reason_class="timeout"`, so a payload can carry an explicit timeout-family reason
    AND unreadable paths. The contract says unreadable outranks EVERY budget cause, so it must
    win here too -- an ordering that merely hoists unreadable above `scan_limit` does not.
    """
    out = unified_incomplete_envelope({
        "incomplete_reason": "rg timed out after 30s",
        # WITHOUT this class the payload never establishes the reason IS a budget cause, and
        # the test would be asserting an over-reach rather than the contract. v2 omitted it.
        "incomplete_reason_class": "timeout",
        "unreadable_paths": UNREADABLE,
    })
    assert out["cause"] == "unreadable_path"
    assert out["budget_remediable"] is False


def test_a_non_budget_explicit_reason_is_PRESERVED():
    """The contract's scope is budget causes ONLY -- this is the control on the fix's reach.

    `docs/CONTRACTS.md:24-27` says unreadable_path outranks every BUDGET cause. It says nothing
    about non-budget reasons, so a caller that explicitly reported e.g. a parser limitation must
    keep it. Without this arm, a fix that overwrites EVERY explicit reason looks correct.
    """
    out = unified_incomplete_envelope({
        "incomplete_reason": "language_not_parser_backed",
        "incomplete_reason_class": "unsupported_language",
        "unreadable_paths": UNREADABLE,
    })
    assert out["cause"] == "language_not_parser_backed"
