"""opt10 campaign #3: bound the discarded ``_context_tests`` copy on the callers/impact/refs
paths (Part A) + add its missing ``--deadline`` gate (Part B).

Part A root cause: ``_build_context_pack_from_map`` (repo_map.py) slices its ``ranked_files``
list to ``test_source_files`` via an existing ``_test_source_limit`` parameter BEFORE calling
``_context_tests`` (the whole-repo test-relevance scorer whose ``_test_graph_score`` helper rebuilds
an aliases-by-file dict PER TEST -- O(len(tests) * len(source_files)), unbounded on a large repo).
``build_context_edit_plan_from_map`` already threads ``_test_source_limit=max_files`` at its call
site; ``build_symbol_impact_from_map``/``build_symbol_refs_from_map``/``build_symbol_callers_from_map``
did not, so their ``ranked_files`` (already query-relevance-filtered, but potentially large on a
broad query over a big repo) fed ``_context_tests`` fully unbounded even though:
  - refs/callers read ``context_payload["test_matches"]`` only via ``_ranking_quality``'s
    ``test_matches[:1]`` (repo_map.py's ``_ranking_quality``, reads ``[*file_matches[:2],
    *test_matches[:1]]``) -- the rest of the list is discarded.
  - impact re-derives its OWN ``test_matches``/``tests`` fields from a SEPARATE, independently
    computed ``related_tests`` (``_relevant_tests_for_symbol``), using ``context_payload`` only as a
    per-path score lookup (for whichever tests intersect) or, if that has nothing, an ordered
    ``fallback_tests=`` last resort.

The tests below prove the specific constant this PR ships (``_CONTEXT_TESTS_SOURCE_FILE_CEILING``,
== the already-established ``CALLER_SCAN_FILE_CEILING``) is a behavior no-op on a realistic
fixture (the "load-bearing" ranking-parity gate), AND separately prove the underlying bounding
MECHANISM has genuine teeth at a deliberately tight bound -- so the no-op result above is not
vacuous (the fixture and scorer really are sensitive to truncation; the shipped ceiling is just
generous enough never to trigger it in practice).

Part B: ``_context_tests`` had no ``deadline_monotonic``/``deadline_hit`` params at all, so its
``for current in tests:`` scan ran unconditionally to completion even under ``--deadline``. The
fix mirrors the identical per-item idiom every sibling loop in this module already uses (the
#691/#222-fixed loops in ``_relevant_tests_for_symbol``, ``_build_context_pack_from_map``'s own
symbol-scoring loop) -- checked at the iteration boundary, folding into the caller's existing
``partial``/``deadline_limit`` signal via the shared ``_DeadlineBreakFlag`` plumbing that already
exists at both of ``_context_tests``'s call sites.

PRE-FIX BASELINE MEASURED 2026-08-02 (orchestrator, before any merge) -- READ THIS FIRST.
Run against unpatched `main` source in the real venv: **12 failed / 6 passed.** "It went red" is
NOT the same as "the red arm is good", and this file needs work in both directions:

WEAK RED ARMS (11 of the 12). They fail with `AttributeError` because the module has no
`_CONTEXT_TESTS_SOURCE_FILE_CEILING` -- an ERROR while evaluating the assertion, not a
demonstration that the property is broken. A test that errors is not a red arm. Only
`test_callers_relevant_tests_for_symbol_call_site_receives_deadline` fails behaviourally
(`deadline_monotonic` is None; the observed kwargs dict is `{'raw_query': 'widget'}`).

VACUOUS-FOR-THEIR-NAME (3 of the 6 that PASS pre-fix):
`test_impact_context_tests_deadline_folds_into_partial` and its refs/callers twins. Proof they
cannot be observing what they are named for: pre-fix, `_context_tests` does not accept a
`deadline_monotonic` parameter AT ALL (params are source_files, tests, terms, imports_by_file,
file_distances, graph_scores, file_scores, raw_query) -- and the tests pass anyway. What they
actually observe is the PRE-EXISTING file-scoring deadline folding into `partial`, because
`_rig_deadline_via_score_file_path` patches `_score_file_path` GLOBALLY and that helper has five
call sites, so the budget is blown upstream before `_context_tests` is ever reached.

THE OBVIOUS FIX WAS TRIED AND MEASURED AND IT DOES NOT WORK -- do not re-attempt it. Scoping the
rig to `_test_graph_score` (which an AST walk confirms is called from `_context_tests` and NOWHERE
else) looks like it must isolate the mechanism. It does not: mutation asserted applied (3 scoped
call sites, 0 global), and all three tests STILL PASS on the pre-fix baseline. The reason is that
**24 functions in this module read `time.monotonic`**, so advancing the clock anywhere trips the
next DOWNSTREAM pre-existing deadline check, which sets `partial` on its own. Scoping the PATCH
SITE does not scope the OBSERVABLE.

What would actually discriminate: assert something only the new code path can produce -- an
attribution that names `_context_tests` as the stage that stopped -- rather than asserting the
shared `partial` / `deadline_exceeded` booleans, which every one of those 24 readers can set.

THE DESIGN ANSWER, AND IT NEEDS NO NEW CONVENTION (measured 2026-08-02). `deadline_limit` ALREADY
carries per-stage scanned/total counter pairs, five of them:
`files_scanned`/`files_total`, `caller_files_scanned`/`caller_files_total`,
`reference_files_scanned`/`reference_files_total`,
`importer_candidates_scanned`/`importer_candidates_total`,
`source_candidates_examined`/`source_candidates_total`.
There is NO test-candidate pair -- that absence is precisely the gap (AST-enumerated over every
string constant ending `_scanned`/`_total`/`_examined`, so it is a key census, not a grep).

So the fix should contribute `test_candidates_scanned` / `test_candidates_total` from
`_context_tests`, following the existing convention rather than inventing a `stage` field. The three
tests then assert the shape this suite ALREADY uses for exactly this purpose --
`tests/unit/test_repo_map_deadline.py` carries
`assert 0 < deadline_limit["files_scanned"] < deadline_limit["files_total"]  # some but not all`.
Nothing else scans test candidates, so that assertion CANNOT be satisfied by any of the other 24
clock readers -- which is what makes it able to fail, and what the `partial` assertion never was.

IMPLEMENTATION MAP (verified 2026-08-02 by two independent methods -- `tg callers` and an AST walk
-- which AGREE; that matters, because two methods sharing an assumption are one method run twice).
The counts must thread THREE levels, not two:

    build_symbol_{impact,refs,callers}_from_map   (3 entry points; each calls the next)
      -> build_context_pack_from_map              (public wrapper)
        -> _build_context_pack_from_map           (exactly ONE caller -- narrow, safe to widen)
          -> _context_tests                       (the loop; set total=len(tests), count iterations)

Payload assembly is ONE site per entry point -- each currently writes the bare
`payload["deadline_limit"] = {"deadline_exceeded": True}`; the new pair joins that dict there.

So: 1 new counter class + 3 signature widenings (optional kwarg, default None -- no existing caller
breaks) + 1 loop change + 3 payload assemblies, plus the `docs/CONTRACTS.md` entry. Mechanical, but
it edits core `repo_map.py` at 8 sites and wants the FULL affected suite rather than this one file,
so it belongs on a clean checkout.

BLAST RADIUS CHECKED 2026-08-02 -- carry the counts on a DEDICATED object, not the shared flag.
`tg callers src _DeadlineBreakFlag` reports **23 consumers across 6 modules** (`agent_capsule.py`,
`codemap.py`, `docs_coverage.py`, `inventory.py`, `main.py`, `repo_map.py`). That object is the
module's general-purpose mutable out-signal, so hanging `test_candidates_scanned`/`_total` on it
would widen a 23-consumer shared symbol for the benefit of ONE caller. Pass a small dedicated
counter object to `_context_tests` instead and read it in the three `build_symbol_*` call sites --
same out-signal idiom, blast radius of one. (This fork was invisible until the callers query was
run; it is exactly why the house rule is read-before-write on a shared symbol.)

GOVERNANCE CHECKED 2026-08-02 -- the new key is ADDITIVELY SAFE, no existing test breaks:
* 26 test files reference `deadline_limit`. **None** asserts an exclusive key set (no
  `set(deadline_limit)`, `.keys() ==`, or `sorted(deadline_limit)`), and the grep that found none is
  trustworthy because 24 files in the same suite DO use exact-set assertions -- so the pattern is
  real and findable, and its absence here is a measured negative rather than an unresolved one.
* `tests/unit/test_disclosure_covers_every_incompleteness_emitter.py` names `deadline_limit` as a
  CONTAINER, not per-key, so a new sub-key needs no registration in that census.
* `tests/unit/test_enterprise_docs_governance.py` asserts `"deadline_limit.files_scanned" in
  contracts` -- i.e. counter keys are expected to be DOCUMENTED. Add the new pair to
  `docs/CONTRACTS.md` in the same PR; that is a doc obligation, not a blocking pin.

The other three that pass pre-fix -- `..._test_source_limit_none_is_noop`,
`test_tight_bound_would_have_changed_consumed_output`,
`test_bound_covering_full_relevant_set_preserves_parity` -- are NOT swept: a "none is a no-op" test
SHOULD pass in both arms, that is its job. Classify each individually; the question is whether some
OTHER assertion already proves what it claims.

"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

import tensor_grep.cli.repo_map as repo_map

# ======================================================================================================
# Shared fixture: many source files that all score > 0 for the query "widget" (same file-name
# term, tied file_score -> deterministic alphabetical tie-break order in `ranked_files`), plus ONE
# test file whose import/graph-relatedness score is tied SPECIFICALLY to a "late" module (one that
# a too-tight `_test_source_limit` would truncate out of `source_files`).
# ======================================================================================================

_MODULE_COUNT = 30
_LATE_INDEX = 25  # "widget_core.py" + mod00..mod24 already fill 26 slots -> mod25 is slot #27


def _build_relevance_fixture(root: Path) -> Path:
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    tests_dir = project / "tests"
    tests_dir.mkdir(parents=True)
    for index in range(_MODULE_COUNT):
        name = f"widget_mod{index:02d}"
        (src / f"{name}.py").write_text(
            f'"""widget helper module {index}."""\n\n\n'
            f"def widget_helper_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    (src / "widget_core.py").write_text('def widget():\n    return "core"\n', encoding="utf-8")
    late_name = f"widget_mod{_LATE_INDEX:02d}"
    (tests_dir / f"test_uses_{late_name}.py").write_text(
        f"from src.{late_name} import widget_helper_{_LATE_INDEX}\n\n\n"
        f"def test_widget_helper_{_LATE_INDEX}():\n"
        f"    assert widget_helper_{_LATE_INDEX}() == {_LATE_INDEX}\n",
        encoding="utf-8",
    )
    (tests_dir / "test_unrelated.py").write_text(
        "def test_noop():\n    assert True\n", encoding="utf-8"
    )
    return project.resolve()


@pytest.fixture
def relevance_repo_map(tmp_path: Path) -> dict[str, Any]:
    project = _build_relevance_fixture(tmp_path)
    return repo_map.build_repo_map(str(project))


# ======================================================================================================
# Section 1 -- structural registration: the 3 blast-radius call sites now thread
# `_test_source_limit`. Multi-site registration (verify-plan-against-code Rule 6) fails QUIETLY if
# any one site is missed, so each function gets its own spy.
# ======================================================================================================


def _spy_on_build_context_pack_from_map(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    original = repo_map.build_context_pack_from_map

    def _spy(rm: Any, query: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return original(rm, query, **kwargs)

    monkeypatch.setattr(repo_map, "build_context_pack_from_map", _spy)
    return captured


def test_impact_deliberately_does_NOT_thread_test_source_limit(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """INVERTED 2026-08-02. This asserted that impact threads the ceiling. It does not, on
    purpose: the adversarial gate on #904 measured that bounding impact's source list downgrades
    the payload it RETURNS (score 23->2, association.confidence strong->weak), because
    `test_matches_by_path` is built from the context pack's `test_matches` and supplies those
    values. refs and callers keep the limit -- measured unchanged even at ceiling 1, because they
    really do read only `test_matches[:1]`.

    Kept as an explicit NEGATIVE assertion rather than deleted: a removed test is silent, and the
    next person optimising this path will find the same tempting `_test_source_limit=` line at the
    refs/callers call sites and reasonably assume impact was an oversight. This says it was not.
    See test_source_ceiling_changes_no_payload_at_the_BOUNDARY for the behavioural half.
    """
    captured = _spy_on_build_context_pack_from_map(monkeypatch)
    repo_map.build_symbol_impact_from_map(relevance_repo_map, "widget")
    assert captured.get("_test_source_limit") is None, (
        "impact must NOT bound the test-source list -- doing so silently weakens "
        "association.confidence on repos past the ceiling"
    )


def test_refs_threads_test_source_limit(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _spy_on_build_context_pack_from_map(monkeypatch)
    repo_map.build_symbol_refs_from_map(relevance_repo_map, "widget")
    assert captured.get("_test_source_limit") == repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING


def test_callers_threads_test_source_limit(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _spy_on_build_context_pack_from_map(monkeypatch)
    repo_map.build_symbol_callers_from_map(relevance_repo_map, "widget")
    assert captured.get("_test_source_limit") == repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING


def test_shipped_ceiling_reuses_caller_scan_file_ceiling() -> None:
    # Not a new magic number -- the same already-battle-tested ceiling refs/callers already accept
    # for their own reference/caller-scan file universe (`_cap_caller_scan_files`).
    assert repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING == repo_map.CALLER_SCAN_FILE_CEILING


# ======================================================================================================
# Section 2 -- THE load-bearing ranking-parity gate: at the shipped ceiling, consumed output
# (test_matches[:1] / ranking_quality / files ordering) is byte-identical to "before" (simulated by
# forcing `_test_source_limit=None` at the call site, the exact pre-fix behavior).
# ======================================================================================================


def _before_after(
    monkeypatch: pytest.MonkeyPatch, builder: Any, repo_map_payload: dict[str, Any], symbol: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    real_ceiling = repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING
    monkeypatch.setattr(repo_map, "_CONTEXT_TESTS_SOURCE_FILE_CEILING", None)
    before = builder(repo_map_payload, symbol)
    monkeypatch.setattr(repo_map, "_CONTEXT_TESTS_SOURCE_FILE_CEILING", real_ceiling)
    after = builder(repo_map_payload, symbol)
    return before, after


def test_impact_ranking_parity_at_shipped_ceiling(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    before, after = _before_after(
        monkeypatch, repo_map.build_symbol_impact_from_map, relevance_repo_map, "widget"
    )
    assert before["test_matches"] == after["test_matches"]
    assert before["tests"] == after["tests"]
    assert before["ranking_quality"] == after["ranking_quality"]
    assert before["files"] == after["files"]
    assert before["file_matches"] == after["file_matches"]
    # sanity: the coupled test actually scored (proves the fixture engages _context_tests's
    # import/graph scoring at all -- a silently-broken fixture would pass this test vacuously).
    assert before["test_matches"], "fixture did not produce any test_matches -- check the fixture"


def test_refs_ranking_parity_at_shipped_ceiling(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    before, after = _before_after(
        monkeypatch, repo_map.build_symbol_refs_from_map, relevance_repo_map, "widget"
    )
    assert before["ranking_quality"] == after["ranking_quality"]
    assert before["files"] == after["files"]
    assert before["tests"] == after["tests"]


def test_callers_ranking_parity_at_shipped_ceiling(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    before, after = _before_after(
        monkeypatch, repo_map.build_symbol_callers_from_map, relevance_repo_map, "widget"
    )
    assert before["ranking_quality"] == after["ranking_quality"]
    assert before["tests"] == after["tests"]
    assert before["files"] == after["files"]


def test_context_pack_from_map_test_source_limit_none_is_noop(
    relevance_repo_map: dict[str, Any],
) -> None:
    # Direct mechanism check, independent of the 3 blast-radius call sites: not passing
    # `_test_source_limit` at all vs explicitly passing `_test_source_limit=None` are the same
    # call (both keep the pre-existing default) -- proves the None default this PR relies on
    # really is a byte-identical no-op, not just "returns similar-looking data".
    omitted = repo_map.build_context_pack_from_map(relevance_repo_map, "widget")
    explicit_none = repo_map.build_context_pack_from_map(
        relevance_repo_map, "widget", _test_source_limit=None
    )
    assert omitted == explicit_none


# ======================================================================================================
# Section 3 -- mechanism sensitivity: proves the ranking-parity assertions above have real
# discriminating power (a WRONG/too-tight ceiling would be caught) rather than being vacuously true
# because the fixture never exercises the truncation branch at all.
# ======================================================================================================


def test_tight_bound_would_have_changed_consumed_output(relevance_repo_map: dict[str, Any]) -> None:
    unbounded = repo_map.build_context_pack_from_map(
        relevance_repo_map, "widget", _test_source_limit=None
    )
    # "widget_core.py" + mod00..mod24 = 26 files already fill a bound of 26 (alphabetical tie-break
    # order), so mod25 -- the module the fixture's only relevant test imports -- is truncated out.
    too_tight = repo_map.build_context_pack_from_map(
        relevance_repo_map, "widget", _test_source_limit=26
    )
    unbounded_score = int(unbounded["test_matches"][0]["score"])
    too_tight_score = int(too_tight["test_matches"][0]["score"])
    assert unbounded_score != too_tight_score, (
        "expected a bound of 26 to truncate mod25 out of source_files and change the coupled "
        "test's score -- if this assertion fails the fixture stopped exercising the truncation "
        "branch and the shipped-ceiling parity tests above would be running vacuously"
    )


def test_bound_covering_full_relevant_set_preserves_parity(
    relevance_repo_map: dict[str, Any],
) -> None:
    unbounded = repo_map.build_context_pack_from_map(
        relevance_repo_map, "widget", _test_source_limit=None
    )
    # A bound >= the total relevant-file count (31: widget_core + 30 modules) is the MINIMUM value
    # that includes mod25 regardless of tie-break order -- confirms parity holds once the ceiling
    # actually covers the relevant set, with a huge remaining margin below the shipped 2000.
    exactly_covering = repo_map.build_context_pack_from_map(
        relevance_repo_map, "widget", _test_source_limit=len(unbounded["files"])
    )
    assert unbounded["test_matches"] == exactly_covering["test_matches"]
    assert unbounded["ranking_quality"] == exactly_covering["ranking_quality"]


# ======================================================================================================
# Section 4 -- `_context_tests` deadline gate (Part B): None default is a byte-identical no-op;
# an already-expired deadline returns immediately; a deterministic fake clock proves a mid-loop
# break stops early and stamps the shared `_DeadlineBreakFlag`.
# ======================================================================================================

_STUB_ARGS: tuple[Any, ...] = (
    ["a.py"],  # source_files
    [f"test_{i}.py" for i in range(10)],  # tests
    ["x"],  # terms
    {},  # imports_by_file
    {},  # file_distances
    {},  # graph_scores
    {},  # file_scores
)


def test_context_tests_deadline_none_is_noop() -> None:
    omitted = repo_map._context_tests(*_STUB_ARGS)
    explicit_none = repo_map._context_tests(*_STUB_ARGS, deadline_monotonic=None, deadline_hit=None)
    assert omitted == explicit_none == []  # none of the 10 stub tests score > 0 on terms=["x"]


def test_context_tests_already_expired_deadline_returns_immediately() -> None:
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._context_tests(
        *_STUB_ARGS, deadline_monotonic=time.monotonic() - 1.0, deadline_hit=flag
    )
    assert result == []
    assert flag.hit is True


def test_context_tests_deadline_breaks_mid_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deterministic fake clock (mirrors test_repo_map_deadline.py): monotonic only advances when
    # _score_file_path -- called once per `for current in tests:` iteration, right after this PR's
    # new deadline check -- runs, so the deadline crosses after exactly 5 iterations regardless of
    # any other monotonic() caller.
    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score_file_path = repo_map._score_file_path

    def _advancing(path: str, terms: list[str]) -> int:
        clock["t"] += 1.0
        return original_score_file_path(path, terms)

    monkeypatch.setattr(repo_map, "_score_file_path", _advancing)

    tests = [f"test_{i}.py" for i in range(10)]
    flag = repo_map._DeadlineBreakFlag()
    repo_map._context_tests(
        ["a.py"],
        tests,
        ["x"],
        {},
        {},
        {},
        {},
        deadline_monotonic=base + 5.0,
        deadline_hit=flag,
    )
    assert flag.hit is True
    assert clock["t"] - base == 5.0, "expected the loop to break after exactly 5 iterations"


def test_context_tests_unbounded_never_sets_flag_even_past_many_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Companion to the mid-loop-break test: with deadline_monotonic=None, the clock still "moves"
    # (same advancing stub) but the loop must run to completion and the flag must stay False --
    # proves the None-check short-circuits the comparison entirely rather than merely tolerating a
    # generous deadline.
    clock = {"t": 1000.0}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score_file_path = repo_map._score_file_path

    def _advancing(path: str, terms: list[str]) -> int:
        clock["t"] += 1.0
        return original_score_file_path(path, terms)

    monkeypatch.setattr(repo_map, "_score_file_path", _advancing)

    tests = [f"test_{i}.py" for i in range(10)]
    flag = repo_map._DeadlineBreakFlag()
    repo_map._context_tests(
        ["a.py"], tests, ["x"], {}, {}, {}, {}, deadline_monotonic=None, deadline_hit=flag
    )
    assert flag.hit is False
    assert clock["t"] - 1000.0 == 10.0, "expected all 10 iterations to run unbounded"


# ======================================================================================================
# Section 5 -- end-to-end fold-in: an early break INSIDE _context_tests (isolated from every other
# already-deadline-gated sibling loop by keeping the fixture tiny/fast) still propagates to the
# outer symbol builder's `partial`/`deadline_limit` honesty signal.
# ======================================================================================================


def _rig_deadline_via_score_file_path(
    monkeypatch: pytest.MonkeyPatch, base: float
) -> dict[str, float]:
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score_file_path = repo_map._score_file_path

    def _advancing(path: str, terms: list[str]) -> int:
        clock["t"] += 1.0
        return original_score_file_path(path, terms)

    monkeypatch.setattr(repo_map, "_score_file_path", _advancing)
    return clock


def test_impact_context_tests_deadline_folds_into_partial(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = 1000.0
    _rig_deadline_via_score_file_path(monkeypatch, base)
    payload = repo_map.build_symbol_impact_from_map(
        relevance_repo_map, "widget", deadline_monotonic=base + 0.5
    )
    # DISCRIMINATING ASSERTION -- see the module docstring. `partial` /
    # `deadline_exceeded` are SHARED booleans any of the module's 24 time.monotonic
    # readers can set, so asserting them cannot say WHICH stage stopped. Nothing
    # else scans test candidates, so this pair can only be produced by the
    # `_context_tests` threading under test. Same shape as
    # tests/unit/test_repo_map_deadline.py's files_scanned < files_total.
    deadline_limit = payload.get("deadline_limit", {})
    assert payload.get("partial") is True
    assert deadline_limit.get("deadline_exceeded") is True
    # `.get` + an explicit assert, NOT `deadline_limit["..."]` -- a subscript raises KeyError,
    # which is an ERROR rather than a failed assertion and tells the reader nothing about what the
    # payload DID contain. This file already carries 11 arms that fail that way; do not add a 12th.
    scanned = deadline_limit.get("test_candidates_scanned")
    total = deadline_limit.get("test_candidates_total")
    assert scanned is not None and total is not None, (
        "_context_tests must contribute its own scanned/total pair to deadline_limit; "
        f"present keys were {sorted(deadline_limit)}"
    )
    # `scanned` may legitimately be 0: the rig blows the budget UPSTREAM, so `_context_tests` is
    # reached and breaks before its first item. That is still an attribution -- `total` is stamped
    # before the loop precisely so this case reports an honest 0/N instead of a 0/0 that reads as
    # "the stage never ran". The discriminating power is the KEYS' presence plus `scanned < total`;
    # requiring `0 < scanned` was over-specified and failed on the correct implementation.
    assert total > 0, "the stage must report the denominator it was going to scan"
    assert scanned < total, f"expected an early stop, got {scanned}/{total}"


def test_refs_context_tests_deadline_folds_into_partial(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = 1000.0
    _rig_deadline_via_score_file_path(monkeypatch, base)
    payload = repo_map.build_symbol_refs_from_map(
        relevance_repo_map, "widget", deadline_monotonic=base + 0.5
    )
    # DISCRIMINATING ASSERTION -- see the module docstring. `partial` /
    # `deadline_exceeded` are SHARED booleans any of the module's 24 time.monotonic
    # readers can set, so asserting them cannot say WHICH stage stopped. Nothing
    # else scans test candidates, so this pair can only be produced by the
    # `_context_tests` threading under test. Same shape as
    # tests/unit/test_repo_map_deadline.py's files_scanned < files_total.
    deadline_limit = payload.get("deadline_limit", {})
    assert payload.get("partial") is True
    assert deadline_limit.get("deadline_exceeded") is True
    # `.get` + an explicit assert, NOT `deadline_limit["..."]` -- a subscript raises KeyError,
    # which is an ERROR rather than a failed assertion and tells the reader nothing about what the
    # payload DID contain. This file already carries 11 arms that fail that way; do not add a 12th.
    scanned = deadline_limit.get("test_candidates_scanned")
    total = deadline_limit.get("test_candidates_total")
    assert scanned is not None and total is not None, (
        "_context_tests must contribute its own scanned/total pair to deadline_limit; "
        f"present keys were {sorted(deadline_limit)}"
    )
    # `scanned` may legitimately be 0: the rig blows the budget UPSTREAM, so `_context_tests` is
    # reached and breaks before its first item. That is still an attribution -- `total` is stamped
    # before the loop precisely so this case reports an honest 0/N instead of a 0/0 that reads as
    # "the stage never ran". The discriminating power is the KEYS' presence plus `scanned < total`;
    # requiring `0 < scanned` was over-specified and failed on the correct implementation.
    assert total > 0, "the stage must report the denominator it was going to scan"
    assert scanned < total, f"expected an early stop, got {scanned}/{total}"


def test_callers_context_tests_deadline_folds_into_partial(
    relevance_repo_map: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    base = 1000.0
    _rig_deadline_via_score_file_path(monkeypatch, base)
    payload = repo_map.build_symbol_callers_from_map(
        relevance_repo_map, "widget", deadline_monotonic=base + 0.5
    )
    # DISCRIMINATING ASSERTION -- see the module docstring. `partial` /
    # `deadline_exceeded` are SHARED booleans any of the module's 24 time.monotonic
    # readers can set, so asserting them cannot say WHICH stage stopped. Nothing
    # else scans test candidates, so this pair can only be produced by the
    # `_context_tests` threading under test. Same shape as
    # tests/unit/test_repo_map_deadline.py's files_scanned < files_total.
    deadline_limit = payload.get("deadline_limit", {})
    assert payload.get("partial") is True
    assert deadline_limit.get("deadline_exceeded") is True
    # `.get` + an explicit assert, NOT `deadline_limit["..."]` -- a subscript raises KeyError,
    # which is an ERROR rather than a failed assertion and tells the reader nothing about what the
    # payload DID contain. This file already carries 11 arms that fail that way; do not add a 12th.
    scanned = deadline_limit.get("test_candidates_scanned")
    total = deadline_limit.get("test_candidates_total")
    assert scanned is not None and total is not None, (
        "_context_tests must contribute its own scanned/total pair to deadline_limit; "
        f"present keys were {sorted(deadline_limit)}"
    )
    # `scanned` may legitimately be 0: the rig blows the budget UPSTREAM, so `_context_tests` is
    # reached and breaks before its first item. That is still an attribution -- `total` is stamped
    # before the loop precisely so this case reports an honest 0/N instead of a 0/0 that reads as
    # "the stage never ran". The discriminating power is the KEYS' presence plus `scanned < total`;
    # requiring `0 < scanned` was over-specified and failed on the correct implementation.
    assert total > 0, "the stage must report the denominator it was going to scan"
    assert scanned < total, f"expected an early stop, got {scanned}/{total}"


# ======================================================================================================
# Section 6 -- the `_relevant_tests_for_symbol` call site (repo_map.py :3982, reached via callers'
# `caller_files=` branch) also receives the deadline params -- spy-based, since that copy of
# `_context_tests`'s output IS fully consumed (ranked/ordered in full) and must NOT be
# `_test_source_limit`-bounded, only deadline-gated.
# ======================================================================================================


def _build_caller_fixture(root: Path) -> Path:
    # Purpose-built (deliberately separate from `relevance_repo_map`): `_relevant_tests_for_symbol`
    # only reaches its OWN internal `_context_tests` call (repo_map.py :3982) when `caller_files` is
    # truthy -- an empty list is falsy in Python, so a symbol with no actual call sites (true of
    # every def-only file in `relevance_repo_map`) never exercises that branch. This fixture adds a
    # real caller so `tg callers widget` finds a non-empty `caller_files`.
    project = root / "caller_project"
    project.mkdir()
    (project / "widget_core.py").write_text('def widget():\n    return "core"\n', encoding="utf-8")
    (project / "widget_user.py").write_text(
        "from widget_core import widget\n\n\ndef use_widget():\n    return widget()\n",
        encoding="utf-8",
    )
    (project / "test_widget_user.py").write_text(
        'from widget_user import use_widget\n\n\ndef test_use_widget():\n    assert use_widget() == "core"\n',
        encoding="utf-8",
    )
    return project.resolve()


def test_callers_relevant_tests_for_symbol_call_site_receives_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _build_caller_fixture(tmp_path)
    rmap = repo_map.build_repo_map(str(project))

    captured_calls: list[dict[str, Any]] = []
    original = repo_map._context_tests

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured_calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_context_tests", _spy)
    sentinel = time.monotonic() + 10_000.0
    payload = repo_map.build_symbol_callers_from_map(rmap, "widget", deadline_monotonic=sentinel)

    assert payload["callers"], "fixture did not produce any caller records -- check the fixture"
    assert len(captured_calls) == 2, (
        f"expected callers to reach BOTH _context_tests call sites (the _build_context_pack_"
        f"from_map one and the _relevant_tests_for_symbol caller_files-truthy one), got "
        f"{len(captured_calls)} calls"
    )
    for call_kwargs in captured_calls:
        assert call_kwargs.get("deadline_monotonic") == sentinel
        assert call_kwargs.get("deadline_hit") is not None


def test_every_context_tests_scan_on_the_callers_path_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial gate on #904, HIGH: `build_symbol_callers_from_map` reaches `_context_tests`
    TWICE -- once inside the context pack (`_build_context_pack_from_map`) and once inside
    `_relevant_tests_for_symbol` -- and only the first carried `_test_scan_counts`.

    A budget expiring in the UNCOUNTED scan therefore still reported
    ``test_candidates_scanned == test_candidates_total``: the attribution exonerating the very
    stage that stopped, which is the failure this pair exists to prevent. The uncounted one is
    also the likelier to trip, because `_test_source_limit` is deliberately not applied to it.

    STRUCTURAL ON PURPOSE, and this is a limitation worth stating: it asserts every scan is
    INSTRUMENTED, not that a particular timing produces a particular fraction. A clock-rigged
    arm would prove more and would be timing-fragile; this one cannot flake and it fails
    immediately if a future call site is added without the counter -- the defect that occurred.
    """
    # SAME fixture as test_callers_relevant_tests_for_symbol_call_site_receives_deadline: the
    # second scan lives behind `if caller_files:`, so a repo map with no callers reaches
    # _context_tests only ONCE and this guard would be vacuous. The `>= 2` precondition below is
    # what makes that failure loud instead of silent -- it fired on the first draft of this test,
    # which used the caller-less fixture.
    project = _build_caller_fixture(tmp_path)
    rmap = repo_map.build_repo_map(str(project))

    seen: list[Any] = []
    original = repo_map._context_tests

    def _spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("_test_scan_counts"))
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_context_tests", _spy)
    repo_map.build_symbol_callers_from_map(rmap, "widget")

    assert len(seen) >= 2, (
        "this guard is vacuous unless the callers path really reaches _context_tests more than "
        f"once -- it was called {len(seen)} time(s)"
    )
    uncounted = [i for i, counts in enumerate(seen) if counts is None]
    assert not uncounted, (
        f"scan(s) at index {uncounted} of {len(seen)} received no _test_scan_counts, so a "
        "deadline expiring there would report scanned == total for a stage that stopped"
    )
    assert len({id(c) for c in seen}) == 1, (
        "every scan must share ONE counter object, else the emitted pair describes only one scan"
    )


