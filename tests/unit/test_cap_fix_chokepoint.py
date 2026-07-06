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
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import repo_map, session_store
from tensor_grep.cli.main import _DEFAULT_AGENT_REPO_SCAN_LIMIT, app

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
    assert "scan_remediation" in result


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
