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
    # loops never advance the fake clock at all) but build_symbol_impact_from_map's UNCONSTRAINED
    # _preferred_definition_files call (out of task #61's scope -- impact has no deadline param)
    # burns the remaining budget, so blast-radius's OWN direct call must observe an
    # already-exceeded deadline and fold that into partial by itself -- proving the fold-in is not
    # just piggy-backing on callers_payload["partial"].
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
    # ticks) finishes clean -- callers_payload is NOT partial. build_symbol_impact_from_map's
    # unconstrained call then burns another 6 ticks (clock -> base+12), pushing the shared deadline
    # into the past before blast-radius's own direct call even starts.
    result = repo_map.build_symbol_blast_radius_from_map(
        rm, "shared", deadline_monotonic=base + 10.0
    )

    assert result.get("partial") is True
    assert result["graph_completeness"] == "partial"
