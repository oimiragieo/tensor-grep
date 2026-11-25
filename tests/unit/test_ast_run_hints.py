from __future__ import annotations

from tensor_grep.cli.ast_workflow_rules import (
    _ast_run_remediation_lines,
    _build_ast_run_search_payload,
)
from tensor_grep.core.result import SearchResult


def test_python_zero_match_remediation_suggests_return_typed_pattern_and_selector() -> None:
    lines = _ast_run_remediation_lines("def $NAME($$$ARGS): $$$BODY", "python")
    joined = "\n".join(lines)

    assert "def $NAME($$$ARGS): $$$BODY" in joined
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in joined
    assert "--selector function_definition" in joined


def test_ast_run_search_payload_includes_python_return_typed_hint_on_zero_matches() -> None:
    empty_result = SearchResult(matches=[], routing_backend="AstBackend")
    payload = _build_ast_run_search_payload(
        stdin=False,
        backend_name="AstBackend",
        pattern="def $NAME($$$ARGS): $$$BODY",
        path=".",
        result=empty_result,
        lang="python",
    )

    hints = payload["remediation"]["hints"]
    joined = "\n".join(hints)
    assert "def $NAME($$$ARGS) -> $RET: $$$BODY" in joined
    assert "--selector function_definition" in joined
