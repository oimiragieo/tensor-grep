"""Gate for the W1 disposition ledger (docs/audits/2026-08-20-handler-dispositions.json).

WHY THIS EXISTS. `test_silent_failure_hardening.py`'s ratchet is arithmetically satisfiable by a
no-op audit: classify all formerly-excluded handlers as INTENTIONAL-BOUNDARY, raise the ceiling
by the full delta, suite green, nothing proved (docs/plans/2026-08-20-worldclass-closeout-plan.md,
W1.4). This gate makes the classification an auditable, machine-checked artifact instead of prose.

IDENTITY vs ADVISORY (W1.4). A record's identity is the triple
``(module, enclosing_symbol, handler_index_within_symbol)``. ``lineno`` is ADVISORY ONLY -- never
part of identity or uniqueness -- so an ordinary line-shifting edit elsewhere in the file cannot
orphan a record or manufacture a spurious duplicate. It is still checked, but only for
plausibility (does it fall inside the enclosing symbol's own span).

SCOPE OF THE COMPLETENESS CHECK. The ledger is append-only and W1's four slices merge serially, so
a full-population (128-record) completeness check would be unsatisfiable at every intermediate
merge -- W1-d only ever disposes of 2. The rule is therefore: at any commit, every broad handler in
the modules that have been REMOVED from `_EXCLUDED_MODULES` so far (cumulatively) has exactly one
ledger record, and no record exists for a module still excluded. Both directions are asserted.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "docs" / "audits" / "2026-08-20-handler-dispositions.json"
GATE_PATH = REPO_ROOT / "tests" / "unit" / "test_silent_failure_hardening.py"
PY_SRC = REPO_ROOT / "src" / "tensor_grep"

# The full historical population of modules excluded from the ratchet at the start of W1
# (docs/plans/2026-08-20-worldclass-closeout-plan.md, W1.1). This set never grows -- a module can
# only ever be REMOVED from `_EXCLUDED_MODULES` by a slice auditing it, never added back. It is
# the universe the "cumulative so far" rule is computed against.
_ORIGINAL_EXCLUDED_MODULES = frozenset({
    "cli/main.py",
    "cli/repo_map.py",
    "cli/mcp_server.py",
    "cli/mcp_rewrite_tools.py",
    "cli/mcp_audit_tools.py",
    "cli/mcp_symbol_tools.py",
    "cli/repo_map_cache.py",
    "cli/repo_map_lang_java.py",
    "cli/repo_map_lang_js.py",
    "cli/repo_map_lang_python.py",
    "cli/repo_map_lang_rust.py",
    "cli/repo_map_output_budget.py",
    "cli/repo_map_regex_fallback.py",
    "cli/_main_binding.py",
    "cli/ast_scan.py",
    "cli/doctor_payload.py",
    "cli/doctor_report.py",
    "cli/native_frontdoor.py",
    "cli/windows_launcher.py",
})

# Backend modules never lived in _ORIGINAL_EXCLUDED_MODULES (W1 carve-out was CLI-only).
# Completeness for backends is gated by this explicit set, grown only when a slice
# appends matching ledger rows (HANDLER-CENSUS-W2).
_EXPLICIT_AUDITED_MODULES = frozenset({
    "backends/cpu_backend.py",
    "backends/ripgrep_backend.py",
    "backends/ast_backend.py",
    "backends/ast_wrapper_backend.py",
    "backends/rust_backend.py",
    "backends/stringzilla_backend.py",
})

_VALID_CATEGORIES = frozenset({"SILENT-SWALLOW", "LOGGED-DEGRADE", "INTENTIONAL-BOUNDARY"})


def _current_excluded_modules() -> frozenset[str]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_disposition_gate_source", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset(module._EXCLUDED_MODULES)


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def _enclosing_symbol_node(
    tree: ast.Module, handler: ast.ExceptHandler
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Innermost enclosing def node, or None if the handler sits at module level. NOTE: a
    module may define more than one function with the SAME NAME at different scopes (e.g. a
    private `_walk` closure repeated per outer function) -- callers must key off the NODE, not
    just the name, or two distinct handlers collapse onto one identity."""

    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= handler.lineno <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def _real_handlers_for_module(relative_path: str) -> list[tuple[str, int, int, int, int]]:
    """Returns [(enclosing_symbol, handler_index_within_symbol, lineno, span_start, span_end),
    ...] for every broad handler currently in `relative_path`, in source order. span_start/
    span_end are the ENCLOSING SYMBOL's own node span (module span if handler is top-level),
    resolved from the same node the handler was matched against -- never re-looked-up by name,
    because a module can define the same function name more than once at different scopes."""

    path = PY_SRC / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    per_symbol_counter: dict[str, int] = {}
    result: list[tuple[str, int, int, int, int]] = []
    handlers: list[ast.ExceptHandler] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handlers.extend(h for h in node.handlers if _is_broad_handler(h))
    for handler in sorted(handlers, key=lambda h: h.lineno):
        enclosing = _enclosing_symbol_node(tree, handler)
        if enclosing is None:
            symbol = "<module>"
            span_start = 1
            span_end = (tree.body[-1].end_lineno if tree.body else 1) or 1
        else:
            symbol = enclosing.name
            span_start = enclosing.lineno
            span_end = enclosing.end_lineno or enclosing.lineno
        idx = per_symbol_counter.get(symbol, 0)
        per_symbol_counter[symbol] = idx + 1
        result.append((symbol, idx, handler.lineno, span_start, span_end))
    return result