@pytest.mark.parametrize(
    "builder_name",
    ["build_symbol_impact_from_map", "build_symbol_refs_from_map", "build_symbol_callers_from_map"],
)
def test_source_ceiling_changes_no_payload_at_the_BOUNDARY(
    tmp_path: Path, builder_name: str
) -> None:
    """Adversarial gate on #904, MEDIUM: every OTHER parity arm in this file runs a 32-file
    fixture where `_CONTEXT_TESTS_SOURCE_FILE_CEILING` (2000) is unreachable -- the one population
    where the bound CANNOT fail. That is how a real output change shipped unnoticed: impact's
    `test_matches` values are supplied by `test_matches_by_path`, which is built FROM the context
    pack's `test_matches`, so bounding the source list downgraded them:

        unbounded  score=23  reasons=[path, filename, test-graph, graph-centrality]  conf=strong
        ceiling=1  score= 2  reasons=[path]                                          conf=weak

    This test forces the ceiling to 1 -- unmissably past the boundary -- and pins that NO entry
    point's payload moves. `_test_source_limit` is now applied only where that holds (refs and
    callers, which really do read just `test_matches[:1]`); impact stays bounded by `--deadline`,
    which is honest because it stamps `partial`.

    The same-ceiling CONTROL is load-bearing: without it a comparison that always matched would
    be indistinguishable from a nondeterministic builder whose diff this test could never see.
    """
    project = _build_caller_fixture(tmp_path)
    rmap = repo_map.build_repo_map(str(project))
    builder = getattr(repo_map, builder_name)

    def _run(ceiling: int) -> str:
        original = repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING
        repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING = ceiling
        try:
            return json.dumps(builder(rmap, "widget"), default=str, sort_keys=True)
        finally:
            repo_map._CONTEXT_TESTS_SOURCE_FILE_CEILING = original

    unbounded = _run(10_000)
    assert unbounded == _run(10_000), (
        f"{builder_name} is not deterministic at a fixed ceiling, so the comparison below could "
        "never detect a real difference -- fix the nondeterminism before trusting this test"
    )
    assert _run(1) == unbounded, (
        f"{builder_name}'s payload CHANGED when the test-source ceiling was forced to 1. The "
        "bound is only safe where the caller reads test_matches[:1]; if this fires, either drop "
        "_test_source_limit at that call site or disclose the truncation."
    )
