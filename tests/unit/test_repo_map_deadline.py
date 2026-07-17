"""Moat P0-6 STEP 1: build_repo_map deadline -> partial results on a time budget.

A supplied ABSOLUTE monotonic deadline stops the CPU-bound per-file parse loop early and returns
partial:true + a deadline_limit sibling, instead of the caller's hard timeout discarding all work
(the recurring dogfood complaint: '60s cap errors with bare timed-out, exit 1, zero JSON'). The
signal is kept SEPARATE from scan_limit (file-cap cause) so the remediation advice is the right knob.
"""

from __future__ import annotations

import time
from pathlib import Path

import tensor_grep.cli.repo_map as repo_map


def _make_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )


def test_deadline_already_expired_returns_partial_immediately(tmp_path: Path) -> None:
    _make_repo(tmp_path, 6)
    # An already-expired deadline must return a valid partial dict (no exception, no hang).
    result = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=time.monotonic() - 1.0)
    assert isinstance(result, dict)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True
    assert result["deadline_limit"]["files_scanned"] == 0  # broke before any parse


def test_deadline_mid_scan_keeps_partial_results(tmp_path: Path, monkeypatch) -> None:
    _make_repo(tmp_path, 10)
    # Deterministic fake clock: monotonic only advances when a file is parsed, so the deadline
    # crosses after exactly 5 parses regardless of any other monotonic() callers.
    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_parse = repo_map._imports_and_symbols_for_path

    def _clock_advancing_parse(path, **kwargs):
        clock["t"] += 1.0
        return original_parse(path, **kwargs)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _clock_advancing_parse)

    result = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=base + 5.0)

    assert result.get("partial") is True
    deadline_limit = result["deadline_limit"]
    assert deadline_limit["deadline_exceeded"] is True
    assert 0 < deadline_limit["files_scanned"] < deadline_limit["files_total"]  # some but not all
    assert result["symbols"], "partial work must be RETAINED, not zeroed"


def test_deadline_none_is_no_op(tmp_path: Path) -> None:
    _make_repo(tmp_path, 6)
    result = repo_map.build_repo_map(str(tmp_path))  # no deadline -> unchanged behavior
    assert "partial" not in result
    assert "deadline_limit" not in result
    assert result["symbols"]  # full parse, nothing bounded


def _install_advancing_clock(monkeypatch, base: float = 1000.0) -> None:
    # monotonic only advances when a file is parsed, so a deadline crosses deterministically.
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_parse = repo_map._imports_and_symbols_for_path

    def _advancing(path, **kwargs):
        clock["t"] += 1.0
        return original_parse(path, **kwargs)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _advancing)


def test_step3_top_level_builders_thread_deadline_to_partial(tmp_path: Path, monkeypatch) -> None:
    # moat P0-6 step 3: each top-level symbol builder converts deadline_seconds to one absolute
    # budget, threads it into build_repo_map, and surfaces partial:true on the wrapped output.
    _make_repo(tmp_path, 12)
    _install_advancing_clock(monkeypatch)  # deadline crosses after ~5 parses (base + 5.0)
    for builder in (
        repo_map.build_symbol_refs,
        repo_map.build_symbol_callers,
        repo_map.build_symbol_impact,
        repo_map.build_symbol_blast_radius,
        repo_map.build_symbol_source,  # CEO v1.72.1 dogfood M1
        repo_map.build_symbol_blast_radius_plan,  # CEO v1.72.1 dogfood M1
    ):
        result = builder("f1", str(tmp_path), deadline_seconds=5.0)
        assert result.get("partial") is True, (
            f"{builder.__name__} dropped the deadline partial flag"
        )


def test_step3_deadline_none_leaves_builders_unbounded(tmp_path: Path) -> None:
    _make_repo(tmp_path, 4)
    result = repo_map.build_symbol_callers("f1", str(tmp_path))  # no deadline
    assert "partial" not in result
    assert "deadline_limit" not in result


# --- CEO v1.72.1 dogfood M1: build_symbol_source / build_symbol_blast_radius_plan join the #581
# --deadline threading pattern (same builders as above, called out individually so a regression in
# either one's OWN wiring -- not just the shared loop above -- fails with an unambiguous name). ---


def test_m1_source_deadline_already_expired_returns_partial_immediately(tmp_path: Path) -> None:
    _make_repo(tmp_path, 6)
    result = repo_map.build_symbol_source("f1", str(tmp_path), deadline_seconds=-1.0)
    assert isinstance(result, dict)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_m1_source_deadline_none_is_unaffected(tmp_path: Path) -> None:
    _make_repo(tmp_path, 4)
    result = repo_map.build_symbol_source("f1", str(tmp_path))
    assert "partial" not in result
    assert "deadline_limit" not in result


def test_m1_blast_radius_plan_deadline_already_expired_returns_partial_immediately(
    tmp_path: Path,
) -> None:
    _make_repo(tmp_path, 6)
    result = repo_map.build_symbol_blast_radius_plan("f1", str(tmp_path), deadline_seconds=-1.0)
    assert isinstance(result, dict)
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_m1_blast_radius_plan_deadline_none_is_unaffected(tmp_path: Path) -> None:
    _make_repo(tmp_path, 4)
    result = repo_map.build_symbol_blast_radius_plan("f1", str(tmp_path))
    assert "partial" not in result
    assert "deadline_limit" not in result


