"""Task #103: `tg impact` built its whole repo_map TWICE per invocation.

Root cause (profiler-confirmed, not a PageRank cost -- that's ~22ms and negligible): the CLI
`impact()` handler called two self-contained wrappers, `build_symbol_impact(...)` and
`build_symbol_callers(...)`, and EACH one independently called `build_repo_map(...)` from scratch
(so the whole repo was parsed twice) AND independently converted `--deadline` into a fresh
`deadline_monotonic = time.monotonic() + deadline_seconds` starting from ITS OWN call time (so
`--deadline N` silently allowed up to ~2N seconds of total wall-clock across the two passes).

The fix builds `repo_map` and `deadline_monotonic` ONCE in `impact()` and calls the pre-built-map
variants (`build_symbol_impact_from_map` / `build_symbol_callers_from_map`) against that shared
map -- the same proven pattern `build_symbol_blast_radius` already uses internally
(repo_map.py's own `build_repo_map(...)` once + two `_from_map` calls sharing one
`deadline_monotonic`), and the same pattern the daemon (session_store.py) and MCP server already
use across a session's whole repo_map.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import tensor_grep.cli.repo_map as repo_map
from tensor_grep.cli.main import app


def _make_repo(root: Path, count: int) -> None:
    src = root / "src"
    src.mkdir(parents=True)
    for index in range(count):
        (src / f"m{index}.py").write_text(
            f"def f{index}():\n    return {index}\n", encoding="utf-8"
        )


def _make_caller_repo(root: Path) -> None:
    (root / "m.py").write_text(
        "def widget():\n    return 1\n\n\ndef use():\n    return widget()\n", encoding="utf-8"
    )


def test_impact_builds_repo_map_exactly_once(tmp_path: Path, monkeypatch) -> None:
    # THE regression: before the fix this counted 2 (one build_repo_map call inside
    # build_symbol_impact, a second independent one inside build_symbol_callers) -- doubling
    # _imports_and_symbols_for_path calls (2 x file count) for a single `tg impact` invocation.
    _make_caller_repo(tmp_path)
    call_count = {"n": 0}
    original = repo_map.build_repo_map

    def _counting_build_repo_map(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _counting_build_repo_map)

    result = CliRunner().invoke(app, ["impact", "widget", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert call_count["n"] == 1, (
        f"build_repo_map invoked {call_count['n']} times for one `tg impact` call -- "
        "expected exactly 1 (a shared repo_map across the impact + callers passes)"
    )


def test_impact_builds_repo_map_exactly_once_even_when_symbol_not_found(
    tmp_path: Path, monkeypatch
) -> None:
    # The no_match branch skips the second (callers) pass entirely, but build_repo_map must still
    # only run once -- guards against a fix that only shares the map on the "found" path.
    _make_caller_repo(tmp_path)
    call_count = {"n": 0}
    original = repo_map.build_repo_map

    def _counting_build_repo_map(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _counting_build_repo_map)

    result = CliRunner().invoke(app, ["impact", "does_not_exist_anywhere", str(tmp_path), "--json"])

    assert result.exit_code == 1, result.output
    assert call_count["n"] == 1, f"build_repo_map invoked {call_count['n']} times, expected 1"


def test_impact_deadline_not_doubled_by_second_pass(tmp_path: Path, monkeypatch) -> None:
    # THE deadline-doubling regression: before the fix, build_symbol_impact's own repo_map build
    # could consume the FULL --deadline budget parsing files, then build_symbol_callers converted
    # --deadline into a SECOND fresh deadline_monotonic (time.monotonic() + deadline_seconds,
    # evaluated AFTER the first pass already advanced the clock) and burned a second full budget
    # rebuilding the repo_map from scratch -- up to ~2x the requested --deadline in total
    # wall-clock. A deterministic fake clock (advances only when a file is actually parsed) proves
    # the TOTAL parse-clock consumed by the whole `tg impact` call stays within one shared budget.
    _make_repo(tmp_path, 20)
    base = 1000.0
    clock = {"t": base}
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: clock["t"])
    original_parse = repo_map._imports_and_symbols_for_path

    def _clock_advancing_parse(path, **kwargs):
        clock["t"] += 1.0
        return original_parse(path, **kwargs)

    monkeypatch.setattr(repo_map, "_imports_and_symbols_for_path", _clock_advancing_parse)

    result = CliRunner().invoke(app, ["impact", "f1", str(tmp_path), "--deadline", "5", "--json"])

    assert result.exit_code in (0, 2), result.output
    total_ticks_consumed = clock["t"] - base
    # One shared 5-tick budget: allow a little slack for legitimate deadline-gated sibling loops
    # (#52/#61) that share the SAME deadline_monotonic, but nowhere near the old ~2x-budget
    # (10-tick) double-build behavior.
    assert total_ticks_consumed <= 7, (
        f"consumed {total_ticks_consumed} parse-ticks against a 5-tick --deadline budget -- "
        "deadline_monotonic was likely re-derived a second time (the pre-fix double-build bug)"
    )


def test_impact_deadline_none_still_builds_repo_map_once(tmp_path: Path, monkeypatch) -> None:
    # Golden-parity guard: no --deadline supplied is unaffected by the shared-map fix.
    _make_caller_repo(tmp_path)
    call_count = {"n": 0}
    original = repo_map.build_repo_map

    def _counting_build_repo_map(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(repo_map, "build_repo_map", _counting_build_repo_map)

    result = CliRunner().invoke(app, ["impact", "widget", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    assert call_count["n"] == 1