def _load_ledger() -> list[dict]:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def _audited_modules_so_far() -> frozenset[str]:
    return (_ORIGINAL_EXCLUDED_MODULES - _current_excluded_modules()) | _EXPLICIT_AUDITED_MODULES


def test_ledger_completeness_scoped_to_audited_modules() -> None:
    """Every broad handler in a module REMOVED from _EXCLUDED_MODULES so far has exactly one
    ledger record, and no record exists for a module still excluded (both directions)."""

    ledger = _load_ledger()
    audited = _audited_modules_so_far()

    ledger_identities = {
        (r["module"], r["enclosing_symbol"], r["handler_index_within_symbol"]) for r in ledger
    }

    expected_identities: set[tuple[str, str, int]] = set()
    for module in audited:
        for symbol, idx, _lineno, _start, _end in _real_handlers_for_module(module):
            expected_identities.add((module, symbol, idx))

    missing = expected_identities - ledger_identities
    assert not missing, f"audited handlers with no ledger record: {sorted(missing)}"

    still_excluded = _current_excluded_modules()
    unaudited_records = {ident for ident in ledger_identities if ident[0] in still_excluded}
    assert not unaudited_records, (
        f"ledger has records for modules not yet removed from _EXCLUDED_MODULES: {sorted(unaudited_records)}"
    )