def _make_caller_repo(root: Path, callers: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "target.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    for index in range(callers):
        (src / f"caller{index}.py").write_text(
            "from src.target import widget\n\n\ndef use():\n    return widget()\n", encoding="utf-8"
        )


def test_step6_caller_scan_honors_already_expired_deadline(tmp_path: Path) -> None:
    # moat P0-6 step 6: the CALLER-SCAN traversal (not just the repo-map parse) must honor the
    # deadline -- this is why central symbols hung past --deadline while leaf symbols didn't.
    _make_caller_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))  # full map, no deadline
    result = repo_map.build_symbol_callers_from_map(
        rm, "widget", deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step6_caller_scan_no_deadline_is_complete(tmp_path: Path) -> None:
    _make_caller_repo(tmp_path, 4)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_callers_from_map(rm, "widget")  # no deadline
    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"
    assert len(result["callers"]) >= 1  # found the real callers when unbounded


def test_step6_blast_radius_honors_caller_scan_deadline(tmp_path: Path) -> None:
    # moat P0-6 step 6: blast-radius runs the same direct-caller scan, so it must honor --deadline
    # for central symbols too (the 1.35.0 dogfood: `blast-radius QueryEngine --deadline 10` hung 90s+).
    _make_caller_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_blast_radius_from_map(
        rm, "widget", deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"


def test_step6_blast_radius_no_deadline_is_complete(tmp_path: Path) -> None:
    _make_caller_repo(tmp_path, 4)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_blast_radius_from_map(rm, "widget")  # no deadline
    assert "partial" not in result


def test_step6_refs_honors_scan_deadline(tmp_path: Path) -> None:
    # moat P0-6 step 6: refs runs the same per-file reference scan -> must honor --deadline for
    # central symbols (1.35.0 dogfood: `refs QueryEngine --deadline 15` -> 45s timeout, no partial).
    _make_caller_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_refs_from_map(
        rm, "widget", deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step6_refs_no_deadline_is_complete(tmp_path: Path) -> None:
    _make_caller_repo(tmp_path, 4)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_refs_from_map(rm, "widget")  # no deadline
    assert "partial" not in result


# ---------------------------------------------------------------------------------------------
# Task #61: --deadline was ineffective for a CENTRAL symbol because two loops SIBLING to the
# (already deadline-bounded) caller-scan main loop -- _build_import_graph_consumers_from_map and
# _preferred_definition_files -- re-walked the same large file universe with NO deadline check of
# their own. A central symbol's main loop could finish inside budget while either sibling loop
# alone pushed wall-clock well past --deadline (profiled: `callers ... --deadline 10` -> ~25s).
# ---------------------------------------------------------------------------------------------


def _make_import_consumer_repo(root: Path, consumers: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "target.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    for index in range(consumers):
        # Import-only (no call site) -- exercises the import-graph-consumers sibling loop
        # specifically, independent of the caller-scan main loop's own call-matching.
        (src / f"consumer{index}.py").write_text(
            "from src.target import widget\n", encoding="utf-8"
        )


def test_step61_import_graph_consumers_loop_honors_already_expired_deadline(tmp_path: Path) -> None:
    # Direct unit test of the sibling function itself: an already-expired deadline breaks on the
    # FIRST iteration and flags deadline_hit, without raising or processing any file.
    _make_import_consumer_repo(tmp_path, 5)
    rm = repo_map.build_repo_map(str(tmp_path))
    files = repo_map._repo_map_file_universe(rm)
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._build_import_graph_consumers_from_map(
        rm,
        "widget",
        [str(tmp_path / "src" / "target.py")],
        bounded_files=files,
        deadline_monotonic=time.monotonic() - 1.0,
        deadline_hit=flag,
    )
    assert result == []
    assert flag.hit is True


def test_step61_import_graph_consumers_loop_none_deadline_is_unaffected(tmp_path: Path) -> None:
    # Golden-parity guard: deadline_monotonic=None (the default) must behave exactly as before --
    # a full, unbounded scan that finds every import consumer.
    _make_import_consumer_repo(tmp_path, 5)
    rm = repo_map.build_repo_map(str(tmp_path))
    files = repo_map._repo_map_file_universe(rm)
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._build_import_graph_consumers_from_map(
        rm,
        "widget",
        [str(tmp_path / "src" / "target.py")],
        bounded_files=files,
        deadline_hit=flag,
    )
    assert len(result) == 5
    assert flag.hit is False


def test_step61_import_graph_consumers_loop_breaks_mid_loop_not_just_pre_check(
    tmp_path: Path, monkeypatch
) -> None:
    # The sharper claim: the deadline crosses WHILE the loop is running (not merely pre-expired),
    # so only SOME consumers are found and the loop demonstrably stopped early rather than
    # completing naturally.
    _make_import_consumer_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    files = repo_map._repo_map_file_universe(rm)

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_target = repo_map._import_update_target
    call_count = {"n": 0}

    def _advancing_target(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_target(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_import_update_target", _advancing_target)

    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._build_import_graph_consumers_from_map(
        rm,
        "widget",
        [str(tmp_path / "src" / "target.py")],
        bounded_files=files,
        deadline_monotonic=base + 3.0,
        deadline_hit=flag,
    )
    assert flag.hit is True
    assert 0 < call_count["n"] < 6  # cut short mid-scan, not exhausted
    assert len(result) < 6  # partial consumer set, not the full 6


def test_step61_preferred_definition_files_honors_already_expired_deadline(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._preferred_definition_files(
        rm, "shared", deadline_monotonic=time.monotonic() - 1.0, deadline_hit=flag
    )
    assert flag.hit is True
    # No scoring happened (broke before any file was scanned) -> falls back to all definitions.
    assert sorted(result) == sorted([str(src / "a.py"), str(src / "b.py")])


def test_step61_preferred_definition_files_none_deadline_is_unaffected(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    (src / "use_a.py").write_text(
        "from src.a import shared\n\n\ndef go():\n    return shared()\n", encoding="utf-8"
    )
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._preferred_definition_files(rm, "shared", deadline_hit=flag)
    assert flag.hit is False
    assert result == [str(src / "a.py")]


def test_step61_preferred_definition_files_breaks_mid_loop_not_just_pre_check(
    tmp_path: Path, monkeypatch
) -> None:
    # TDD gate requirement (3): a deadline-break in _preferred_definition_files specifically is
    # exercised -- the universe-scan loop must stop partway through, not merely refuse to start.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    for index in range(6):
        (src / f"other{index}.py").write_text(f"x = {index}\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original = repo_map._file_imports_symbol_from_definition
    call_count = {"n": 0}

    def _advancing(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_file_imports_symbol_from_definition", _advancing)

    flag = repo_map._DeadlineBreakFlag()
    result = repo_map._preferred_definition_files(
        rm, "shared", deadline_monotonic=base + 3.0, deadline_hit=flag
    )
    assert flag.hit is True
    # 6 "other*.py" files x up to 2 definition files = up to 12 possible calls; the deadline
    # (3-tick budget, 1 tick/call) must cut this off well short of exhausting the universe.
    assert 0 < call_count["n"] < 12
    assert result  # falls back to definition_files (no score cleared this early) -- never empty


def test_step61_callers_marks_partial_when_import_graph_consumers_loop_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    # THE bug fix, end to end at the repo_map layer: the caller-scan MAIN loop finishes inside the
    # shared --deadline budget (nothing here advances the fake clock during it), but the SIBLING
    # import-graph-consumers loop crosses the deadline mid-scan. Before task #61 this sibling loop
    # had no deadline check at all and the result would come back marked COMPLETE (graph_completeness
    # == "moderate") despite having blown well past the wall-clock budget the caller asked for.
    _make_import_consumer_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_target = repo_map._import_update_target
    call_count = {"n": 0}

    def _advancing_target(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_target(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_import_update_target", _advancing_target)

    result = repo_map.build_symbol_callers_from_map(rm, "widget", deadline_monotonic=base + 3.0)

    assert call_count["n"] > 0  # the sibling loop actually ran (proves it wasn't the main loop)
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert result["deadline_limit"]["deadline_exceeded"] is True
    assert len(result.get("import_graph_consumers", [])) < 6  # cut short, not exhaustive


def test_step61_callers_no_deadline_import_graph_consumers_stays_complete(tmp_path: Path) -> None:
    # Golden-parity companion to the test above: without --deadline, behavior is unchanged --
    # graph_completeness stays "moderate" and every import consumer is found.
    _make_import_consumer_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_callers_from_map(rm, "widget")  # no deadline
    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"
    assert len(result.get("import_graph_consumers", [])) == 6


def test_step61_blast_radius_already_expired_deadline_is_partial(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))
    result = repo_map.build_symbol_blast_radius_from_map(
        rm, "shared", deadline_monotonic=time.monotonic() - 1.0
    )
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"


def test_step61_blast_radius_marks_partial_on_its_own_preferred_definition_files_call(
    tmp_path: Path, monkeypatch
) -> None:
    # build_symbol_blast_radius_from_map makes its OWN separate _preferred_definition_files call
    # (distinct from the one build_symbol_callers_from_map already makes internally). Construct the
    # narrow case where the NESTED callers_payload comes back complete (its own internal
    # preferred-definition-files call finishes inside budget, and the caller-scan/import-graph
    # loops never advance the fake clock at all).
    #
    # #52 fix (loop C) update: build_symbol_impact_from_map now ALSO receives the shared
    # deadline_monotonic (previously unconstrained -- out of task #61's scope, impact had no
    # deadline param at all). Its OWN internal _preferred_definition_files call now stops EXACTLY
    # at the shared deadline instead of running unconstrained past it -- and folds that into
    # impact_payload["partial"] via the NEW partial block loop C adds. Either way the clock lands
    # on (or past) the shared deadline by the time blast-radius's OWN direct
    # _preferred_definition_files call runs, so it independently observes an already-exceeded
    # deadline and folds that into partial by itself too. This test now proves BOTH of the
    # widened fold-in's sources (impact_payload["partial"] and this function's own direct call)
    # independently arrive at the correct answer, not just piggy-backing on
    # callers_payload["partial"].
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    for index in range(3):
        (src / f"other{index}.py").write_text(f"x = {index}\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original = repo_map._file_imports_symbol_from_definition

    def _advancing(*args, **kwargs):
        clock["t"] += 1.0
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_file_imports_symbol_from_definition", _advancing)

    # 3 "other*.py" files x 2 definition files = 6 ticks per FULL _preferred_definition_files scan.
    # Budget = base + 10: generous enough that build_symbol_callers_from_map's internal call (6
    # ticks, clock -> base+6) finishes clean -- callers_payload is NOT partial. impact_payload's
    # OWN _preferred_definition_files call is now ALSO deadline-bound (loop C fix): it can only get
    # partway through the remaining budget before ITS OWN pre-iteration check catches the exhausted
    # deadline and breaks, landing the clock at-or-past the shared deadline. blast-radius's OWN
    # direct call then observes an already-exceeded (or exactly-at) deadline immediately.
    result = repo_map.build_symbol_blast_radius_from_map(
        rm, "shared", deadline_monotonic=base + 10.0
    )

    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"


# =================================================================================================
# Task #52: FOUR more unbounded loops found by a verify-plan-against-code design pass on top of
# #396/#440 (task #61) -- the dominant remaining cause of a --deadline overrun on a high-fan-out
# symbol shape (e.g. "main": many definitions, ~0 import edges). Loop labels below match the design
# doc: A = _iter_repo_files walk, B = _relevant_tests_for_symbol, C = build_symbol_impact_from_map
# (no deadline plumbing at all), D = build_symbol_refs_from_map's second string_refs pass.
# =================================================================================================


def _make_flat_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index}.py").write_text(f"x_{index} = {index}\n", encoding="utf-8")


# --- Loop A: _iter_repo_files -------------------------------------------------------------------


def _install_call_counting_clock(monkeypatch, base: float = 1000.0) -> dict:
    """Every call to time.monotonic() advances the fake clock by exactly 1.0 -- used to test
    _iter_repo_files directly, where the only per-file 'work' during the walk IS the deadline
    check itself (there is no separate expensive per-file function to hook, unlike the parse loop
    tests above which hook _imports_and_symbols_for_path)."""
    clock = {"t": base, "calls": 0}

    def _fake_monotonic() -> float:
        clock["calls"] += 1
        clock["t"] = base + (clock["calls"] - 1)
        return clock["t"]

    monkeypatch.setattr(repo_map.time, "monotonic", _fake_monotonic)
    return clock


def test_step52_iter_repo_files_bucket_branch_breaks_mid_walk(tmp_path: Path, monkeypatch) -> None:
    # loop A (max_files set -> the bucket-interleave branch): a fake clock that advances on every
    # monotonic() call proves the walk breaks WHILE running (some but not all files collected),
    # not merely refuses to start.
    _make_flat_repo(tmp_path, 10)
    base = _install_call_counting_clock(monkeypatch)["t"]
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map._iter_repo_files(
        tmp_path, max_files=100, deadline_monotonic=base + 3.0, deadline_hit=flag
    )

    assert 0 < len(result) < 10
    assert flag.hit is True


def test_step52_iter_repo_files_bucket_branch_none_deadline_is_unaffected(tmp_path: Path) -> None:
    # Golden-parity guard: deadline_monotonic=None (the default) is a byte-identical no-op -- every
    # one of the walk's ~12 existing call sites that never pass it stay unaffected.
    _make_flat_repo(tmp_path, 10)
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map._iter_repo_files(tmp_path, max_files=100, deadline_hit=flag)

    assert len(result) == 10
    assert flag.hit is False


def test_step52_iter_repo_files_unbounded_branch_breaks_mid_walk(
    tmp_path: Path, monkeypatch
) -> None:
    # loop A (max_files=None -> the plain-list branch): build_repo_map's own no-cap call sites
    # (every test in this file that calls build_repo_map(str(tmp_path)) without max_repo_files)
    # reach this branch, which had NO deadline bound at all before this fix.
    _make_flat_repo(tmp_path, 10)
    base = _install_call_counting_clock(monkeypatch)["t"]
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map._iter_repo_files(tmp_path, deadline_monotonic=base + 3.0, deadline_hit=flag)

    assert 0 < len(result) < 10
    assert flag.hit is True


def test_step52_iter_repo_files_unbounded_branch_none_deadline_is_unaffected(
    tmp_path: Path,
) -> None:
    _make_flat_repo(tmp_path, 10)
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map._iter_repo_files(tmp_path, deadline_hit=flag)

    assert len(result) == 10
    assert flag.hit is False


def test_step52_build_repo_map_folds_walk_timeout_into_partial(tmp_path: Path, monkeypatch) -> None:
    # loop A end to end: build_repo_map calls _iter_repo_files with NO max_repo_files (the common
    # no-cap shape every other test in this file uses), so before this fix the walk was completely
    # unbounded regardless of --deadline. No hook on _imports_and_symbols_for_path here -- the
    # WALK's own per-file check alone consumes the shared fake clock, proving the walk phase
    # itself (not just the parse loop) now honors --deadline: files_total below is the walked
    # file-list SIZE, which before this fix would always equal the full repo (10) no matter the
    # budget.
    _make_repo(tmp_path, 10)
    base = _install_call_counting_clock(monkeypatch)["t"]

    result = repo_map.build_repo_map(str(tmp_path), deadline_monotonic=base + 2.0)

    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True
    assert result["deadline_limit"]["files_total"] < 10  # the WALK itself stopped early


def test_step52_build_repo_map_walk_none_deadline_is_unaffected(tmp_path: Path) -> None:
    _make_repo(tmp_path, 10)
    result = repo_map.build_repo_map(str(tmp_path))  # no deadline
    assert "partial" not in result
    assert len(result["files"]) == 10


# --- Loop B: _relevant_tests_for_symbol ----------------------------------------------------------


def _make_widget_repo_with_tests(root: Path, test_count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "target.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    for index in range(test_count):
        (tests_dir / f"test_t{index}.py").write_text(
            "from src.target import widget\n\n\ndef test_x():\n    assert widget() == 1\n",
            encoding="utf-8",
        )


def _install_import_check_advancing_clock(monkeypatch) -> dict:
    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original = repo_map._file_imports_symbol_from_definition
    call_count = {"n": 0}

    def _advancing(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_file_imports_symbol_from_definition", _advancing)
    return {"base": base, "clock": clock, "call_count": call_count}


def test_step52_relevant_tests_direct_definition_branch_breaks_mid_loop(
    tmp_path: Path, monkeypatch
) -> None:
    # loop B, branch 1 (direct_definition_tests, reached when caller_files is truthy): this loop
    # re-walked the FULL tests list with no deadline check at all before this fix.
    _make_widget_repo_with_tests(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    handles = _install_import_check_advancing_clock(monkeypatch)
    base, call_count = handles["base"], handles["call_count"]

    flag = repo_map._DeadlineBreakFlag()
    definition_files = [str(tmp_path / "src" / "target.py")]
    result = repo_map._relevant_tests_for_symbol(
        rm,
        "widget",
        definition_files,
        caller_files=definition_files,  # any truthy value routes into the direct-definition branch
        deadline_monotonic=base + 3.0,
        deadline_hit=flag,
    )

    assert flag.hit is True
    assert 0 < call_count["n"] < 6  # cut short mid-scan, not exhausted
    assert isinstance(result, list)


def test_step52_relevant_tests_related_branch_breaks_mid_loop(tmp_path: Path, monkeypatch) -> None:
    # loop B, branch 2 (related, reached when caller_files is falsy): same unbounded hazard,
    # guarded identically.
    _make_widget_repo_with_tests(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    handles = _install_import_check_advancing_clock(monkeypatch)
    base, call_count = handles["base"], handles["call_count"]

    flag = repo_map._DeadlineBreakFlag()
    definition_files = [str(tmp_path / "src" / "target.py")]
    result = repo_map._relevant_tests_for_symbol(
        rm,
        "widget",
        definition_files,
        # caller_files omitted (falsy) -> skips the direct-definition branch, exercises `related`
        deadline_monotonic=base + 3.0,
        deadline_hit=flag,
    )

    assert flag.hit is True
    assert 0 < call_count["n"] < 6
    assert isinstance(result, list)


def test_step52_relevant_tests_none_deadline_is_unaffected(tmp_path: Path) -> None:
    _make_widget_repo_with_tests(tmp_path, 1)
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map._relevant_tests_for_symbol(
        rm, "widget", [str(tmp_path / "src" / "target.py")], deadline_hit=flag
    )

    assert flag.hit is False
    assert result == [str(tmp_path / "tests" / "test_t0.py")]


def test_step52_callers_marks_partial_when_relevant_tests_loop_times_out(
    tmp_path: Path, monkeypatch
) -> None:
    # loop B end to end (mirrors the #61 import-graph-consumers test above): the caller-scan MAIN
    # loop, import-graph-consumers loop, and preferred-definition-files call (a single definition
    # -> trivial early return) all finish inside the shared budget without advancing the fake
    # clock, but the SIBLING _relevant_tests_for_symbol loop crosses the deadline mid-scan. Before
    # this fix that loop had no deadline check at all and the result would come back marked
    # COMPLETE (graph_completeness == "moderate") despite blowing well past the wall-clock budget.
    _make_widget_repo_with_tests(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    handles = _install_import_check_advancing_clock(monkeypatch)
    base, call_count = handles["base"], handles["call_count"]

    result = repo_map.build_symbol_callers_from_map(rm, "widget", deadline_monotonic=base + 3.0)

    assert call_count["n"] > 0  # the sibling loop actually ran
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step52_callers_no_deadline_relevant_tests_stays_complete(tmp_path: Path) -> None:
    # Golden-parity companion: without --deadline, behavior is unchanged.
    _make_widget_repo_with_tests(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_callers_from_map(rm, "widget")  # no deadline

    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"
    assert len(result.get("tests", [])) == 6


# --- Loop C: build_symbol_impact_from_map had NO deadline plumbing at all ------------------------


def test_step52_impact_from_map_accepts_deadline_and_sets_partial_block(tmp_path: Path) -> None:
    # loop C: build_symbol_impact_from_map previously took no deadline parameter whatsoever --
    # both its _preferred_definition_files and _relevant_tests_for_symbol calls ran fully unbounded
    # no matter the caller's budget, and the function had no partial/deadline_limit block at all.
    # An already-expired deadline must stop the (>1 definition, so non-trivial) scoring scan on its
    # first iteration and set the NEW block.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_impact_from_map(
        rm, "shared", deadline_monotonic=time.monotonic() - 1.0
    )

    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step52_impact_from_map_none_deadline_is_unaffected(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_impact_from_map(rm, "shared")  # no deadline

    assert "partial" not in result
    assert "deadline_limit" not in result


def test_step52_impact_from_map_marks_partial_on_relevant_tests_timeout_alone(
    tmp_path: Path, monkeypatch
) -> None:
    # Narrow case (mirrors the blast-radius isolation test above): exactly ONE definition means
    # _preferred_definition_files short-circuits trivially (len<=1 -> immediate return, zero
    # ticks), isolating related_tests_deadline_hit as the SOLE source of the partial signal --
    # proving the fold-in is not just piggy-backing on the other flag.
    _make_widget_repo_with_tests(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))
    handles = _install_import_check_advancing_clock(monkeypatch)
    base, call_count = handles["base"], handles["call_count"]

    result = repo_map.build_symbol_impact_from_map(rm, "widget", deadline_monotonic=base + 2.0)

    assert call_count["n"] > 0
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step52_build_symbol_impact_forwards_deadline_to_from_map(
    tmp_path: Path, monkeypatch
) -> None:
    # loop C wiring: build_symbol_impact (the public entry point) must forward deadline_monotonic
    # into its build_symbol_impact_from_map call -- the exact one-line kwarg this fix adds.
    _make_repo(tmp_path, 4)
    recorded: dict = {}
    original = repo_map.build_symbol_impact_from_map

    def _spy(repo_map_arg, symbol, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(repo_map_arg, symbol, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_impact_from_map", _spy)

    repo_map.build_symbol_impact("f1", str(tmp_path), deadline_seconds=5.0)

    assert recorded.get("deadline_monotonic") is not None


def test_step52_build_symbol_impact_forwards_none_deadline(tmp_path: Path, monkeypatch) -> None:
    _make_repo(tmp_path, 4)
    recorded: dict = {"deadline_monotonic": "sentinel"}
    original = repo_map.build_symbol_impact_from_map

    def _spy(repo_map_arg, symbol, **kwargs):
        recorded["deadline_monotonic"] = kwargs.get("deadline_monotonic")
        return original(repo_map_arg, symbol, **kwargs)

    monkeypatch.setattr(repo_map, "build_symbol_impact_from_map", _spy)

    repo_map.build_symbol_impact("f1", str(tmp_path))  # no deadline

    assert recorded.get("deadline_monotonic") is None


# --- Loop D: build_symbol_refs_from_map's second (string_refs) pass over bounded_files -----------


def _make_refs_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    (src / "target.py").write_text("def widget():\n    return 1\n", encoding="utf-8")
    for index in range(count):
        (src / f"m{index}.py").write_text(f"x = {index}\n", encoding="utf-8")


def test_step52_refs_string_refs_loop_breaks_mid_scan(tmp_path: Path, monkeypatch) -> None:
    # loop D: the string_refs pass ran AFTER the deadline-checked main reference scan with no bound
    # of its own. Hook _string_literal_references directly (it runs unconditionally per bounded
    # file, regardless of content) to prove this SECOND pass breaks mid-scan even when the FIRST
    # pass completed cleanly inside budget.
    _make_refs_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original = repo_map._string_literal_references
    call_count = {"n": 0}

    def _advancing(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_string_literal_references", _advancing)

    result = repo_map.build_symbol_refs_from_map(rm, "widget", deadline_monotonic=base + 3.0)

    assert 0 < call_count["n"] < 7  # 7 bounded files (target.py + 6 m*.py) -- cut short
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step52_refs_string_refs_loop_none_deadline_is_unaffected(tmp_path: Path) -> None:
    _make_refs_repo(tmp_path, 3)
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_refs_from_map(rm, "widget")  # no deadline

    assert "partial" not in result


def test_step52_string_literal_references_reads_via_cached_helper(tmp_path: Path) -> None:
    # Fix D size-guard bundle: _string_literal_references now reads through
    # _read_source_text_cached instead of a raw path.read_text -- confirm it still finds a literal
    # occurrence correctly (behavior-preserving) and shares the cache (same content object) with a
    # second call on the SAME unmodified file.
    target = tmp_path / "m.py"
    target.write_text('ALIAS = "widget"\n', encoding="utf-8")

    first = repo_map._string_literal_references(target, "widget")
    second = repo_map._string_literal_references(target, "widget")

    assert len(first) == 1
    assert first[0]["occurrence"] == "string-literal"
    assert first == second


# --- Regression: the "main"-shape high-fan-out symbol that motivated this whole task -------------


def _make_high_fan_out_repo(root: Path, definition_count: int, test_count: int) -> None:
    # Shape of the real-world "main" 23x regression (task #52): MANY definitions of the SAME
    # symbol name with ~ZERO cross-import edges between them -> _preferred_definition_files's
    # import-consumer scoring loop finds nothing (every score stays 0) and falls back to the FULL
    # unfiltered definition_files list, which then floods _relevant_tests_for_symbol's
    # O(definitions x tests) any() loops unbounded.
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(definition_count):
        (src / f"mod{index}.py").write_text(f"def main():\n    return {index}\n", encoding="utf-8")
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    for index in range(test_count):
        # None of these import any mod*.py -- unrelated tests, exactly like the real regression's
        # test suite (many tests, none of them importing every single "main" definition).
        (tests_dir / f"test_other{index}.py").write_text(
            f"def test_unrelated_{index}():\n    assert {index} == {index}\n", encoding="utf-8"
        )


def test_step52_high_fan_out_symbol_regression_closes_23x_mechanism(
    tmp_path: Path, monkeypatch
) -> None:
    # THE regression this whole task exists to close: a symbol shaped like "main" (many
    # definitions, ~0 import edges) used to blow --deadline by up to 23x on a real repo because
    # _preferred_definition_files fell back to the FULL unfiltered definition list, flooding the
    # (then-unguarded) _relevant_tests_for_symbol loop with O(definitions x tests) unguarded work.
    # Prove the fix bounds the TOTAL _file_imports_symbol_from_definition call volume (the shared
    # hot path across every loop in this chain) to well under what an unbounded run would cost,
    # not just one loop tested in isolation.
    definition_count = 12
    test_count = 12
    _make_high_fan_out_repo(tmp_path, definition_count, test_count)
    rm = repo_map.build_repo_map(str(tmp_path))
    handles = _install_import_check_advancing_clock(monkeypatch)
    base, call_count = handles["base"], handles["call_count"]

    # Unbounded, this shape drives _preferred_definition_files through up to
    # test_count x definition_count candidate checks, then _relevant_tests_for_symbol through up
    # to another test_count x definition_count -- 2x that product if nothing bounds either loop.
    unbounded_ceiling = 2 * definition_count * test_count

    result = repo_map.build_symbol_callers_from_map(rm, "main", deadline_monotonic=base + 20.0)

    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert 0 < call_count["n"] < unbounded_ceiling


def test_step52_high_fan_out_symbol_no_deadline_still_completes(tmp_path: Path) -> None:
    # Golden-parity companion: without --deadline, the same high-fan-out shape still completes
    # (just slower) and is NOT marked partial -- the fix only changes behavior when a deadline is
    # actually supplied.
    _make_high_fan_out_repo(tmp_path, 5, 5)
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_callers_from_map(rm, "main")  # no deadline

    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"


# =================================================================================================
# Task #103 Fix 2: build_context_pack_from_map / _build_context_pack_from_map accepted NO deadline
# parameter at all and ran their full symbol-scoring cost unconditionally -- called from BOTH
# build_symbol_impact_from_map (repo_map.py:~13855) and build_symbol_callers_from_map
# (repo_map.py:~15168), so --deadline leaked wall-clock through this shared helper on every
# impact/callers/blast-radius call (profiled at ~13% of callers' cost). Mirrors the #52/#61
# _DeadlineBreakFlag idiom exactly: gate the symbol-scoring loop (the single largest
# repo-size-proportional loop in context-pack construction), fold the early-break signal into
# `partial` at both call sites.
# =================================================================================================


def _make_many_symbols_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index}.py").write_text(
            f"def widget_{index}():\n    return {index}\n", encoding="utf-8"
        )


def test_step103_context_pack_honors_already_expired_deadline(tmp_path: Path) -> None:
    _make_many_symbols_repo(tmp_path, 8)
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()

    result = repo_map.build_context_pack_from_map(
        rm, "widget", deadline_monotonic=time.monotonic() - 1.0, deadline_hit=flag
    )

    assert flag.hit is True
    assert isinstance(result, dict)  # returns a valid (partial) payload -- does not raise or hang


def test_step103_context_pack_none_deadline_is_unaffected(tmp_path: Path) -> None:
    # Golden-parity guard: deadline_monotonic=None (the default, and every pre-existing call site's
    # behavior) never trips the new gate.
    _make_many_symbols_repo(tmp_path, 8)
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()

    repo_map.build_context_pack_from_map(rm, "widget", deadline_hit=flag)

    assert flag.hit is False


def test_step103_context_pack_loop_breaks_mid_scan_not_just_pre_check(
    tmp_path: Path, monkeypatch
) -> None:
    # The sharper claim: the deadline crosses WHILE the scoring loop is running, not merely
    # pre-expired -- proves a PARTIAL (not zero, not full) symbol set was scored.
    _make_many_symbols_repo(tmp_path, 10)
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score = repo_map._score_symbol
    call_count = {"n": 0}

    def _advancing_score(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_score(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_score_symbol", _advancing_score)
    flag = repo_map._DeadlineBreakFlag()

    repo_map.build_context_pack_from_map(
        rm, "widget", deadline_monotonic=base + 4.0, deadline_hit=flag
    )

    assert flag.hit is True
    assert 0 < call_count["n"] < 10  # cut short mid-scan, not exhausted


def test_step103_impact_marks_partial_on_context_pack_timeout_alone(
    tmp_path: Path, monkeypatch
) -> None:
    # Isolation test (mirrors test_step52_impact_from_map_marks_partial_on_relevant_tests_timeout_
    # alone above): _score_symbol is unique to the context-pack scoring loop (no other sibling loop
    # in build_symbol_impact_from_map calls it), so hooking it isolates the context-pack loop as the
    # SOLE source of the partial signal.
    _make_many_symbols_repo(tmp_path, 10)
    (tmp_path / "src" / "target.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score = repo_map._score_symbol
    call_count = {"n": 0}

    def _advancing_score(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_score(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_score_symbol", _advancing_score)

    result = repo_map.build_symbol_impact_from_map(rm, "shared", deadline_monotonic=base + 3.0)

    assert call_count["n"] > 0  # the context-pack scoring loop actually ran
    assert result.get("partial") is True
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step103_callers_marks_partial_on_context_pack_timeout_alone(
    tmp_path: Path, monkeypatch
) -> None:
    _make_many_symbols_repo(tmp_path, 10)
    (tmp_path / "src" / "target.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score = repo_map._score_symbol
    call_count = {"n": 0}

    def _advancing_score(*args, **kwargs):
        call_count["n"] += 1
        clock["t"] += 1.0
        return original_score(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_score_symbol", _advancing_score)

    result = repo_map.build_symbol_callers_from_map(rm, "shared", deadline_monotonic=base + 3.0)

    assert call_count["n"] > 0  # the context-pack scoring loop actually ran
    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
    assert result["deadline_limit"]["deadline_exceeded"] is True


def test_step103_impact_no_deadline_context_pack_stays_complete(tmp_path: Path) -> None:
    # Golden-parity companion: without --deadline, behavior is unchanged.
    _make_many_symbols_repo(tmp_path, 6)
    (tmp_path / "src" / "target.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_impact_from_map(rm, "shared")  # no deadline

    assert "partial" not in result
    assert "deadline_limit" not in result


def test_step103_callers_no_deadline_context_pack_stays_complete(tmp_path: Path) -> None:
    _make_many_symbols_repo(tmp_path, 6)
    (tmp_path / "src" / "target.py").write_text("def shared():\n    return 1\n", encoding="utf-8")
    rm = repo_map.build_repo_map(str(tmp_path))

    result = repo_map.build_symbol_callers_from_map(rm, "shared")  # no deadline

    assert "partial" not in result
    assert result["graph_completeness"] == "moderate"


# ---------------------------------------------------------------------------------------------
# dogfood finding 1: `tg agent`/`tg codemap` --deadline was threaded into build_repo_map (the
# SCAN) but the POST-MAP stages (context-pack symbol/graph scoring, feeding agent/context/
# edit-plan alike) ran unbounded AND unstamped -- a real whole-repo `tg agent --deadline 8`
# silently overran to ~20s at exit 0, partial=None. build_context_pack_from_map (the public
# build_context_pack_from_map -> _build_context_pack_from_map seam every one of those commands
# shares) now self-stamps `partial`/`deadline_limit` from its OWN internal deadline_hit readback
# even when the caller supplies none -- the pre-fix shape for agent/context/edit-plan, none of
# which passed a deadline_hit flag before this change.
# ---------------------------------------------------------------------------------------------


def _make_pagerank_stress_repo(root: Path, symbol_count: int) -> None:
    """``symbol_count`` trivial one-function modules that all import a shared ``hub.py`` --
    non-empty reverse_importers (a real hub with real fan-in), so pagerank's per-node sort has
    genuine (if small) work to do, not the trivially-empty-set fast path."""
    src = root / "src"
    src.mkdir(parents=True)
    (src / "hub.py").write_text("def hub_fn():\n    return 0\n", encoding="utf-8")
    for index in range(symbol_count):
        (src / f"m{index}.py").write_text(
            f"from src.hub import hub_fn\n\n\ndef f{index}():\n    return hub_fn() + {index}\n",
            encoding="utf-8",
        )


def test_build_context_pack_from_map_self_stamps_partial_when_pagerank_abandons(
    tmp_path: Path, monkeypatch
) -> None:
    """Council must-fix #2 (stamp the partial boolean DIRECTLY): the symbol-scoring loop finishes
    INSIDE budget, but the very next sibling stage -- _personalized_reverse_import_pagerank --
    crosses the SAME shared deadline. Before this fix, build_context_pack_from_map never read
    back its own deadline_hit flag at all (agent/context/edit-plan all call it with no deadline_
    hit argument), so this whole class of post-symbol-scoring break was silently unstamped."""
    _make_pagerank_stress_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))

    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_score = repo_map._score_symbol

    def _advancing_score(*args, **kwargs):
        clock["t"] += 1.0
        return original_score(*args, **kwargs)

    monkeypatch.setattr(repo_map, "_score_symbol", _advancing_score)

    # symbol-scoring calls _score_symbol exactly once per symbol in rm["symbols"], checking the
    # deadline BEFORE each call (clock values base .. base+total-1) -- a deadline of
    # base+total-0.5 lets every one of those checks through (all strictly less), leaving the
    # clock parked at base+total once the loop naturally ends. That is exactly the value
    # pagerank's OWN new iteration-boundary check reads first, so it aborts on iteration 0.
    total_symbols = len(rm["symbols"])
    assert total_symbols > 0, "fixture must actually produce symbols to score"
    deadline_monotonic = base + total_symbols - 0.5

    payload = repo_map.build_context_pack_from_map(rm, "f0", deadline_monotonic=deadline_monotonic)

    assert payload.get("partial") is True
    assert payload.get("deadline_limit") == {"deadline_exceeded": True}


def test_build_context_pack_from_map_no_deadline_stays_complete(tmp_path: Path) -> None:
    _make_pagerank_stress_repo(tmp_path, 6)
    rm = repo_map.build_repo_map(str(tmp_path))

    payload = repo_map.build_context_pack_from_map(rm, "f0")  # no deadline

    assert "partial" not in payload
    assert "deadline_limit" not in payload


def test_build_context_pack_from_map_honors_caller_supplied_deadline_hit_too(
    tmp_path: Path,
) -> None:
    """A caller that DOES pass its own `_DeadlineBreakFlag` (mirroring the callers/impact/blast-
    radius fold-in pattern) must still see it flip to `.hit = True` -- the self-stamp fix must
    not swallow/replace a caller-supplied flag with an internal one it never reads back."""
    _make_pagerank_stress_repo(tmp_path, 4)
    rm = repo_map.build_repo_map(str(tmp_path))
    flag = repo_map._DeadlineBreakFlag()

    payload = repo_map.build_context_pack_from_map(
        rm, "f0", deadline_monotonic=time.monotonic() - 1.0, deadline_hit=flag
    )

    assert payload.get("partial") is True
    assert flag.hit is True
