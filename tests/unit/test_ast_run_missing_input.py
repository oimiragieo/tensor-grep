"""An explicit missing AST input is an execution error, not a clean zero-match scan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from tensor_grep.cli import ast_workflows
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.core.result import SearchResult


@pytest.mark.parametrize("exists", [False, True])
def test_ast_run_validates_explicit_input_before_wrapper_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exists: bool,
) -> None:
    target = tmp_path / "target.py"
    if exists:
        target.write_text("hub_fn(1)\n", encoding="utf-8")
    calls: list[list[str]] = []

    class AstGrepWrapperBackend:
        def search_many(self, paths: list[str], pattern: str, config: Any = None) -> SearchResult:
            calls.append(paths)
            return SearchResult(matches=[], total_files=0, total_matches=0)

    monkeypatch.setattr(
        ast_workflows, "_select_ast_backend_for_pattern", lambda *_: AstGrepWrapperBackend()
    )
    status = ast_workflows.run_command(
        "hub_fn($VALUE)", path=str(target), lang="python", json_mode=True
    )
    output = capsys.readouterr()
    payload = json.loads(output.out)

    if exists:
        assert status == 1
        assert calls == [[str(target)]]
        assert "remediation" in payload
    else:
        assert status == 2
        assert calls == []
        assert payload["ok"] is False
        assert payload["error"] == "backend_error"
        assert "not found" in payload["detail"].lower()
        assert "remediation" not in payload
        assert "skipped unreadable paths" not in output.err


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("json_mode", [False, True])
def test_missing_input_precedes_backend_availability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exists: bool,
    json_mode: bool,
) -> None:
    target = tmp_path / "target.py"
    if exists:
        target.write_text("hub_fn(1)\n", encoding="utf-8")
    selector = Mock(side_effect=ConfigurationError("ast-grep unavailable"))
    monkeypatch.setattr(ast_workflows, "_select_ast_backend_for_pattern", selector)
    status = ast_workflows.run_command(
        "hub_fn($VALUE)", path=str(target), lang="python", json_mode=json_mode
    )
    output = capsys.readouterr()
    assert status == 2
    if exists:
        selector.assert_called_once()
        expected_error = "configuration_error"
    else:
        selector.assert_not_called()
        expected_error = "backend_error"
    if json_mode:
        payload = json.loads(output.out)
        assert payload["ok"] is False
        assert payload["error"] == expected_error
        assert "remediation" not in payload
        if not exists:
            assert "not found" in payload["detail"].lower()
            assert "routing_backend" not in payload
    else:
        assert output.out == ""
        assert ("ast-grep unavailable" if exists else "not found") in output.err.lower()
        assert "No AST matches found" not in output.err


@pytest.mark.parametrize("consolidated", [False, True])
def test_mcp_ast_missing_path_refuses_before_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consolidated: bool
) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    construct = Mock(side_effect=AssertionError("missing input must be refused before pipeline"))
    monkeypatch.setattr(mcp_server, "Pipeline", construct)
    missing = tmp_path / "missing.py"
    if consolidated:
        result = mcp_server.tg_query(
            action="ast", pattern="hub_fn($VALUE)", lang="python", path=str(missing)
        )
    else:
        result = mcp_server.tg_ast_search(
            pattern="hub_fn($VALUE)", lang="python", path=str(missing)
        )
    payload = json.loads(result)
    assert payload["error"]["code"] == "invalid_input"
    assert "not found" in payload["error"]["message"].lower()
    construct.assert_not_called()


@pytest.mark.parametrize("warning", [None, "Warning: Pattern contains an ERROR node"])
def test_ast_run_uses_wrapper_pattern_validation_before_zero_match_advice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    warning: str | None,
) -> None:
    target = tmp_path / "target.py"
    target.write_text("def actual(): pass\n", encoding="utf-8")
    checks: list[str] = []

    class AstGrepWrapperBackend:
        def search_many(self, paths: list[str], pattern: str, config: Any = None) -> SearchResult:
            return SearchResult(matches=[], total_files=0, total_matches=0)

        def pattern_warning(self, pattern: str, config: Any = None) -> str | None:
            checks.append(pattern)
            return warning

    monkeypatch.setattr(
        ast_workflows, "_select_ast_backend_for_pattern", lambda *_: AstGrepWrapperBackend()
    )
    status = ast_workflows.run_command(
        "def $NAME(", path=str(target), lang="python", json_mode=True
    )
    payload = json.loads(capsys.readouterr().out)
    assert checks == ["def $NAME("]
    if warning is None:
        assert status == 1
        assert "remediation" in payload
    else:
        assert status == 2
        assert payload["error"] == "backend_error"
        assert "ERROR node" in payload["detail"]
        assert "remediation" not in payload
