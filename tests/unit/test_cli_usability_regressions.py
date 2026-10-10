from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.cli.repo_map import _iter_repo_files
from tensor_grep.core.config import SearchConfig
from tensor_grep.io.directory_scanner import DirectoryScanner


def _write_codemap_fixture(root: Path) -> Path:
    generated = root / "docs" / "code-map"
    generated.mkdir(parents=True)
    (generated / "_coverage.json").write_text(
        json.dumps({
            "schema_version": 1,
            "out": str(generated),
            "tree_manifest_sha256": "a" * 64,
            "files_total": 1,
            "folders_total": 1,
            "symbols_total": 0,
        }),
        encoding="utf-8",
    )
    return generated


def test_session_refresh_json_missing_id_has_actionable_hint(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["session", "refresh", ".", str(tmp_path), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "error": {
            "code": "session_not_found",
            "message": "Session not found: .",
            "hint": "pass SESSION_ID (see tg session list PATH --json)",
        }
    }
    text_result = CliRunner().invoke(app, ["session", "refresh", ".", str(tmp_path)])
    assert text_result.exit_code == 1
    assert "Hint: pass SESSION_ID (see tg session list PATH --json)" in text_result.stderr


def test_session_open_and_refresh_skip_codemap_and_disclose_it(tmp_path: Path) -> None:
    generated = _write_codemap_fixture(tmp_path)
    (generated / "src_app.md").write_text("generated page", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("normal guide", encoding="utf-8")
    runner = CliRunner()

    opened = runner.invoke(app, ["session", "open", str(tmp_path), "--json"])
    assert opened.exit_code == 0, opened.output
    opened_payload = json.loads(opened.stdout)
    assert opened_payload["file_count"] == 1
    assert opened_payload["excluded_generated_dirs"] == ["docs/code-map"]

    refreshed = runner.invoke(
        app,
        ["session", "refresh", opened_payload["session_id"], str(tmp_path), "--json"],
    )
    assert refreshed.exit_code == 0, refreshed.output
    refreshed_payload = json.loads(refreshed.stdout)
    assert refreshed_payload["file_count"] == 1
    assert refreshed_payload["excluded_generated_dirs"] == ["docs/code-map"]


def test_search_rank_skips_codemap_and_plain_search_walk_stays_unchanged(
    tmp_path: Path,
) -> None:
    generated = _write_codemap_fixture(tmp_path)
    (generated / "generated.md").write_text("codemapneedle", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("codemapneedle", encoding="utf-8")

    ranked = CliRunner().invoke(app, ["search", "codemapneedle", str(tmp_path), "--rank", "--json"])

    assert ranked.exit_code == 0, ranked.output
    ranked_payload = json.loads(ranked.stdout)
    assert ranked_payload["excluded_generated_dirs"] == ["docs/code-map"]
    assert all(
        "docs/code-map" not in match["file"].replace("\\", "/")
        for match in ranked_payload["matches"]
    )
    plain_candidates = list(DirectoryScanner(SearchConfig()).walk(str(tmp_path)))
    assert any("docs\\code-map" in path for path in plain_candidates)


def test_explicit_codemap_root_is_scanned_by_ranked_search_and_repo_walk(tmp_path: Path) -> None:
    generated = _write_codemap_fixture(tmp_path)
    page = generated / "guide.md"
    page.write_text("explicit codemap target", encoding="utf-8")

    scanner = DirectoryScanner(SearchConfig(rank_bm25=True))
    search_files = list(scanner.walk(str(generated)))
    repo_files = _iter_repo_files(generated, exclude_codemap_dirs=True)

    assert str(page) in search_files
    assert page.resolve() in repo_files
    assert scanner.excluded_generated_dirs == []
