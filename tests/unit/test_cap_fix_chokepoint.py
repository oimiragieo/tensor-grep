"""TDD for backlog #1 (Fable+thinktank plan, 2026-07-06): the cap-fix chokepoint.

Two changes, tested together because they only make sense as a pair:

1. ``repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT`` (and the CLI's shared
   ``main._DEFAULT_AGENT_REPO_SCAN_LIMIT``) raised 512 -> 2000 so ROUTING commands
   (defs/edit-plan/agent/context-render) stop misrouting on repos with more than 512 files --
   a file past the old cap never entered the map at all, so the right file could not be found.
2. A NEW internal chokepoint, ``repo_map.CALLER_SCAN_FILE_CEILING`` (512), caps the file
   universe that the CALLER-SCAN functions (``build_symbol_callers_from_map``,
   ``build_symbol_blast_radius_from_map``, ``build_symbol_refs_from_map``) actually walk for
   their slow per-file prefilter + re-parse, REGARDLESS of how large the map/session repo_map
   is. This is what keeps callers/refs/blast-radius fast even though the map default just grew
   4x, and it is also what fixes the session-blast-radius leak (session_store.py calls
   ``build_symbol_blast_radius_from_map`` directly on a full stored session map with no
   per-command cap to intercept it).

Gate items (a)-(d) below map 1:1 onto the plan's TDD list.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import repo_map, session_store
from tensor_grep.cli.main import _DEFAULT_AGENT_REPO_SCAN_LIMIT, _scan_truncation_warning, app

runner = CliRunner()


def _make_flat_repo(
    root: Path,
    count: int,
    *,
    target_index: int | None = None,
    symbol: str | None = None,
) -> Path:
    """Build a project with ``count`` trivial .py files in a single directory (one top-level
    walk bucket), so the deterministic alphabetical file order is easy to reason about. When
    ``target_index`` is given, that file ALSO defines ``symbol`` -- callers can place it past a
    known cap boundary."""
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    width = max(5, len(str(count)))
    for index in range(count):
        body = f"def helper_{index}():\n    return {index}\n"
        if target_index is not None and index == target_index and symbol:
            body += f"\n\ndef {symbol}():\n    return {index}\n"
        (src / f"m{index:0{width}d}.py").write_text(body, encoding="utf-8")
    return project


def _make_flat_repo_with_tests(
    root: Path,
    source_count: int,
    *,
    target_index: int,
    symbol: str,
) -> Path:
    """F1 fixture: ``source_count`` trivial source files (one directory, so the ceiling slice
    is genuinely source-heavy) PLUS 5 pytest-named test files (``_is_test_file`` classifies by
    ``test_`` filename prefix regardless of directory), one of which (``test_qe.py``) actually
    references ``symbol`` -- reproducing the dogfood regression: a >CALLER_SCAN_FILE_CEILING
    source-only universe strands 100% of the (source-first-then-tests-ordered) test files past
    the ceiling slice, dropping the test file's ref."""
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    width = max(5, len(str(source_count)))
    for index in range(source_count):
        body = f"def helper_{index}():\n    return {index}\n"
        if index == target_index:
            body += f"\n\nclass {symbol}:\n    def run(self):\n        return True\n"
        (src / f"m{index:0{width}d}.py").write_text(body, encoding="utf-8")
    for name in ("test_a.py", "test_b.py", "test_c.py", "test_d.py"):
        (src / name).write_text("def test_noop():\n    assert True\n", encoding="utf-8")
    target_module = f"m{target_index:0{width}d}"
    (src / "test_qe.py").write_text(
        f"from src.{target_module} import {symbol}\n\n\n"
        f"def test_query_engine():\n    engine = {symbol}()\n    assert engine.run()\n",
        encoding="utf-8",
    )
    return project


