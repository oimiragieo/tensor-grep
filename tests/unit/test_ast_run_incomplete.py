"""The public `tg run` route must preserve partial AST coverage through its output layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli.main import app
from tensor_grep.core.result import MatchLine, SearchResult


@pytest.mark.parametrize("backend_kind", ["wrapper", "native"])
@pytest.mark.parametrize("has_match", [False, True], ids=["zero-matches", "positive-match"])
def test_tg_run_json_preserves_partial_ast_result_and_suppresses_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_kind: str, has_match: bool
) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('found')\n", encoding="utf-8")

    class AstGrepWrapperBackend:
        def search_many(self, paths, pattern, config=None) -> SearchResult:
            assert paths == [str(source)]
            return _partial_result(source, has_match)

    class AstBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            assert file_path == str(source)
            return _partial_result(source, has_match)

    backend = AstGrepWrapperBackend() if backend_kind == "wrapper" else AstBackend()
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: backend,
    )

    result = CliRunner().invoke(
        app,
        ["run", "--pattern", "print($VALUE)", str(source), "--lang", "python", "--json"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == int(has_match)
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "ast-grep skipped unreadable paths during the scan"
    assert payload["incomplete_reason_class"] == "unreadable_path"
    assert ("remediation" in payload) is False
    assert bool(payload["matches"]) is has_match


@pytest.mark.parametrize("backend_kind", ["wrapper", "native"])
@pytest.mark.parametrize("has_match", [False, True], ids=["zero-matches", "positive-match"])
def test_tg_run_text_partial_result_exits_two_without_no_match_advice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend_kind: str, has_match: bool
) -> None:
    source = tmp_path / "app.py"
    source.write_text("pass\n", encoding="utf-8")

    class AstGrepWrapperBackend:
        def search_many(self, paths, pattern, config=None) -> SearchResult:
            return _partial_result(source, has_match)

    class AstBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            return _partial_result(source, has_match)

    backend = AstGrepWrapperBackend() if backend_kind == "wrapper" else AstBackend()
    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: backend,
    )

    result = CliRunner().invoke(app, ["run", "--pattern", "print($VALUE)", str(source)])

    assert result.exit_code == 2
    assert "tg ast-info" not in result.stdout
    assert "incomplete" in result.stderr.lower()
    assert "tg ast-info" not in result.stderr
    assert ("found" in result.stdout) is has_match


@pytest.mark.parametrize("has_match", [False, True], ids=["zero-matches", "positive-match"])
def test_tg_run_files_with_matches_partial_exits_two_and_keeps_path_output_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, has_match: bool
) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('found')\n", encoding="utf-8")

    class AstGrepWrapperBackend:
        def search_many(self, paths, pattern, config=None) -> SearchResult:
            return _partial_result(source, has_match)

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstGrepWrapperBackend(),
    )

    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($VALUE)",
            str(source),
            "--lang",
            "python",
            "--files-with-matches",
        ],
    )

    assert result.exit_code == 2
    assert result.stdout.splitlines() == ([str(source)] if has_match else [])
    assert "incomplete" in result.stderr.lower()
    assert "tg ast-info" not in result.stderr


def test_tg_run_per_file_aggregate_keeps_earlier_partial_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("print('partial')\n", encoding="utf-8")
    second.write_text("print('complete')\n", encoding="utf-8")

    class AstBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            result = _partial_result(Path(file_path), True)
            if file_path == str(second):
                result.incomplete_reason = None
                result.incomplete_reason_class = None
                result.result_incomplete = False
            return result

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstBackend(),
    )

    result = CliRunner().invoke(
        app,
        ["run", "--pattern", "print($VALUE)", str(tmp_path), "--lang", "python", "--json"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 2
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason_class"] == "unreadable_path"


def test_tg_run_interactive_refuses_positive_partial_before_prompt_or_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.py"
    source.write_text("print('found')\n", encoding="utf-8")

    class AstBackend:
        def search(self, file_path, pattern, config=None) -> SearchResult:
            return _partial_result(source, True)

    monkeypatch.setattr(
        "tensor_grep.cli.ast_workflows._select_ast_backend_for_pattern",
        lambda *_args, **_kwargs: AstBackend(),
    )
    apply_calls: list[str] = []

    def apply_stub(**kwargs):
        apply_calls.append(kwargs["path"])
        return '{"ok": true}', 0

    monkeypatch.setattr("tensor_grep.cli.ast_workflows.execute_rewrite_apply_json", apply_stub)
    original = source.read_bytes()
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--pattern",
            "print($VALUE)",
            str(source),
            "--lang",
            "python",
            "--rewrite",
            "print('replacement')",
            "--interactive",
        ],
        input="y\n",
    )

    assert result.exit_code == 2
    assert "Apply rewrite" not in result.stdout
    assert apply_calls == []
    assert source.read_bytes() == original
    assert "incomplete" in result.stderr.lower()


def _partial_result(source: Path, has_match: bool) -> SearchResult:
    matches = (
        [MatchLine(line_number=1, text="print('found')", file=str(source))] if has_match else []
    )
    return SearchResult(
        matches=matches,
        matched_file_paths=[str(source)] if has_match else [],
        total_files=int(has_match),
        total_matches=len(matches),
        result_incomplete=True,
        incomplete_reason="ast-grep skipped unreadable paths during the scan",
        incomplete_reason_class="unreadable_path",
    )
