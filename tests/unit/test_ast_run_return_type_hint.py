from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli import ast_workflows
from tensor_grep.core.result import MatchLine, SearchResult


class _StubAstWrapperBackend:
    def __init__(self, matches: list[MatchLine]) -> None:
        self._matches = matches

    def search(self, _file_path: str, _pattern: str, config=None) -> SearchResult:
        return self.search_many([_file_path], _pattern, config=config)

    def search_many(self, _paths: list[str], _pattern: str, config=None) -> SearchResult:
        matched_files = list(dict.fromkeys(m.file for m in self._matches if m.file))
        return SearchResult(
            matches=list(self._matches),
            matched_file_paths=matched_files,
            total_matches=len(self._matches),
            total_files=len(matched_files),
            routing_backend="AstGrepWrapperBackend",
            routing_reason="ast",
        )


def test_run_return_type_advisory_hints_on_zero_matches_json_and_text(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "typed_only.py"
    target.write_text("def typed(x: int) -> int:\n    return x\n", encoding="utf-8")

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([]),
    )

    exit_json = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=True,
    )
    assert exit_json == 1
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["total_matches"] == 0
    assert "remediation" in payload
    advisory = "\n".join(payload["advisory_hints"])
    assert "--selector function_definition" in advisory
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in advisory

    exit_text = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=False,
    )
    assert exit_text == 1
    captured = capsys.readouterr()
    assert "advisory_hints:" in captured.err
    assert "--selector function_definition" in captured.err
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in captured.err


def test_run_return_type_advisory_hints_on_nonzero_matches_mixed_functions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "mixed.py"
    target.write_text(
        "def untyped(x):\n    return x\n\ndef typed(x: int) -> int:\n    return x\n",
        encoding="utf-8",
    )

    matched = MatchLine(
        file=str(target),
        line_number=1,
        text="def untyped(x):\n    return x",
    )
    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([matched]),
    )

    exit_json = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=True,
    )
    assert exit_json == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["total_matches"] == 1
    assert "remediation" not in payload
    advisory = "\n".join(payload["advisory_hints"])
    assert "--selector function_definition" in advisory
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in advisory

    exit_text = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=False,
    )
    assert exit_text == 0
    captured = capsys.readouterr()
    assert "advisory_hints:" in captured.err
    assert "--selector function_definition" in captured.err
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in captured.err


def test_run_return_type_advisory_hints_omitted_when_no_unmatched_typed_or_arrow_used(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "untyped_only.py"
    target.write_text("def untyped(x):\n    return x\n", encoding="utf-8")

    matched = MatchLine(
        file=str(target),
        line_number=1,
        text="def untyped(x):\n    return x",
    )
    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([matched]),
    )

    exit_json = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=True,
    )
    assert exit_json == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" not in payload

    exit_arrow = ast_workflows.run_command(
        "def $NAME($$$ARGS) -> $RET: $$$BODY",
        str(target),
        lang="python",
        json_mode=True,
    )
    assert exit_arrow == 0
    payload_arrow = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" not in payload_arrow


def test_run_return_type_advisory_skips_mismatched_literal_function_name(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    target = tmp_path / "other_typed.py"
    target.write_text("def bar(x: int) -> int:\n    return x\n", encoding="utf-8")

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([]),
    )

    exit_mismatch = ast_workflows.run_command(
        "def foo($$$ARGS): $$$BODY",
        str(target),
        lang="python",
        json_mode=True,
    )
    assert exit_mismatch == 1
    payload_mismatch = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" not in payload_mismatch

    matching_target = tmp_path / "foo_typed.py"
    matching_target.write_text("def foo(x: int) -> int:\n    return x\n", encoding="utf-8")

    exit_match = ast_workflows.run_command(
        "def foo($$$ARGS): $$$BODY",
        str(matching_target),
        lang="python",
        json_mode=True,
    )
    assert exit_match == 1
    payload_match = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" in payload_match


def test_run_return_type_advisory_bounds_secondary_scan(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tensor_grep.cli import ast_workflow_rules

    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("def plain(x):\n    return x\n", encoding="utf-8")

    monkeypatch.setattr(ast_workflow_rules, "_MAX_ADVISORY_FILES", 2)
    monkeypatch.setattr(
        ast_workflow_rules,
        "_collect_candidate_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("secondary scan must not invoke full _collect_candidate_files walk")
        ),
    )

    inspected_paths: list[str] = []
    orig_helper = ast_workflow_rules._source_has_unmatched_typed_def

    def _recording_helper(
        source: str, matched_lines: set[int], target_name: str | None = None
    ) -> bool:
        inspected_paths.append(source)
        return orig_helper(source, matched_lines, target_name)

    monkeypatch.setattr(ast_workflow_rules, "_source_has_unmatched_typed_def", _recording_helper)
    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([]),
    )

    exit_code = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(tmp_path),
        lang="python",
        json_mode=True,
    )
    assert exit_code == 1
    capsys.readouterr()
    assert len(inspected_paths) == 2


def test_run_return_type_advisory_suppressed_by_selector_and_skips_oversized_files(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from tensor_grep.cli import ast_workflow_rules

    typed = tmp_path / "typed.py"
    typed.write_text("def typed(x: int) -> int:\n    return x\n", encoding="utf-8")

    monkeypatch.setattr(
        ast_workflows,
        "_select_ast_backend_for_pattern",
        lambda _cfg, _pattern: _StubAstWrapperBackend([]),
    )

    exit_selector = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(typed),
        lang="python",
        selector="function_definition",
        json_mode=True,
    )
    assert exit_selector == 1
    payload_selector = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" not in payload_selector

    huge = tmp_path / "huge.py"
    padding = "# " + ("a" * (ast_workflow_rules._MAX_ADVISORY_FILE_BYTES + 64))
    huge.write_text(f"{padding}\ndef typed(x: int) -> int:\n    return x\n", encoding="utf-8")

    parsed_sources: list[str] = []
    orig_helper = ast_workflow_rules._source_has_unmatched_typed_def

    def _recording_helper(
        source: str, matched_lines: set[int], target_name: str | None = None
    ) -> bool:
        parsed_sources.append(source)
        return orig_helper(source, matched_lines, target_name)

    monkeypatch.setattr(ast_workflow_rules, "_source_has_unmatched_typed_def", _recording_helper)

    exit_huge = ast_workflows.run_command(
        "def $NAME($$$ARGS): $$$BODY",
        str(huge),
        lang="python",
        json_mode=True,
    )
    assert exit_huge == 1
    payload_huge = json.loads(capsys.readouterr().out.strip())
    assert "advisory_hints" not in payload_huge
    assert parsed_sources == []