def test_constants_locked_to_the_plan() -> None:
    assert repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT == 2000
    assert repo_map.CALLER_SCAN_FILE_CEILING == 512
    # The CLI's shared routing/caller-scan default must track the map limit -- this is the
    # "necessary correction" over the plan's literal wording: main.py's `--max-repo-files`
    # default for defs/edit-plan/agent/context-render is a SEPARATE literal
    # (`_DEFAULT_AGENT_REPO_SCAN_LIMIT`), not `DEFAULT_AGENT_REPO_MAP_LIMIT` -- bumping only the
    # repo_map.py constant would silently leave those CLI commands defaulting to 512.
    assert _DEFAULT_AGENT_REPO_SCAN_LIMIT == repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT


# --- (a) routing commands find a symbol whose definition sits past the OLD 512-file cap -------


def test_defs_finds_symbol_past_512_at_the_new_default(tmp_path: Path) -> None:
    project = _make_flat_repo(tmp_path, 600, target_index=550, symbol="find_me_past_512")

    # Reproduce the bug at the OLD cap: the symbol's file never enters the map.
    old_cap_result = repo_map.build_symbol_defs(
        "find_me_past_512", str(project), max_repo_files=512
    )
    assert old_cap_result.get("no_match") is True

    # `tg defs` with NO --max-repo-files override uses the CLI's real default.
    result = runner.invoke(app, ["defs", str(project), "find_me_past_512", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload.get("no_match") is not True
    assert any(str(d["file"]).endswith("m00550.py") for d in payload["definitions"])


def test_edit_plan_routes_to_symbol_past_512_at_the_new_default(tmp_path: Path) -> None:
    project = _make_flat_repo(tmp_path, 600, target_index=550, symbol="find_me_past_512")

    result = runner.invoke(app, ["edit-plan", str(project), "find_me_past_512", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    all_paths = [
        *payload.get("files", []),
        *[str(s.get("file", "")) for s in payload.get("symbols", [])],
    ]
    assert any(path.endswith("m00550.py") for path in all_paths), payload


# --- (b) caller-scan internally bounds its file universe + honesty flag -----------------------


def test_build_symbol_callers_from_map_bounds_scan_to_ceiling(tmp_path, monkeypatch) -> None:
    project = _make_flat_repo(tmp_path, 700, target_index=0, symbol="target_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)
    assert len(rmap["files"]) + len(rmap.get("tests", [])) >= 700

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_callers_from_map(rmap, "target_symbol")

    assert calls["n"] <= repo_map.CALLER_SCAN_FILE_CEILING
    assert calls["n"] == repo_map.CALLER_SCAN_FILE_CEILING
    assert result.get("result_incomplete") is True
    # truthy, not just key-present: the pre-fix setdefault left scan_remediation=None (the dogfood bug)
    assert result.get("scan_remediation")


def test_build_symbol_callers_from_map_below_ceiling_stays_complete(tmp_path, monkeypatch) -> None:
    project = _make_flat_repo(tmp_path, 300, target_index=0, symbol="target_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=300)

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_callers_from_map(rmap, "target_symbol")

    assert calls["n"] < repo_map.CALLER_SCAN_FILE_CEILING
    assert result.get("result_incomplete") is not True


def test_build_symbol_refs_from_map_bounds_scan_to_ceiling(tmp_path, monkeypatch) -> None:
    project = _make_flat_repo(tmp_path, 700, target_index=0, symbol="target_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_refs_from_map(rmap, "target_symbol")

    assert result.get("result_incomplete") is True


def test_build_symbol_blast_radius_from_map_bounds_scan_to_ceiling(tmp_path, monkeypatch) -> None:
    project = _make_flat_repo(tmp_path, 700, target_index=0, symbol="target_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_blast_radius_from_map(rmap, "target_symbol")

    assert calls["n"] <= repo_map.CALLER_SCAN_FILE_CEILING
    assert result.get("result_incomplete") is True


# --- (c) the session-blast-radius leak: build_symbol_blast_radius_from_map is called directly --
# --- on the full stored session repo_map, with no per-command cap to intercept it --------------


def test_session_blast_radius_leak_fix_bounds_scan(tmp_path, monkeypatch) -> None:
    project = _make_flat_repo(tmp_path, 700, target_index=0, symbol="leaked_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)

    monkeypatch.setattr(
        session_store,
        "_load_session_payload",
        lambda session_id, path, **kwargs: {"repo_map": rmap},
    )

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = session_store.session_blast_radius("fake-session", "leaked_symbol", str(project))

    assert calls["n"] <= repo_map.CALLER_SCAN_FILE_CEILING
    assert result.get("result_incomplete") is True


# --- (d) a genuinely oversized (>2000-file) tree still trips the exit-2 truncation contract ----


def test_defs_on_oversized_repo_still_exits_2(tmp_path: Path) -> None:
    project = _make_flat_repo(tmp_path, 2100, target_index=2050, symbol="beyond_new_cap")

    result = runner.invoke(app, ["defs", str(project), "beyond_new_cap", "--json"])

    assert result.exit_code == 2, result.stdout
    payload = json.loads(result.stdout)
    assert payload.get("no_match") is True
    assert payload.get("result_incomplete") is True
    scan_limit = payload.get("scan_limit")
    assert isinstance(scan_limit, dict)
    assert scan_limit.get("possibly_truncated") is True


# --- BLOCKER (Fable final review of #405): blast-radius must exit 2 on a caller-scan CEILING
#     truncation (a SCAN truncation), while a mere --max-callers OUTPUT cap stays exit 0. ---


def test_blast_radius_exits_2_on_caller_scan_ceiling_truncation(tmp_path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _spy(symbol, path=".", **_kwargs):
        # ceiling truncation: caller_scan_truncated set, but NOT partial / scan_limit (the 2000-map is
        # itself complete) -- the blast-radius gate must STILL exit 2, not silently exit 0 at 512.
        return {
            "symbol": symbol,
            "path": str(path),
            "definitions": [{"file": "m.py", "line": 1}],
            "callers": [{"file": "m.py", "line": 2}],
            "files": ["m.py"],
            "tests": [],
            "result_incomplete": True,
            "caller_scan_truncated": True,
            "scan_remediation": "caller-scan bounded to 512 files",
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _spy)
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "f", "--json"])
    assert result.exit_code == 2, result.stdout


def test_blast_radius_output_cap_only_stays_exit_0(tmp_path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def _spy(symbol, path=".", **_kwargs):
        # OUTPUT cap only (--max-callers trims a COMPLETE analysis) -> exit 0, NOT 2.
        return {
            "symbol": symbol,
            "path": str(path),
            "definitions": [{"file": "m.py", "line": 1}],
            "callers": [{"file": "m.py", "line": 2}],
            "files": ["m.py"],
            "tests": [],
            "result_incomplete": True,
            "output_limit": {"possibly_truncated": True, "callers_truncated": True},
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius", _spy)
    result = runner.invoke(app, ["blast-radius", str(tmp_path), "f", "--json"])
    assert result.exit_code == 0, result.stdout


# --- F1 (dogfood v1.42.0, 24->14 refs regression): the ceiling slice must ORDER literal-hit ---
# --- files + interleave tests BEFORE slicing, and stamp a structured caller_scan_limit caveat --


def test_refs_ceiling_orders_literal_hits_and_surfaces_caller_scan_limit(tmp_path: Path) -> None:
    project = _make_flat_repo_with_tests(tmp_path, 600, target_index=300, symbol="QueryEngine")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)
    assert len(rmap.get("tests", [])) == 5
    universe_size = len(rmap.get("files", [])) + len(rmap.get("tests", []))
    assert universe_size > repo_map.CALLER_SCAN_FILE_CEILING

    result = repo_map.build_symbol_refs_from_map(rmap, "QueryEngine")

    ref_file_names = {Path(str(ref["file"])).name for ref in result["references"]}
    assert "test_qe.py" in ref_file_names, result["references"]

    caller_scan_limit = result.get("caller_scan_limit")
    assert isinstance(caller_scan_limit, dict)
    assert caller_scan_limit.get("possibly_truncated") is True
    assert caller_scan_limit.get("ceiling") == repo_map.CALLER_SCAN_FILE_CEILING
    assert caller_scan_limit.get("files_total") == universe_size

    assert result.get("result_incomplete") is True
    assert _scan_truncation_warning(result) is not None


def test_callers_scan_still_bounded_at_ceiling_with_ordering_enabled(tmp_path, monkeypatch) -> None:
    """The ordering pass must not go UNBOUNDED: it probes at most
    CALLER_SCAN_ORDER_PROBE_CEILING files (for literal-hit ordering) and then walks the capped
    512-file window once for the real scan -- a fixed, non-quadratic cost, not a blowup."""
    project = _make_flat_repo_with_tests(tmp_path, 600, target_index=300, symbol="QueryEngine")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=700)
    universe_size = len(rmap.get("files", [])) + len(rmap.get("tests", []))
    assert universe_size < repo_map.CALLER_SCAN_ORDER_PROBE_CEILING

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_callers_from_map(rmap, "QueryEngine")

    assert (
        calls["n"] <= repo_map.CALLER_SCAN_ORDER_PROBE_CEILING + repo_map.CALLER_SCAN_FILE_CEILING
    )
    assert result.get("result_incomplete") is True


# --- F1-review HIGH (must fix): the ordering PROBE itself is a separate hot loop from the -------
# --- capped per-file scan loop, and must be bounded independently of the file universe size ------


def test_ordering_probe_bounded_on_universe_larger_than_probe_ceiling(
    tmp_path, monkeypatch
) -> None:
    """Regression for the F1-review HIGH finding: on a repo raised well past the default (e.g.
    via --max-repo-files), the ordering probe inside _order_caller_scan_candidates must NOT scan
    every file in the universe -- it must stop at CALLER_SCAN_ORDER_PROBE_CEILING regardless of
    how large the universe is. Total _file_may_contain_literal_symbol calls therefore bound to
    (probe ceiling) + (scan ceiling), never O(universe size)."""
    project = _make_flat_repo_with_tests(tmp_path, 3000, target_index=1500, symbol="QueryEngine")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=3100)
    universe_size = len(rmap.get("files", [])) + len(rmap.get("tests", []))
    assert universe_size > repo_map.CALLER_SCAN_ORDER_PROBE_CEILING

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    result = repo_map.build_symbol_refs_from_map(rmap, "QueryEngine")

    assert (
        calls["n"] <= repo_map.CALLER_SCAN_ORDER_PROBE_CEILING + repo_map.CALLER_SCAN_FILE_CEILING
    )
    # sanity: the probe ceiling is actually the binding constraint here, not a fluke of the
    # fixture -- prove it engaged by checking it's well under a naive full-universe scan.
    assert calls["n"] < universe_size
    assert result.get("result_incomplete") is True


def test_ordering_probe_unprobed_files_stay_eligible_not_dropped(tmp_path) -> None:
    """Files beyond the probe ceiling are never read for ordering, but must remain ELIGIBLE for
    the caller-scan (ordering-only, never filtering) -- the same TRAP the F1 fix's own docstring
    warns about, now also true of files the probe never got to."""
    sources = [Path(f"src_{i:05d}.py") for i in range(3000)]
    tests: list[Path] = []

    ordered = repo_map._order_caller_scan_candidates(sources, tests, symbol="Anything")

    assert len(ordered) == len(sources)
    assert set(ordered) == set(sources)


def test_ordering_probe_stops_early_when_deadline_already_elapsed(tmp_path, monkeypatch) -> None:
    """Suspenders: even below the count ceiling, an elapsed --deadline must stop the probe (not
    just the later per-file scan loop) -- the probe should fall back to leaving the remainder
    unprobed rather than reading every file's bytes."""
    sources = [tmp_path / f"src_{i:04d}.py" for i in range(600)]
    for current in sources:
        current.write_text("x = 1\n", encoding="utf-8")

    calls = {"n": 0}
    original = repo_map._file_may_contain_literal_symbol

    def _spy(path: Path, symbol: str) -> bool:
        calls["n"] += 1
        return original(path, symbol)

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _spy)

    # A deadline already in the past: the very first stride check (index 0) must trip it.
    elapsed_deadline = repo_map.time.monotonic() - 1.0
    ordered = repo_map._order_caller_scan_candidates(
        sources, [], symbol="Anything", deadline_monotonic=elapsed_deadline
    )

    assert calls["n"] == 0
    assert len(ordered) == len(sources)
    assert set(ordered) == set(sources)


# --- F1-review MEDIUM (must fix): the interleave itself, not just the literal-hits half ---------


def test_interleave_proportionally_reserves_proportional_positions() -> None:
    """Direct unit test of _interleave_proportionally's own documented contract: ANY prefix of
    the merged result carries roughly its proportional share of tests. Covers 1v3, 3v1, and a
    many-sources/few-tests shape (the ceiling-slice-relevant one)."""

    def _assert_proportional(sources: list[Path], tests: list[Path]) -> None:
        merged = repo_map._interleave_proportionally(sources, tests)
        assert len(merged) == len(sources) + len(tests)
        assert set(merged) == {*sources, *tests}
        total = len(merged)
        test_set = set(tests)
        for cut in sorted({1, max(1, total // 4), max(1, total // 2), total}):
            expected = math.ceil(cut * len(tests) / total)
            actual = sum(1 for current in merged[:cut] if current in test_set)
            assert abs(actual - expected) <= 1, (cut, expected, actual, sources, tests)

    # 1 source : 3 tests -- tests dominate, must front-load quickly.
    _assert_proportional([Path("s0.py")], [Path("t0.py"), Path("t1.py"), Path("t2.py")])
    # 3 sources : 1 test -- a lone test must still land early, not stranded at the tail.
    _assert_proportional([Path("s0.py"), Path("s1.py"), Path("s2.py")], [Path("t0.py")])
    # many sources, few tests -- the shape that actually matters for the ceiling slice.
    _assert_proportional(
        [Path(f"s{i:04d}.py") for i in range(500)], [Path(f"t{i}.py") for i in range(5)]
    )


def test_order_caller_scan_candidates_reserves_proportional_test_share_in_window(
    tmp_path, monkeypatch
) -> None:
    """F1-review MEDIUM fix: the existing ceiling test (test_qe.py) is a LITERAL hit, so it lands
    in the literal-hits block unconditionally and the assertion would pass even if
    _interleave_proportionally were entirely broken. This test forces the ONLY path into the
    bounded window to be the interleave: most source files are literal hits (crowding the front),
    but every test file is a literal MISS, so a test can only reach the 512-file window via
    proportional interleaving."""
    sources = [tmp_path / f"src_{i:04d}.py" for i in range(600)]
    tests = [tmp_path / f"test_{i:04d}.py" for i in range(20)]
    for current in [*sources, *tests]:
        current.write_text("x = 1\n", encoding="utf-8")

    hit_sources = set(sources[:400])  # 400 of 600 sources "match" -- a large crowding block.

    def _fake(path: Path, symbol: str) -> bool:
        return path in hit_sources

    monkeypatch.setattr(repo_map, "_file_may_contain_literal_symbol", _fake)

    ordered = repo_map._order_caller_scan_candidates(sources, tests, symbol="Anything")
    window = ordered[: repo_map.CALLER_SCAN_FILE_CEILING]
    tests_in_window = sum(1 for current in window if current in tests)

    total = len(sources) + len(tests)
    expected = math.ceil(repo_map.CALLER_SCAN_FILE_CEILING * len(tests) / total)
    assert tests_in_window > 0
    assert tests_in_window >= expected - 1
    # never drop: every file (hit or not, probed or not) remains present in the full ordering.
    assert set(ordered) == {*sources, *tests}
