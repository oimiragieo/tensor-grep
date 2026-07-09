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

backlog #57 (2026-07-09) raised ``CALLER_SCAN_FILE_CEILING`` again, 512 -> 2000, matching
``DEFAULT_AGENT_REPO_MAP_LIMIT``, now that #478's --deadline hard-bound closed the task #52 hang
risk that originally kept it frozen below the map default. Tests above that assumed a 700-ish
file fixture would exceed the (then 512) ceiling now monkeypatch ``CALLER_SCAN_FILE_CEILING``
(and, where relevant, the derived ``CALLER_SCAN_ORDER_PROBE_CEILING``) down to a small
test-local value instead of inflating fixtures to exceed 2000/8000 -- see the trailing
"backlog #57" section at the bottom of this file for the new coverage the raise itself needs
(golden-parity, the dogfood regression it fixes, the ``build_file_importers_from_map`` ceiling
branch, and truncation at the new, real 2000 value).
"""

from __future__ import annotations

import json
import math
import time
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


def _make_flat_repo_with_late_caller(
    root: Path,
    count: int,
    *,
    definition_index: int,
    caller_index: int,
    symbol: str,
) -> Path:
    """backlog #57 dogfood fixture: ``count`` trivial .py files in one directory; the file at
    ``definition_index`` DEFINES ``symbol``, and the file at ``caller_index`` (independently)
    IMPORTS + CALLS it. Unlike ``_make_flat_repo``'s ``target_index`` (which only places a
    definition), this reproduces the actual dogfood regression: a real caller sitting past the
    ceiling slice, not just the definition."""
    project = root / "project"
    src = project / "src"
    src.mkdir(parents=True)
    width = max(5, len(str(count)))
    definition_module = f"m{definition_index:0{width}d}"
    for index in range(count):
        if index == definition_index:
            body = f"def {symbol}():\n    return True\n"
        elif index == caller_index:
            body = (
                f"from src.{definition_module} import {symbol}\n\n\n"
                f"def uses_{symbol}():\n    return {symbol}()\n"
            )
        else:
            body = f"def helper_{index}():\n    return {index}\n"
        (src / f"m{index:0{width}d}.py").write_text(body, encoding="utf-8")
    return project


def test_constants_locked_to_the_plan() -> None:
    assert repo_map.DEFAULT_AGENT_REPO_MAP_LIMIT == 2000
    # backlog #57 (2026-07-09): raised 512 -> 2000 (matching DEFAULT_AGENT_REPO_MAP_LIMIT) now
    # that #478's --deadline hard-bound closed the #52 hang risk that kept this ceiling frozen
    # below the map default; see the module-level comment above CALLER_SCAN_FILE_CEILING.
    assert repo_map.CALLER_SCAN_FILE_CEILING == 2000
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
    # backlog #57: CALLER_SCAN_FILE_CEILING is now 2000 in production, well above this test's
    # 700-file fixture -- monkeypatch it back down to a small test-local value (precedent:
    # test_parse_product_cache.py's _SYMBOL_LITERAL_SEED_MAX_BYTES monkeypatch) so the fixture
    # still exceeds the ceiling and exercises truncation, independent of the production constant.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
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
    # backlog #57: see the matching comment in test_build_symbol_callers_from_map_bounds_scan_to_ceiling.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
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
    # backlog #57: see the matching comment in test_build_symbol_callers_from_map_bounds_scan_to_ceiling.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
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
    # backlog #57: see the matching comment in test_build_symbol_callers_from_map_bounds_scan_to_ceiling.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
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


def test_refs_ceiling_orders_literal_hits_and_surfaces_caller_scan_limit(
    tmp_path: Path, monkeypatch
) -> None:
    # backlog #57: see the matching comment in test_build_symbol_callers_from_map_bounds_scan_to_ceiling.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 50)
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
    # backlog #57: CALLER_SCAN_FILE_CEILING is now 2000 in production -- monkeypatch it back down
    # to a small test-local value so this 605-file fixture still exceeds the ceiling (the
    # derived CALLER_SCAN_ORDER_PROBE_CEILING is left at its production value; the assertion
    # below already only requires the universe to stay UNDER the probe ceiling, which holds
    # trivially at the production 8000).
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 50)
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
    (probe ceiling) + (scan ceiling), never O(universe size).

    backlog #57: production CALLER_SCAN_FILE_CEILING is now 2000 (CALLER_SCAN_ORDER_PROBE_CEILING
    = 4x = 8000), which would force an ~9000+ file fixture to keep exceeding the probe ceiling --
    slow and pointless disk I/O for a test whose CONTRACT is "the probe bounds itself regardless
    of the production constant's value". Monkeypatch both constants down to a small test-local
    pair (keeping the real 4x relationship) instead, mirroring the
    test_parse_product_cache.py::test_oversize_file_bypasses_parse_product_cache precedent of
    shrinking a production constant rather than inflating the fixture.
    """
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
    monkeypatch.setattr(repo_map, "CALLER_SCAN_ORDER_PROBE_CEILING", 400)
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
    but every test file is a literal MISS, so a test can only reach the ceiling window via
    proportional interleaving.

    backlog #57: CALLER_SCAN_FILE_CEILING is now 2000 in production, larger than this test's
    620-file (600+20) universe -- ``ordered[:CEILING]`` would then be the WHOLE list, making the
    "proportional share of a partial window" assertion vacuous. Monkeypatch the ceiling back down
    to a small test-local value so the window this test slices is a genuine partial prefix."""
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 100)
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


# =================================================================================================
# backlog #57 (2026-07-09): raise CALLER_SCAN_FILE_CEILING 512 -> 2000, + companion fixes.
# =================================================================================================
# (e) golden-parity: a repo that was ALREADY <=512 files must be byte-identical across the raise --
#     the early-return branch in _cap_caller_scan_files is unchanged code and fires either way.
# -------------------------------------------------------------------------------------------------


def test_below_512_repo_caller_result_is_byte_identical_across_the_raise(
    tmp_path, monkeypatch
) -> None:
    """Golden-parity gate (design doc: "<=512 repos byte-identical, early-return unchanged"):
    _cap_caller_scan_files's early-return (`len(files) <= CEILING: return files, False`) is
    unmodified code that fires for a <=512-file repo whether CEILING is the OLD 512 or the NEW
    2000 (400 <= both) -- raising the ceiling must not perturb such a repo's caller-scan result
    AT ALL. Proven empirically: run the identical fixture/map under both ceiling values and diff
    the JSON-serialized payload byte-for-byte."""
    production_ceiling = repo_map.CALLER_SCAN_FILE_CEILING
    project = _make_flat_repo(tmp_path, 400, target_index=50, symbol="stable_symbol")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=400)

    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 512)
    old_ceiling_result = repo_map.build_symbol_callers_from_map(rmap, "stable_symbol")
    assert old_ceiling_result.get("result_incomplete") is not True  # sanity: 400 <= 512 too

    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", production_ceiling)
    new_ceiling_result = repo_map.build_symbol_callers_from_map(rmap, "stable_symbol")
    assert new_ceiling_result.get("result_incomplete") is not True

    assert json.dumps(old_ceiling_result, sort_keys=True) == json.dumps(
        new_ceiling_result, sort_keys=True
    )


# ---------------------------------------------------------------------------------------------
# (f) the motivating dogfood regression: a caller sitting PAST the old 512-file index must now
#     be found -- absent/truncated at a (simulated) 512 ceiling, present and complete at 2000.
# ---------------------------------------------------------------------------------------------


def test_dogfood_caller_past_index_512_found_after_raise_absent_before(
    tmp_path, monkeypatch
) -> None:
    """The dogfood shape backlog #57 exists to fix: an ~805-file repo where the ONLY caller of a
    symbol sits past file-index 512 (alphabetically -- no ordering/interleaving in play here,
    since this fixture has no test-named files, matching the plain prefix-slice branch of
    _cap_caller_scan_files). RED against a mentally-512 ceiling (caller absent, result marked
    incomplete); GREEN at the real 2000 production ceiling (caller present, result complete)."""
    project = _make_flat_repo_with_late_caller(
        tmp_path, 805, definition_index=0, caller_index=600, symbol="moat_symbol"
    )
    rmap = repo_map.build_repo_map(str(project), max_repo_files=805)
    assert len(rmap["files"]) + len(rmap.get("tests", [])) == 805

    # RED (simulated OLD ceiling): the caller past index 512 must be dropped by the slice, and
    # the payload must honestly flag itself incomplete.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 512)
    old_result = repo_map.build_symbol_callers_from_map(rmap, "moat_symbol")
    old_caller_files = {Path(str(c["file"])).name for c in old_result.get("callers", [])}
    assert "m00600.py" not in old_caller_files, (
        "test fixture assumption broken: the caller must NOT be visible at the old 512 ceiling"
    )
    assert old_result.get("result_incomplete") is True

    # GREEN (the real, raised production ceiling): 805 <= 2000, so nothing is dropped at all.
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 2000)
    new_result = repo_map.build_symbol_callers_from_map(rmap, "moat_symbol")
    new_caller_files = {Path(str(c["file"])).name for c in new_result.get("callers", [])}
    assert "m00600.py" in new_caller_files, new_result.get("callers")
    assert new_result.get("result_incomplete") is not True


# ---------------------------------------------------------------------------------------------
# (g) the 4th consumer, build_file_importers_from_map, re-implements the ceiling INLINE and had
#     ZERO test coverage of its ceiling branch before this backlog item.
# ---------------------------------------------------------------------------------------------


def test_build_file_importers_from_map_ceiling_branch_marks_result_incomplete(
    tmp_path, monkeypatch
) -> None:
    """build_file_importers_from_map (`tg importers`) re-implements the CALLER_SCAN_FILE_CEILING
    cap inline rather than calling the shared _cap_caller_scan_files chokepoint, and had no test
    at all for what happens when the importer-candidate count exceeds the ceiling. Prove two
    things: (1) the ceiling branch fires (`caller_scan_limit.possibly_truncated`), the pre-#57
    behavior; and (2) the #57 companion fix -- it now ALSO calls _mark_result_incomplete (like
    build_symbol_callers_from_map / build_symbol_refs_from_map's own ceiling-hit sites), setting
    `result_incomplete` + `scan_remediation`, so a non-CLI consumer (MCP tools, a direct
    build_file_importers*/session_file_importers call) sees the same honesty signal the CLI's
    exit-2 gate already read out of `caller_scan_limit` alone."""
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 50)
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "target.js"
    target.write_text("export function shared() { return 1; }\n", encoding="utf-8")
    importer_count = 60  # > the monkeypatched 50-file ceiling
    for index in range(importer_count):
        (src / f"importer_{index:04d}.js").write_text(
            f'import {{ shared }} from "./target";\n'
            f"export function use_{index}() {{ return shared(); }}\n",
            encoding="utf-8",
        )

    rmap = repo_map.build_repo_map(str(project), max_repo_files=importer_count + 10)
    payload = repo_map.build_file_importers_from_map(rmap, str(target))

    caller_scan_limit = payload.get("caller_scan_limit")
    assert isinstance(caller_scan_limit, dict)
    assert caller_scan_limit.get("possibly_truncated") is True
    assert caller_scan_limit.get("ceiling") == 50
    assert caller_scan_limit.get("files_total") == importer_count

    assert payload.get("result_incomplete") is True
    assert payload.get("scan_remediation")


def test_build_file_importers_from_map_below_ceiling_stays_complete(tmp_path, monkeypatch) -> None:
    """Parity check for the sibling above: below the ceiling, no truncation signal at all."""
    monkeypatch.setattr(repo_map, "CALLER_SCAN_FILE_CEILING", 50)
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    target = src / "target.js"
    target.write_text("export function shared() { return 1; }\n", encoding="utf-8")
    (src / "importer_0.js").write_text(
        'import { shared } from "./target";\nexport function use_0() { return shared(); }\n',
        encoding="utf-8",
    )

    rmap = repo_map.build_repo_map(str(project), max_repo_files=10)
    payload = repo_map.build_file_importers_from_map(rmap, str(target))

    assert payload.get("caller_scan_limit") is None
    assert payload.get("result_incomplete") is not True


# ---------------------------------------------------------------------------------------------
# (h) truncation past the NEW, real ceiling (2000) -- not a monkeypatched stand-in -- still
#     fires result_incomplete/caller_scan_limit/exit-2, and stays within the latency budget.
# ---------------------------------------------------------------------------------------------


def test_build_symbol_callers_from_map_bounds_scan_to_the_raised_ceiling(tmp_path: Path) -> None:
    """No monkeypatch here: exercises the REAL production CALLER_SCAN_FILE_CEILING (2000) against
    a repo that genuinely exceeds it, proving the raise didn't just move the truncation case out
    of reach -- it still fires, at the new, bigger value."""
    project = _make_flat_repo(tmp_path, 2500, target_index=0, symbol="target_symbol_past_2000")
    rmap = repo_map.build_repo_map(str(project), max_repo_files=2500)
    universe_size = len(rmap["files"]) + len(rmap.get("tests", []))
    assert universe_size > repo_map.CALLER_SCAN_FILE_CEILING == 2000

    start = time.monotonic()
    result = repo_map.build_symbol_callers_from_map(rmap, "target_symbol_past_2000")
    elapsed = time.monotonic() - start

    assert result.get("result_incomplete") is True
    caller_scan_limit = result.get("caller_scan_limit")
    assert isinstance(caller_scan_limit, dict)
    assert caller_scan_limit.get("possibly_truncated") is True
    assert caller_scan_limit.get("ceiling") == repo_map.CALLER_SCAN_FILE_CEILING
    assert caller_scan_limit.get("files_total") == universe_size
    # Loose wall-clock smoke (CI-safe latency proxy, per the design doc's "no speed claim
    # without numbers" -- but this is a synthetic proxy, NOT the real-repo Measure-Command the
    # design also requires; start generous, learn from CHANGELOG #444's too-tight 1.0s flake).
    assert elapsed < 30.0, f"callers scan at the raised ceiling took {elapsed:.2f}s (budget 30s)"


def test_callers_cli_exits_2_past_the_raised_ceiling(tmp_path: Path) -> None:
    """End-to-end companion of the test above: the CLI's exit-2 contract (main.py's
    _scan_truncation_warning / _annotate_result_completeness) still fires off the real 2000
    ceiling, not just off a monkeypatched stand-in."""
    project = _make_flat_repo(tmp_path, 2500, target_index=0, symbol="target_symbol_past_2000")

    result = runner.invoke(
        app,
        ["callers", str(project), "target_symbol_past_2000", "--max-repo-files", "2500", "--json"],
    )

    assert result.exit_code == 2, result.stdout
    payload = json.loads(result.stdout)
    assert payload.get("result_incomplete") is True
    caller_scan_limit = payload.get("caller_scan_limit")
    assert isinstance(caller_scan_limit, dict)
    assert caller_scan_limit.get("possibly_truncated") is True
