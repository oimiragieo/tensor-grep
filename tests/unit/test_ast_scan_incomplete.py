"""AST ruleset scans must keep partial backend coverage through every aggregation path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tensor_grep.core.result import MatchLine, SearchResult

_RULE = {"id": "sample-rule", "language": "python", "pattern": "print($X)"}


def _assert_partial_contract(payload: dict[str, Any]) -> None:
    assert payload["partial"] is True
    assert payload["partial_reason"] == "unreadable_path"
    assert "incomplete" in payload["remediation"].lower()
    assert (
        not {"result_incomplete", "incomplete_reason", "incomplete_reason_class"} & payload.keys()
    )


class _ProjectResults(dict[str, SearchResult]):
    def __init__(self, aggregate: SearchResult) -> None:
        super().__init__()
        self.aggregate = aggregate


def _result(path: Path, *, matched: bool, incomplete: bool) -> SearchResult:
    matches = [MatchLine(1, "print('hit')", str(path))] if matched else []
    return SearchResult(
        matches=matches,
        matched_file_paths=[str(path)] if matched else [],
        total_files=int(matched),
        total_matches=len(matches),
        result_incomplete=incomplete,
        incomplete_reason="ast-grep skipped unreadable paths" if incomplete else None,
        incomplete_reason_class="unreadable_path" if incomplete else None,
    )


class _ProjectWrapper:
    def __init__(self, root: Path, *, matched: bool, incomplete: bool) -> None:
        self.root, self.matched, self.incomplete = root, matched, incomplete
        self.aggregate = _result(root / "a.py", matched=False, incomplete=incomplete)

    def is_available(self) -> bool:
        return True

    def search_project(self, _root: str, _config: str) -> dict[str, SearchResult]:
        results = _ProjectResults(self.aggregate)
        if self.matched:
            results["sample-rule"] = _result(
                self.root / "a.py", matched=True, incomplete=self.incomplete
            )
        return results


_ProjectWrapper.__name__ = "AstGrepWrapperBackend"


def _payload(
    tmp_path: Path, monkeypatch: Any, *, matched: bool, incomplete: bool
) -> dict[str, Any]:
    import tensor_grep.cli.ast_workflows as workflows
    from tensor_grep.cli.main import _run_ast_scan_payload

    (tmp_path / "a.py").write_text("print('hit')\n", encoding="utf-8")
    backend = _ProjectWrapper(tmp_path, matched=matched, incomplete=incomplete)
    monkeypatch.setattr(workflows, "_select_ast_backend_for_rule", lambda *_args: backend)
    return _run_ast_scan_payload(
        {"config_path": "fixture.yml", "root_dir": tmp_path, "language": "python"},
        [dict(_RULE)],
        routing_reason="project-scan",
        project_scan_fast_path=True,
    )


def test_project_fast_path_partial_zero_and_positive_are_disclosed(
    tmp_path: Path, monkeypatch: Any
):
    clean = _payload(tmp_path, monkeypatch, matched=False, incomplete=False)
    assert "partial" not in clean and "result_incomplete" not in clean

    zero = _payload(tmp_path, monkeypatch, matched=False, incomplete=True)
    positive = _payload(tmp_path, monkeypatch, matched=True, incomplete=True)
    for payload, count in ((zero, 0), (positive, 1)):
        assert payload["total_matches"] == count
        _assert_partial_contract(payload)
        assert all(finding["rule_id"] == "sample-rule" for finding in payload["findings"])


def test_project_rules_merge_partial_metadata_monotonically(tmp_path: Path, monkeypatch: Any):
    import tensor_grep.cli.ast_workflows as workflows
    from tensor_grep.cli.main import _run_ast_scan_payload

    (tmp_path / "a.py").write_text("print('hit')\n", encoding="utf-8")

    class PerRuleWrapper(_ProjectWrapper):
        def search_project(self, _root: str, _config: str) -> dict[str, SearchResult]:
            results = _ProjectResults(_result(self.root / "a.py", matched=False, incomplete=False))
            results["sample-rule"] = _result(self.root / "a.py", matched=True, incomplete=True)
            results["healthy-rule"] = _result(self.root / "a.py", matched=False, incomplete=False)
            return results

    PerRuleWrapper.__name__ = "AstGrepWrapperBackend"
    backend = PerRuleWrapper(tmp_path, matched=False, incomplete=False)
    monkeypatch.setattr(workflows, "_select_ast_backend_for_rule", lambda *_args: backend)
    payload = _run_ast_scan_payload(
        {"config_path": "fixture.yml", "root_dir": tmp_path, "language": "python"},
        [dict(_RULE), {**_RULE, "id": "healthy-rule", "pattern": "unused"}],
        routing_reason="project-scan",
        project_scan_fast_path=True,
    )
    _assert_partial_contract(payload)
    assert payload["total_matches"] == 1


def test_partial_project_scan_renders_sarif_unsuccessful(tmp_path: Path, monkeypatch: Any):
    from tensor_grep.cli.sarif import scan_payload_to_sarif

    payload = _payload(tmp_path, monkeypatch, matched=False, incomplete=True)
    sarif = scan_payload_to_sarif(payload, tool_version="test", base_path=str(tmp_path))
    assert sarif["runs"][0]["invocations"][0]["executionSuccessful"] is False


def test_backend_partial_keeps_existing_unreadable_scan_disclosure(
    tmp_path: Path, monkeypatch: Any
):
    import tensor_grep.cli.ast_workflows as workflows
    from tensor_grep.cli.main import _run_ast_scan_payload

    readable, locked = tmp_path / "readable.py", tmp_path / "locked.py"
    readable.write_text("print('hit')\n", encoding="utf-8")
    locked.write_text("SENTINEL\n", encoding="utf-8")
    backend = _ProjectWrapper(tmp_path, matched=False, incomplete=True)
    monkeypatch.setattr(workflows, "_select_ast_backend_for_rule", lambda *_args: backend)
    pristine = Path.read_text

    def guarded_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == "locked.py":
            raise PermissionError(13, "Permission denied", str(path))
        return pristine(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    payload = _run_ast_scan_payload(
        {"config_path": "fixture.yml", "root_dir": tmp_path, "language": "python"},
        [
            dict(_RULE),
            {"id": "regex-rule", "language": "python", "engine": "regex", "pattern": "SENTINEL"},
        ],
        routing_reason="project-scan",
        candidate_files=[str(readable), str(locked)],
        project_scan_fast_path=True,
    )
    assert payload["partial_reason"] == "unreadable_path"
    assert payload["unreadable_paths"]["sample"]
    assert "failed" in payload["remediation"]
    assert "AST backend reported incomplete scan coverage" in payload["remediation"]
    _assert_partial_contract(payload)


class _PerFileAstBackend:
    def search(self, file_path: str, _pattern: str, config: Any = None) -> SearchResult:
        _ = config
        path = Path(file_path)
        return _result(path, matched=path.name == "first.py", incomplete=path.name == "first.py")


def test_per_file_aggregate_keeps_earlier_partial_result(tmp_path: Path, monkeypatch: Any):
    import tensor_grep.cli.ast_workflows as workflows
    from tensor_grep.cli.main import _run_ast_scan_payload

    first, second = tmp_path / "first.py", tmp_path / "second.py"
    first.write_text("print('first')\n", encoding="utf-8")
    second.write_text("print('second')\n", encoding="utf-8")
    backend = _PerFileAstBackend()
    monkeypatch.setattr(workflows, "_select_ast_backend_for_rule", lambda *_args: backend)
    payload = _run_ast_scan_payload(
        {"config_path": "fixture.yml", "root_dir": tmp_path, "language": "python"},
        [dict(_RULE)],
        routing_reason="project-scan",
        candidate_files=[str(first), str(second)],
    )
    _assert_partial_contract(payload)
    assert payload["total_matches"] == 1


def test_legacy_scan_command_discloses_project_partial_without_changing_exit(
    tmp_path: Path, monkeypatch: Any, capsys: Any
):
    import tensor_grep.cli.ast_workflows as workflows

    (tmp_path / "a.py").write_text("print('hit')\n", encoding="utf-8")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "sample.yml").write_text(
        "id: sample-rule\nlanguage: python\nrule:\n  pattern: print($X)\n", encoding="utf-8"
    )
    (tmp_path / "sgconfig.yml").write_text(
        "ruleDirs:\n  - rules\nlanguage: python\n", encoding="utf-8"
    )
    backend = _ProjectWrapper(tmp_path, matched=False, incomplete=True)
    monkeypatch.setattr(workflows, "_select_ast_backend_for_rule", lambda *_args: backend)
    monkeypatch.setattr(workflows, "_get_cached_backend", lambda _name: backend)

    exit_code = workflows.scan_command(str(tmp_path / "sgconfig.yml"), json_mode=True)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0  # legacy scan_command's public contract remains unchanged
    _assert_partial_contract(payload)

    exit_code = workflows.scan_command(str(tmp_path / "sgconfig.yml"), json_mode=False)
    text_output = capsys.readouterr().out
    assert exit_code == 0
    assert "warning: INCOMPLETE SCAN" in text_output
    assert "remaining scope is clean" in text_output.lower()


def test_search_project_zero_match_preserves_aggregate_metadata(monkeypatch: Any):
    from tensor_grep.backends.ast_wrapper_backend import AstGrepWrapperBackend

    backend = AstGrepWrapperBackend()
    monkeypatch.setattr(backend, "is_available", lambda: True)
    monkeypatch.setattr(backend, "_get_binary_name", lambda: "sg")
    monkeypatch.setattr(
        backend,
        "_run_ast_grep_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["sg"], 0, "[]", "ERROR: C:\\fixtures\\private: Access is denied. (os error 5)"
        ),
    )
    results = backend.search_project(".", "sgconfig.yml")
    assert results == {}  # no fabricated public rule IDs
    aggregate = getattr(results, "aggregate", None)
    assert isinstance(aggregate, SearchResult)
    assert aggregate.result_incomplete is True
    assert aggregate.incomplete_reason_class == "unreadable_path"
