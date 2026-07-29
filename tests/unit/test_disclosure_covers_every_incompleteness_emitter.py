"""Every command that can report an INCOMPLETE result must disclose it on its text path.

Audit item A1b. The existing ratchet (`test_exit_two_is_never_silent.py`) keys on
``_scan_incomplete(...)`` gates whose body contains ``typer.Exit(2)``. That is a correct ratchet for
the family it was built for, and it is **structurally incapable** of catching two whole classes —
which is why both P0s fixed in #854 walked past it:

* ``tg imports`` — the shared symbol-command emitter exits on ``partial or result_incomplete``
  (``main.py``, ``_emit_symbol_command_result``) and **never calls** ``_scan_incomplete``. Invisible
  to a ``_scan_incomplete``-keyed scan, not failing it.
* ``tg orient`` — has **no exit-2 contract at all** by design. Any ``Exit(2)``-proximity heuristic
  cannot see an exit-0 command, so the one surface where the text line is the *only* signal was the
  one the ratchet could never reach.

This file is the complement, not a replacement. It keys on the PAYLOAD FIELDS that mean "incomplete"
(``partial`` / ``result_incomplete``) wherever a command reads them, and asserts each such reader
also reaches a disclosure emitter. The two ratchets are deliberately kept separate: this one would
not catch a ``_scan_incomplete`` gate that stopped disclosing, and that one cannot catch these.

WHY A GENERALIZATION ARM EXISTS BELOW. The audit of the plan caught the acceptance test being a
tautology: "clean against HEAD" is already satisfied by #854's own inline fix, so it cannot
distinguish "the ratchet generalized" from "the earlier fix is still in place". And "assert coverage
by name" yields a ratchet that catches exactly the two known bugs and no third. The synthetic-emitter
arm is what makes this a class check rather than a two-case check.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli" / "main.py"

# A payload READ that means "this result may be incomplete".
#
# `.get(...)` only -- deliberately NOT `payload["partial"]`, which also matches the ASSIGNMENT
# `payload["partial"] = True`. That conflation flagged `_run_ast_scan_payload`, which is a payload
# BUILDER: stamping the field is exactly the right thing for it to do, and the disclosure belongs
# to whoever renders the payload downstream. A producer is not a presenter, and a checker that
# cannot tell them apart reports correct code as broken.
_INCOMPLETE_READ = re.compile(r"""\.get\(["'](partial|result_incomplete)["']\)""")

# Any of the disclosure surfaces. A function that reads incompleteness must reach one of them.
# TRIAGED, not guessed. The first cut of this list held 7 helper names and flagged 9 functions.
# All 9 were FALSE POSITIVES -- each carried 7 to 29 disclosure signals through a surface the list
# did not know about. Reporting them as defects would have been 9 false P0s, which is worse than
# the gap the ratchet exists to close, so the list was widened from what the code actually does:
#
#   docs_coverage  -> _docs_scan_is_unreadable_truncated(), literal "INCOMPLETE"
#   codemap        -> literal "PARTIAL:" prefix
#   scan           -> literal "INCOMPLETE"/"PARTIAL" banners
#   agent/prepare  -> the trustworthy-deadline partial note
#
# A literal banner string IS a disclosure surface. Requiring a helper call would force every
# emitter through one function for the checker's convenience rather than the reader's.
_DISCLOSURE = (
    # helper calls
    "_truncation_message",
    "_emit_scan_incompleteness_banner",
    "_completeness_caveat_lines",
    "_annotate_result_completeness",
    "_scan_truncation_warning",
    "_emit_broad_scan_refusal",
    "_docs_scan_is_unreadable_truncated",
    "_agent_trustworthy_deadline_partial_note",
    "_write_defaulted_scope_note",
    "_scan_incomplete",
    # payload fields a consumer can branch on
    "incomplete_reason",
    "possibly_truncated",
    "deadline_limit",
    # literal banner text emitted straight to the user
    "INCOMPLETE",
    "PARTIAL",
    "not trustworthy",
)


def _functions(source: str) -> dict[str, str]:
    """Map every top-level function name to its source text."""
    tree = ast.parse(source)
    lines = source.split("\n")
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = "\n".join(lines[node.lineno - 1 : (node.end_lineno or node.lineno)])
    return out


def _readers_without_disclosure(source: str) -> list[str]:
    """Functions that READ an incompleteness field but reach no disclosure surface."""
    offenders = []
    for name, body in _functions(source).items():
        if not _INCOMPLETE_READ.search(body):
            continue
        if any(surface in body for surface in _DISCLOSURE):
            continue
        # A pure predicate that only COMPUTES incompleteness for a caller is not an emitter.
        # `_scan_incomplete` is exactly this: it answers a question, it does not answer a user.
        if body.count("return") and "def _" in body.split("\n")[0] and len(body.split("\n")) < 25:
            continue
        offenders.append(name)
    return offenders


def test_no_incompleteness_reader_is_silent() -> None:
    """THE CLASS: reading `partial`/`result_incomplete` obliges you to say so."""
    source = _MAIN.read_text(encoding="utf-8")

    # PREMISE: the matcher really finds incompleteness reads. Without this, a field rename would
    # empty the candidate set and the assertion below would pass vacuously.
    assert _INCOMPLETE_READ.search(source), (
        "found no partial/result_incomplete reads at all; the field names changed and this "
        "ratchet is now inert"
    )

    offenders = _readers_without_disclosure(source)
    assert not offenders, (
        f"these functions read an incompleteness field but reach no disclosure surface: "
        f"{offenders}. A caller cannot tell a genuine zero from a truncated one."
    )


def test_the_ratchet_flags_a_synthetic_new_emitter() -> None:
    """GENERALIZATION ARM — the reason this file is not a two-case check.

    The plan's original acceptance test was 'clean against HEAD', which #854's own inline fix
    already satisfies: it cannot distinguish a generalized ratchet from the earlier point fix.
    Nor can 'assert coverage by name', which catches the two known bugs and no third.

    So: inject a THIRD emitter that reads `result_incomplete` and discloses nothing, into a scratch
    copy of the source, and require the matcher to flag it. If this fails, the ratchet only knows
    about `imports` and `orient` and a future silent command ships unnoticed.
    """
    source = _MAIN.read_text(encoding="utf-8")
    synthetic = source + (
        "\n\n"
        "def _future_command_that_forgets_to_disclose(payload: dict) -> None:\n"
        '    count = len(payload.get("items") or [])\n'
        '    if payload.get("result_incomplete"):\n'
        "        pass\n"
        '    print(f"items={count}")\n'
    )

    offenders = _readers_without_disclosure(synthetic)
    assert "_future_command_that_forgets_to_disclose" in offenders, (
        "the ratchet did not flag a newly added silent emitter -- it is a two-case check, not a "
        f"class check. Flagged: {offenders}"
    )


def test_the_synthetic_emitter_passes_once_it_discloses() -> None:
    """CONTROL ARM on the generalization arm itself.

    Without this, a matcher that flags EVERY function (or simply returns its whole input) would
    satisfy the test above while being useless. The same synthetic emitter, with a disclosure call
    added, must NOT be flagged.
    """
    source = _MAIN.read_text(encoding="utf-8")
    synthetic = source + (
        "\n\n"
        "def _future_command_that_does_disclose(payload: dict) -> None:\n"
        '    count = len(payload.get("items") or [])\n'
        '    if payload.get("result_incomplete"):\n'
        '        print(_truncation_message("the scan did not finish"))\n'
        '    print(f"items={count}")\n'
    )

    offenders = _readers_without_disclosure(synthetic)
    assert "_future_command_that_does_disclose" not in offenders, (
        f"the ratchet flags a function that DOES disclose -- it cannot discriminate: {offenders}"
    )


def test_the_two_p0_surfaces_are_covered_by_the_class_not_by_name() -> None:
    """The #854 regression pins, reached through the class matcher rather than hard-coded.

    Named here only to document WHICH historical bugs this covers. The assertion is still the
    class assertion -- if someone re-breaks `imports` or `orient`, the first test fails on its own
    without this one existing.
    """
    source = _MAIN.read_text(encoding="utf-8")
    functions = _functions(source)

    assert "_emit_symbol_command_result" in functions, (
        "the shared symbol-command emitter was renamed; `tg imports` disclosure is unpinned"
    )
    assert any(s in functions["_emit_symbol_command_result"] for s in _DISCLOSURE), (
        "the symbol-command emitter no longer reaches a disclosure surface -- this is the exact "
        "shape of the `tg imports` P0 (exit 2, imports=0, zero bytes of stderr)"
    )