def test_identity_scheme_has_no_same_named_sibling_ambiguity() -> None:
    """W1-a: the CHECKED INVARIANT behind the identity scheme, resolved rather than assumed.

    THE AMBIGUITY W1-d FLAGGED. ``handler_index_within_symbol`` is counted per SYMBOL NAME, not
    per symbol NODE (see ``_real_handlers_for_module``'s ``per_symbol_counter``). If one module
    defines two functions with the SAME NAME -- e.g. a ``_walk`` closure repeated inside two
    different outer functions, which the lang modules already do -- and BOTH hold broad
    handlers, their records share a name and the index becomes a running count ACROSS both
    definitions. Nothing collides and nothing is orphaned (the indices stay distinct and
    ``_real_handlers_for_module`` carries each handler's own enclosing span), but the record no
    longer names WHICH definition it describes, and inserting a broad handler into the earlier
    definition silently RENUMBERS every later one, re-pointing existing records at different
    handlers with the completeness and locatability checks still green.

    THE RESOLUTION CHOSEN, and why. Not a schema change: deepening the key to a qualified path
    would force the two merged W1-d records to be re-derived and would move the identity scheme
    while three slices are still mid-flight against it. Instead the PRECONDITION under which
    the flat key is unambiguous is asserted here, for the audited population only, so the day it
    stops holding this test fails and NAMES the module -- rather than a later slice silently
    inheriting an ambiguous key. If it ever fires, deepen the key then, with the whole ledger
    migrated in one commit.

    NOTE this deliberately scopes to AUDITED modules. Same-named siblings elsewhere in the tree
    are harmless until that module enters the ledger, and asserting over the whole package would
    make an unrelated module's refactor fail a gate about ledger identity.
    """

    offenders: list[str] = []
    for module in sorted(_audited_modules_so_far()):
        path = PY_SRC / module
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        holders: dict[str, list[int]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            has_broad = any(
                _is_broad_handler(handler)
                for sub in ast.walk(node)
                if isinstance(sub, ast.Try)
                for handler in sub.handlers
            )
            if has_broad:
                holders.setdefault(node.name, []).append(node.lineno)

        for name, linenos in sorted(holders.items()):
            if len(linenos) > 1:
                offenders.append(f"{module}::{name} defined at lines {sorted(linenos)}")

    assert not offenders, (
        "the flat (module, enclosing_symbol, handler_index_within_symbol) identity key is "
        "AMBIGUOUS for these audited symbols -- two same-named definitions each hold at least "
        "one broad handler, so the index runs across both and a future insertion renumbers "
        "records onto the wrong handlers. Deepen the identity key to a qualified symbol path "
        "and migrate the whole ledger in one commit; do not hand-patch indices. "
        f"Offenders: {offenders}"
    )

    # Positive control: the walk above must have SEEN the audited population, or an empty
    # `offenders` list would be a scan that never ran rather than a clean bill (AGENTS.md:
    # "no zero is reported without a control").
    audited = _audited_modules_so_far()
    if audited:
        seen = sum(len(_real_handlers_for_module(m)) for m in audited)
        assert seen > 0, "invariant scan saw zero handlers across audited modules -- dead scan"


def test_ledger_uniqueness() -> None:
    """No IDENTITY triple appears twice. lineno is NOT consulted -- two records that agree on
    (module, enclosing_symbol, handler_index_within_symbol) are the same handler even if their
    lineno fields differ."""

    ledger = _load_ledger()
    seen: set[tuple[str, str, int]] = set()
    dupes = []
    for record in ledger:
        ident = (
            record["module"],
            record["enclosing_symbol"],
            record["handler_index_within_symbol"],
        )
        if ident in seen:
            dupes.append(ident)
        seen.add(ident)
    assert not dupes, f"duplicate identity triples: {dupes}"


def test_ledger_locatability() -> None:
    """Every record's IDENTITY triple resolves to a real broad handler in the current tree, and
    its advisory lineno falls within the enclosing symbol's span."""

    ledger = _load_ledger()
    for record in ledger:
        module = record["module"]
        symbol = record["enclosing_symbol"]
        idx = record["handler_index_within_symbol"]
        real = _real_handlers_for_module(module)
        matches = [h for h in real if h[0] == symbol and h[1] == idx]
        assert matches, f"{module}: no real broad handler at ({symbol!r}, idx={idx})"
        _, _, _, span_start, span_end = matches[0]
        assert span_start <= record["lineno"] <= span_end, (
            f"{module}: lineno {record['lineno']} outside {symbol!r}'s span [{span_start}, {span_end}]"
        )


def test_ledger_vocabulary() -> None:
    ledger = _load_ledger()
    for record in ledger:
        assert record["category"] in _VALID_CATEGORIES, (
            f"{record['module']}:{record['lineno']} has invalid category {record['category']!r}"
        )


def test_ledger_evidence_and_reason_non_empty_and_distinct() -> None:
    ledger = _load_ledger()
    for record in ledger:
        evidence = record.get("evidence", "")
        reason = record.get("reason", "")
        assert evidence.strip(), f"{record['module']}:{record['lineno']} has empty evidence"
        assert reason.strip(), f"{record['module']}:{record['lineno']} has empty reason"
        assert evidence != reason, (
            f"{record['module']}:{record['lineno']} evidence == reason (copy-paste)"
        )


# ---------------------------------------------------------------------------
# Perturbation arms (W1.4): each of these synthesizes a broken ledger IN MEMORY and asserts the
# relevant check catches it, using the same predicates the real tests use above. They do not
# touch the committed ledger file -- see the PR body for the ON-DISK perturbation run (delete /
# duplicate / mislocate / invalid-category a record, observe the real pytest failure, revert).
# ---------------------------------------------------------------------------


def test_perturbation_arm_omission_is_caught() -> None:
    ledger = _load_ledger()
    assert len(ledger) >= 1
    broken = ledger[1:]  # drop the first record
    audited = _audited_modules_so_far()
    expected_identities: set[tuple[str, str, int]] = set()
    for module in audited:
        for symbol, idx, _lineno, _start, _end in _real_handlers_for_module(module):
            expected_identities.add((module, symbol, idx))
    broken_identities = {
        (r["module"], r["enclosing_symbol"], r["handler_index_within_symbol"]) for r in broken
    }
    missing = expected_identities - broken_identities
    assert missing, (
        "omission arm did not produce a missing identity -- perturbation is not discriminating"
    )


def test_perturbation_arm_duplicate_is_caught() -> None:
    ledger = _load_ledger()
    assert len(ledger) >= 1
    broken = [*ledger, dict(ledger[0])]
    seen: set[tuple[str, str, int]] = set()
    dupes = []
    for record in broken:
        ident = (
            record["module"],
            record["enclosing_symbol"],
            record["handler_index_within_symbol"],
        )
        if ident in seen:
            dupes.append(ident)
        seen.add(ident)
    assert dupes, (
        "duplicate arm did not produce a duplicate identity -- perturbation is not discriminating"
    )


def test_perturbation_arm_stale_location_is_caught() -> None:
    ledger = _load_ledger()
    assert len(ledger) >= 1
    record = dict(ledger[0])
    real = _real_handlers_for_module(record["module"])
    matches = [
        h
        for h in real
        if h[0] == record["enclosing_symbol"] and h[1] == record["handler_index_within_symbol"]
    ]
    assert matches
    _, _, _, span_start, span_end = matches[0]
    record["lineno"] = span_end + 1000  # shift far outside the symbol's span
    assert not (span_start <= record["lineno"] <= span_end), (
        "stale-location arm's shifted lineno accidentally still falls in span -- perturbation is not discriminating"
    )


def test_perturbation_arm_invalid_category_is_caught() -> None:
    ledger = _load_ledger()
    assert len(ledger) >= 1
    record = dict(ledger[0])
    record["category"] = "probably-fine"
    assert record["category"] not in _VALID_CATEGORIES, (
        "invalid-category arm's value is accidentally valid -- perturbation is not discriminating"
    )


def test_no_zero_length_ledger_without_a_reason() -> None:
    """AGENTS.md's 'no zero without a control': if nothing has been audited yet, the ledger must
    still exist (possibly empty), not be absent -- absence and 'audited nothing' must be
    distinguishable."""

    assert LEDGER_PATH.exists(), "ledger file must exist even if empty"
    ledger = _load_ledger()
    audited = _audited_modules_so_far()
    if not audited:
        assert ledger == []
    else:
        assert len(ledger) > 0 or all(not _real_handlers_for_module(m) for m in audited), (
            "modules audited but ledger empty and at least one audited module has real handlers"
        )
