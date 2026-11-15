import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tensor_grep.cli import ast_workflows, mcp_server
from tensor_grep.core.result import MatchLine, SearchResult


@pytest.fixture
def run_surface(tmp_path, monkeypatch):
    source = tmp_path / "a.py"
    source.write_bytes(b"foo(x)\n")
    result = SearchResult(
        matches=[MatchLine(file=str(source), line_number=1, text="foo(x)")],
        total_matches=1,
        total_files=1,
    )
    select = Mock(return_value=SimpleNamespace(search=Mock(return_value=result)))

    def apply(**kwargs):
        source.write_bytes(b"bar(x)\n")
        return json.dumps({"applied_edits": 1}), 0

    apply = Mock(side_effect=apply)
    plan = Mock(return_value=(json.dumps({"total_edits": 1}), 0))
    child = Mock(side_effect=AssertionError("unexpected child process"))
    monkeypatch.setattr(ast_workflows, "_select_ast_backend_for_pattern", select)
    monkeypatch.setattr(ast_workflows, "execute_rewrite_apply_json", apply)
    monkeypatch.setattr(ast_workflows, "execute_rewrite_plan_json", plan)
    monkeypatch.setattr(subprocess, "Popen", child)
    return SimpleNamespace(source=source, select=select, apply=apply, plan=plan, child=child)


@pytest.mark.parametrize("filter_regex", ["x", "nevermatch", ""])
@pytest.mark.parametrize("rewrite", ["bar($A)", None])
def test_apply_filter_refuses_before_backend_child_or_write(
    run_surface, capsys, filter_regex, rewrite
):
    surface = run_surface
    code = ast_workflows.run_command(
        "foo($A)",
        str(surface.source),
        lang="python",
        rewrite=rewrite,
        apply=True,
        filter_regex=filter_regex,
        json_mode=True,
    )
    assert code != 0, "unsupported apply/filter must refuse, not become search or apply"
    assert surface.source.read_bytes() == b"foo(x)\n"
    surface.select.assert_not_called()
    surface.apply.assert_not_called()
    surface.plan.assert_not_called()
    surface.child.assert_not_called()
    error = capsys.readouterr().err
    assert "--filter" in error or (rewrite is None and "--rewrite" in error)


@pytest.mark.parametrize(
    "apply,interactive,filter_regex,changed,matches",
    [
        (True, False, None, True, None),
        (False, False, None, False, None),
        (False, False, "x", False, 1),
        (False, False, "nevermatch", False, 0),
        (False, False, "", False, 1),
        (True, True, "x", True, None),
        (True, True, "nevermatch", False, None),
    ],
)
def test_existing_apply_and_readonly_interactive_filters(
    run_surface, monkeypatch, capsys, apply, interactive, filter_regex, changed, matches
):
    surface = run_surface
    monkeypatch.setattr("builtins.input", Mock(return_value="y"))
    code = ast_workflows.run_command(
        "foo($A)",
        str(surface.source),
        lang="python",
        apply=apply,
        interactive=interactive,
        rewrite="bar($A)" if apply or filter_regex is None else None,
        filter_regex=filter_regex,
        json_mode=True,
    )
    assert code == (1 if matches == 0 else 0)
    assert surface.source.read_bytes() == (b"bar(x)\n" if changed else b"foo(x)\n")
    assert surface.apply.call_count == int(changed)
    assert surface.plan.call_count == int(not apply and filter_regex is None)
    if matches is not None:
        assert json.loads(capsys.readouterr().out)["total_matches"] == matches


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"plan": {"edits": [{}]}}, 1),
        ({"plan": {"edits": [{}, {}, {}]}}, 3),
        ({"plan": {"edits": []}}, 0),
        ({"applied_edits": 7, "plan": {"edits": [{}]}}, 7),
        ({"edits": [{}, {}], "plan": {"edits": [{}]}}, 2),
        ({"edits": [], "plan": {"edits": [{}]}}, 0),
        ({"total_edits": 5, "plan": {"edits": [{}]}}, 5),
        ({"total_edits": 0, "plan": {"edits": [{}]}}, 0),
        ({"edits": "bad", "plan": {"edits": [{}]}}, 1),
        ({"plan": None}, 0),
        ({"plan": []}, 0),
        ({"plan": "bad"}, 0),
        ({"plan": 1}, 0),
        ({"plan": {"edits": None}}, 0),
        ({"plan": {"edits": {}}}, 0),
        ({"plan": {"edits": "bad"}}, 0),
        ({"plan": {"edits": True}}, 0),
        ({"total_edits": True}, 0),
    ],
)
def test_apply_receipt_nested_counts_and_existing_precedence(payload, expected):
    assert mcp_server._normalize_apply_result_payload(payload)["applied_edits"] == expected


@pytest.mark.parametrize("embedded", [True, False])
def test_failed_apply_does_not_count_unapplied_plan(tmp_path, monkeypatch, embedded):
    from tensor_grep.cli import mcp_rewrite_tools

    source = tmp_path / "a.py"
    source.write_bytes(b"foo(x)\n")
    monkeypatch.chdir(tmp_path)
    error = {"error": {"code": "backend_error"}, "plan": {"edits": [{}]}}
    transport = Mock(return_value=json.dumps(error))
    monkeypatch.setattr(
        mcp_server,
        "_resolve_native_tg_binary_for_mcp",
        Mock(return_value=(None if embedded else source, None)),
    )
    monkeypatch.setattr(mcp_server, "_embedded_rewrite_available", Mock(return_value=True))
    monkeypatch.setattr(mcp_server, "_execute_embedded_rewrite_json", transport)
    monkeypatch.setattr(mcp_rewrite_tools, "_execute_rewrite_json_command", transport)
    normalize = Mock(side_effect=mcp_rewrite_tools._normalize_apply_result_payload)
    monkeypatch.setattr(mcp_rewrite_tools, "_normalize_apply_result_payload", normalize)
    raw, code = mcp_rewrite_tools.execute_rewrite_apply_json(
        pattern="foo($A)", replacement="bar($A)", lang="python", path=str(source)
    )
    assert code != 0
    assert json.loads(raw) == error
    assert source.read_bytes() == b"foo(x)\n"
    transport.assert_called_once()
    normalize.assert_not_called()
