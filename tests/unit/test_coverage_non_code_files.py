from __future__ import annotations

import json
import os
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.cli.unified_cache import build_source_address_contract, update_view_states


def test_coverage_clean_repo_with_non_code_files_exits_zero(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (project / "config.yml").write_text("enabled: true\n", encoding="utf-8")

    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output

    result = runner.invoke(app, ["coverage", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["unparsed"] == []
    assert payload["non_code_skipped"]["count"] == 3
    assert payload["non_code_skipped"]["sample"] == [
        "README.md",
        "config.yml",
        "pyproject.toml",
    ]


def test_coverage_unregistered_code_file_remains_unparsed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    (project / "Main.kt").write_text('fun main() { println("hi") }\n', encoding="utf-8")

    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output

    result = runner.invoke(app, ["coverage", str(project), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["unparsed"] == [{"path": "Main.kt", "reason": "missing_grammar"}]
    assert payload["non_code_skipped"] == {"count": 0, "sample": []}


def test_coverage_broken_python_file_remains_parse_error(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output

    result = runner.invoke(app, ["coverage", str(project), "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is False
    assert payload["unparsed"] == [{"path": "broken.py", "reason": "parse_error"}]


def test_coverage_unindexed_non_code_files_do_not_break_completeness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")

    runner = CliRunner()
    opened = runner.invoke(app, ["session", "open", str(project), "--json"])
    assert opened.exit_code == 0, opened.output

    (project / "README.md").write_text("# Added later\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    result = runner.invoke(app, ["coverage", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["unindexed"] == []
    assert payload["stale"] == []
    assert payload["unparsed"] == []
    assert payload["non_code_skipped"]["count"] == 2


def test_coverage_unified_cache_manifest_marks_repo_complete(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    main_py = project / "main.py"
    main_py.write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    (project / "README.md").write_text("# Demo\n", encoding="utf-8")

    merkle_root, source_address = build_source_address_contract(project, [main_py])
    assert update_view_states(
        project,
        merkle_root,
        {"symbols_v1": (True, 1), "graph_v1": (True, 1)},
        source_address=source_address,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["coverage", str(project), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["complete"] is True
    assert payload["unindexed"] == []
    assert payload["stale"] == []
    assert payload["unparsed"] == []


def test_coverage_unified_cache_subdirectory_and_stale_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / ".git").mkdir()
    mod_py = src / "mod.py"
    mod_py.write_text("def run() -> int:\n    return 1\n", encoding="utf-8")

    merkle_root, source_address = build_source_address_contract(project, [mod_py])
    assert update_view_states(
        project,
        merkle_root,
        {"symbols_v1": (True, 1), "graph_v1": (True, 1)},
        source_address=source_address,
    )

    runner = CliRunner()
    clean = runner.invoke(app, ["coverage", str(src), "--json"])
    assert clean.exit_code == 0, clean.output
    assert json.loads(clean.stdout)["complete"] is True

    mod_py.write_text("def run() -> int:\n    return 2\n", encoding="utf-8")
    st = mod_py.stat()
    os.utime(mod_py, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    stale = runner.invoke(app, ["coverage", str(src), "--json"])
    assert stale.exit_code == 2, stale.output
    stale_payload = json.loads(stale.stdout)
    assert stale_payload["complete"] is False
    assert stale_payload["unindexed"] == ["mod.py"]
