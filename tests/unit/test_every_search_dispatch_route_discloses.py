"""Every SEARCH dispatch route must reach the defaulted-scope disclosure. Enumerated, not recalled.

THIS FILE IS A MECHANISM, NOT A REMINDER.

The rule "enumerate ALL dispatch routes before fixing a search-surface symptom" was written into
`AGENTS.md` and then violated FOUR times in a single session, three of them AFTER the rule was
written:

    bare text            -> bootstrap rg passthrough        fixed #857
    --ast/--rank/...     -> Python CLI is_empty branch       fixed #862
    --json stderr note   -> bootstrap native delegation      fixed #862 (later)
    --json BODY field    -> native delegation, again         STILL OPEN (task #26)

Each fix was correct and each looked like it closed the feature, because the next report happened
to take a different route. A live dogfood reported "bare search is silent" across FOUR consecutive
releases while each report described a different dispatch path.

Per `convert-a-repeatedly-violated-rule-into-a-mechanism`: the second violation is the signal, and
restating a rule has near-zero yield. The rule's author kept breaking it, which is the documented
signature of a missing affordance rather than a discipline problem. The skill's own receipt names
the remedy that works for exactly this shape -- a claim of the form "all call sites are guarded",
wrong five consecutive times, was closed by **a test that enumerates the population**.

So this test enumerates the population: every terminal exit in `main_entry`'s search dispatch. A new
route added later fails HERE, at the point of addition, rather than in a dogfood report four
releases later.

DELIBERATELY NOT a behavioural test. Exercising all three routes needs the real native binary, real
ripgrep, and a large corpus; that test would be slow, platform-fragile, and would itself only cover
the routes someone remembered to invoke -- reproducing the original defect. Enumerating the source
population is what makes coverage total rather than sampled.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

_BOOTSTRAP = Path(__file__).resolve().parents[2] / "src" / "tensor_grep" / "cli" / "bootstrap.py"

# The guard every search-terminating route must reach, in one form or another.
_DISCLOSURE = ("_write_defaulted_scope_note", "_defaulted_scope_note")

# Exits that terminate a SEARCH. Others in main_entry (help, version, a non-search subcommand
# forwarded to the native binary) legitimately have nothing to disclose.
_SEARCH_DISPATCH = ("_run_rg_passthrough", "_run_native_tg_search")


def _main_entry_source() -> str:
    """`main_entry`'s source with COMMENT LINES STRIPPED.

    The first cut of this mechanism matched a fixed character window from the dispatch call, and
    immediately produced a FALSE POSITIVE: the native route IS guarded, but a 12-line comment
    explaining WHY sits between the call and the guard and pushed it past the window. A gate that
    fires on correct code is worse than no gate -- it teaches everyone to reach for `--no-verify`,
    and that habit discredits every other gate in the repo.

    Comments are stripped rather than the window widened: a wider window would eventually span into
    an UNRELATED adjacent route and silently accept an unguarded one. Well-documented code must not
    be penalised, and this repo deliberately writes long rationale comments at exactly these sites.
    """
    from tensor_grep.cli import bootstrap

    source = inspect.getsource(bootstrap.main_entry)
    kept = [ln for ln in source.splitlines() if not ln.strip().startswith("#")]
    return "\n".join(kept)


def test_every_search_dispatch_exit_reaches_the_disclosure() -> None:
    """THE POPULATION CHECK. A new search route must not ship without a scope disclosure."""
    source = _main_entry_source()

    # PREMISE: the dispatchers are still named this. A rename would empty the population and make
    # the assertion below vacuously true -- the failure mode this whole file exists to prevent.
    found = [d for d in _SEARCH_DISPATCH if d in source]
    assert found == list(_SEARCH_DISPATCH), (
        f"search dispatchers renamed or removed; this mechanism is now blind. Found {found}, "
        f"expected {list(_SEARCH_DISPATCH)}. Re-derive the population before editing this test."
    )

    unguarded = []
    for dispatcher in _SEARCH_DISPATCH:
        # The window from the dispatch call to the end of its enclosing block. A route that
        # SystemExits without passing a disclosure guard is the defect.
        for match in re.finditer(re.escape(dispatcher) + r"\(", source):
            window = source[match.start() : match.start() + 900]
            if not any(guard in window for guard in _DISCLOSURE):
                line = source[: match.start()].count("\n") + 1
                unguarded.append(f"{dispatcher} (main_entry line ~{line})")

    assert not unguarded, (
        "these search dispatch routes exit without reaching the defaulted-scope disclosure: "
        f"{unguarded}. Every route needs its OWN emission -- there is no single chokepoint, which "
        "is why this symptom took four separate fixes. Add the guard at the new route, not only "
        "where the last one went."
    )


def test_the_guard_is_gated_on_both_conditions_at_every_route() -> None:
    """CONTROL ARM: a route that discloses UNCONDITIONALLY is also wrong.

    Without this, satisfying the test above by printing the note on every search would pass -- and
    a note that fires when the caller DID choose the scope, or when the search found matches, is
    noise that trains callers to ignore it.
    """
    source = _main_entry_source()

    for match in re.finditer(r"_write_defaulted_scope_note\(\)", source):
        window = source[max(0, match.start() - 700) : match.start()]
        assert "_search_args_include_explicit_path" in window, (
            "a disclosure call is not gated on the path being defaulted; it would fire on an "
            "explicitly scoped search"
        )
        assert re.search(r"exit_code\s*==\s*1", window), (
            "a disclosure call is not gated on a no-match exit; it would fire on a successful "
            "search"
        )


def test_the_mechanism_fires_on_a_synthetic_unguarded_route() -> None:
    """PROVE THE MECHANISM ITSELF, on the arm that matters.

    An untested gate is untested code, and a gate never watched failing is unproven. Inject a
    synthetic new dispatch route with no guard into a scratch copy and require the matcher to flag
    it. If this ever passes silently, the population check has gone inert and the next route ships
    unguarded exactly like the previous four.
    """
    synthetic = (
        "def main_entry():\n"
        "    if something:\n"
        "        raise SystemExit(_run_rg_passthrough(binary, args))\n"
    )

    unguarded = []
    for dispatcher in _SEARCH_DISPATCH:
        for match in re.finditer(re.escape(dispatcher) + r"\(", synthetic):
            window = synthetic[match.start() : match.start() + 900]
            if not any(guard in window for guard in _DISCLOSURE):
                unguarded.append(dispatcher)

    assert "_run_rg_passthrough" in unguarded, (
        "the matcher did not flag a synthetic unguarded route -- it is inert and would not have "
        "caught any of the four real violations"
    )


def test_the_mechanism_does_not_fire_on_a_guarded_route() -> None:
    """CONTROL ARM on the mechanism: it must discriminate, not flag everything."""
    synthetic = (
        "        exit_code = _run_rg_passthrough(binary, args)\n"
        "        if exit_code == 1 and not _search_args_include_explicit_path(args):\n"
        "            _write_defaulted_scope_note()\n"
        "        raise SystemExit(exit_code)\n"
    )

    match = re.search(re.escape("_run_rg_passthrough") + r"\(", synthetic)
    assert match
    window = synthetic[match.start() : match.start() + 900]
    assert any(guard in window for guard in _DISCLOSURE), (
        "the matcher flags a correctly-guarded route; it cannot discriminate and would be "
        "disabled within a week"
    )


def test_bootstrap_parses() -> None:
    """A shell-escaping accident wrote a literal newline into a string literal in this exact file
    twice in one session. Cheap syntax pin so a broken source file fails here rather than at the
    next import somewhere unrelated."""
    ast.parse(_BOOTSTRAP.read_text(encoding="utf-8"))
