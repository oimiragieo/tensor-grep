from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.freshness import check_freshness, render_freshness_text
from tensor_grep.cli.main import app
from tensor_grep.cli.unified_cache import (
    MANIFEST_REL_PATH,
    build_source_address_contract,
    update_view_states,
)


def _seed_unified_cache(root: Path, files: list[Path]) -> str:
    merkle_root, source_address = build_source_address_contract(root, files)
    assert update_view_states(
        root,
        merkle_root,
        {"symbols_v1": (True, 1), "graph_v1": (True, 1)},
        source_address=source_address,
    )
    return merkle_root


def test_freshness_reports_current_when_unified_cache_is_populated(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mod_py = repo / "mod.py"
    mod_py.write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")

    _seed_unified_cache(repo, [mod_py])

    payload = check_freshness(repo)
    assert payload["session_count"] == 0
    assert payload["stale_count"] == 0
    assert payload["unknown_count"] == 0
    assert not payload.get("result_incomplete")
    assert payload["unified_cache"]["status"] == "current"

    runner = CliRunner()
    result = runner.invoke(app, ["freshness", str(repo), "--json"])
    assert result.exit_code == 0, result.output
    cli_payload = json.loads(result.stdout)
    assert cli_payload["unified_cache"]["status"] == "current"


def test_freshness_subdirectory_finds_root_unified_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (repo / ".git").mkdir()
    mod_py = src / "mod.py"
    mod_py.write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")

    _seed_unified_cache(repo, [mod_py])

    runner = CliRunner()
    result = runner.invoke(app, ["freshness", str(src), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert not payload.get("result_incomplete")
    assert payload["unified_cache"]["status"] == "current"


def test_freshness_reports_stale_when_code_changes_after_unified_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mod_py = repo / "mod.py"
    mod_py.write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")

    _seed_unified_cache(repo, [mod_py])

    mod_py.write_text("def alpha() -> int:\n    return 42\n", encoding="utf-8")
    st = mod_py.stat()
    os.utime(mod_py, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    payload = check_freshness(repo)
    assert payload["stale_count"] == 1
    assert payload["unified_cache"]["status"] == "stale"
    assert "manifest_v1.json" in render_freshness_text(payload)

    runner = CliRunner()
    result = runner.invoke(app, ["freshness", str(repo), "--json"])
    assert result.exit_code == 2, result.output


def test_freshness_fails_closed_on_tampered_unified_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    mod_py = repo / "mod.py"
    mod_py.write_text("def alpha() -> int:\n    return 1\n", encoding="utf-8")

    _seed_unified_cache(repo, [mod_py])

    manifest_path = repo / MANIFEST_REL_PATH
    envelope = json.loads(manifest_path.read_text(encoding="utf-8"))
    envelope["mac"] = "0" * 64
    manifest_path.write_text(json.dumps(envelope), encoding="utf-8")

    payload = check_freshness(repo)
    assert payload["unknown_count"] == 1
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "cache_state_unreadable"
    assert payload["unified_cache"]["status"] == "unknown"

    runner = CliRunner()
    result = runner.invoke(app, ["freshness", str(repo), "--json"])
    assert result.exit_code == 2, result.output
