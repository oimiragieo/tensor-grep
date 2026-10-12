from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp import types
from typer.testing import CliRunner

from tensor_grep.cli.main import app


def _assert_citation_envelope(raw: str) -> dict[str, object]:
    payload = json.loads(raw)
    assert set(payload) == {
        "version",
        "citations",
        "result_incomplete",
        "incomplete_reason",
        "omitted",
    }
    assert payload["version"] == 1
    assert isinstance(payload["result_incomplete"], bool)
    assert payload["incomplete_reason"] is None or isinstance(payload["incomplete_reason"], str)
    assert isinstance(payload["omitted"], int)
    citations = payload["citations"]
    assert isinstance(citations, list)
    for citation in citations:
        assert set(citation) == {"path", "start_line", "end_line"}
        assert isinstance(citation["path"], str)
        assert "/" in citation["path"] or not citation["path"].startswith("/")
        assert type(citation["start_line"]) is int
        assert type(citation["end_line"]) is int
        assert 1 <= citation["start_line"] <= citation["end_line"]
    return payload


def test_adjacent_reverse_ranked_ranges_merge_without_losing_lines(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import citation_envelope

    source = tmp_path / "sample.py"
    source.write_text("\n" * 12, encoding="utf-8")
    result = citation_envelope(
        [
            {"path": str(source), "start_line": 10, "end_line": 12},
            {"path": str(source), "start_line": 8, "end_line": 9},
            {"path": str(tmp_path / "other.py"), "start_line": 1, "end_line": 1},
            {"path": str(source), "start_line": 7, "end_line": 7},
            {"path": str(source), "start_line": 1, "end_line": 2},
        ],
        root=tmp_path,
    )
    assert result["citations"] == [
        {"path": "sample.py", "start_line": 8, "end_line": 12},
        {"path": "other.py", "start_line": 1, "end_line": 1},
        {"path": "sample.py", "start_line": 7, "end_line": 7},
        {"path": "sample.py", "start_line": 1, "end_line": 2},
    ]


def test_relative_match_path_is_resolved_within_root(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import citation_envelope

    (tmp_path / "sample.py").write_text("needle\n", encoding="utf-8")
    result = citation_envelope(
        [{"path": "sample.py", "start_line": 1, "end_line": 1}], root=tmp_path
    )
    assert result["citations"] == [{"path": "sample.py", "start_line": 1, "end_line": 1}]


def test_search_context_cites_only_matching_lines(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("before\nneedle\nneedle\nafter\n", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["search", "needle", str(tmp_path), "--json", "--citations", "-C", "1"]
    )
    assert result.exit_code == 0, result.output
    assert _assert_citation_envelope(result.stdout)["citations"] == [
        {"path": "sample.txt", "start_line": 2, "end_line": 3}
    ]


def test_absolute_search_file_keeps_repository_relative_path() -> None:
    source = Path(__file__).resolve().parents[2] / "src/tensor_grep/cli/citations.py"
    result = CliRunner().invoke(
        app, ["search", "def citation_envelope", str(source), "--json", "--citations"]
    )
    assert result.exit_code == 0, result.output
    assert _assert_citation_envelope(result.stdout)["citations"][0]["path"] == (
        "src/tensor_grep/cli/citations.py"
    )


def test_mcp_search_citation_limits_zero_omit_all(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("needle\n", encoding="utf-8")
    payload = _assert_citation_envelope(
        mcp_server.tg_search(
            "needle", path=str(tmp_path), max_results=0, max_files=0, citations=True
        )
    )
    assert payload["citations"] == []
    assert payload["omitted"] == 2


def test_mcp_search_zero_limit_counts_adjacent_hidden_matches(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\nneedle\n", encoding="utf-8")
    payload = _assert_citation_envelope(
        mcp_server.tg_search("needle", path=str(tmp_path), max_results=0, citations=True)
    )
    assert payload["citations"] == []
    assert payload["omitted"] == 2


def test_mcp_workspace_citations_keep_unstructured_error(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import serialize_workspace_citations

    payload = json.loads(
        serialize_workspace_citations({str(tmp_path): "invalid regular expression"}, [], tmp_path)
    )
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "invalid regular expression"


def test_mcp_workspace_invalid_regex_response_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    root = tmp_path / "project"
    root.mkdir()
    with patch(
        "tensor_grep.cli.mcp_server._tg_query_dispatch", return_value="invalid regular expression"
    ):
        payload = json.loads(
            mcp_server.tg_query(
                action="text",
                pattern="[",
                path=str(tmp_path),
                workspace_roots=[str(root)],
                citations=True,
            )
        )
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "invalid regular expression"


def test_context_citation_requires_matching_evidence(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import payload_citations

    source = tmp_path / "sample.py"
    source.write_text("def f():\n    needle = True\n", encoding="utf-8")
    payload = {
        "query": "needle",
        "sources": [
            {"file": str(source), "start_line": 1, "line_map": [{"line": 1, "text": "def f():"}]}
        ],
    }
    result = payload_citations(payload, tmp_path)
    assert result["citations"] == []
    assert result["result_incomplete"] is True


def test_context_citation_counts_omitted_render_lines(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import payload_citations

    source = tmp_path / "sample.py"
    source.write_text("needle\n", encoding="utf-8")
    payload = {
        "query": "needle",
        "sources": [
            {
                "file": str(source),
                "line_map": [{"line": 1, "text": "needle"}],
                "focus": {"omitted_line_count": 3},
            }
        ],
    }
    assert payload_citations(payload, tmp_path)["omitted"] == 3


def test_context_citation_counts_source_budget_omissions(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import payload_citations

    source = tmp_path / "sample.py"
    source.write_text("needle\n", encoding="utf-8")
    payload = {
        "query": "needle",
        "sources": [{"file": str(source), "line_map": [{"line": 1, "text": "needle"}]}],
        "source_budget": {"omitted_sources": 1, "omitted_line_count": 2},
    }
    assert payload_citations(payload, tmp_path)["omitted"] == 3


def test_search_citations_refuse_files_with_matches(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    result = CliRunner().invoke(
        app, ["search", "needle", str(tmp_path), "--citations", "--files-with-matches"]
    )
    assert result.exit_code == 2
    assert "--citations cannot be combined with --files-with-matches" in result.output


@pytest.mark.parametrize(
    "extra",
    [
        ["--json", "--files"],
        ["--json", "--debug"],
        ["--files-without-match"],
    ],
)
def test_search_citations_refuse_raw_or_debug_output_modes(
    tmp_path: Path, extra: list[str]
) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "unmatched.txt").write_text("haystack\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["search", "needle", str(tmp_path), "--citations", *extra])
    assert result.exit_code == 2, result.output
    assert "--citations cannot be combined with" in result.output
    assert any(flag in result.output for flag in extra)


def test_search_without_citations_preserves_first_format_json_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    args = [
        "search",
        "needle",
        str(tmp_path),
        "--json",
        "--cpu",
        "--format",
        "json",
        "--format",
        "rg",
        "--trim",
    ]
    monkeypatch.setattr("sys.argv", ["tg", *args])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, result.output
    assert "--trim" in result.output


def test_search_citations_refuse_repeated_format(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    args = ["search", "needle", str(tmp_path), "--citations", "--format", "json", "--format", "rg"]
    monkeypatch.setattr("sys.argv", ["tg", *args])
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "--citations cannot be combined with --ndjson or --format rg" in result.output


@pytest.mark.parametrize(
    ("args", "filename"),
    [
        (["search", "needle", "{root}", "--json", "--citations"], "sample.txt"),
        (["find", "needle", "{root}", "--json", "--citations"], "sample.txt"),
        (["agent", "{root}", "needle", "--json", "--citations"], "sample.py"),
        (["context-render", "{root}", "needle", "--json", "--citations"], "sample.py"),
    ],
)
def test_cli_citations_emit_only_the_citation_envelope(tmp_path: Path, args, filename) -> None:
    (tmp_path / filename).write_text(
        "def sample():\n    needle = 'needle'\n    return needle\n"
        if filename.endswith(".py")
        else "needle\n",
        encoding="utf-8",
    )
    invocation = [part.format(root=str(tmp_path)) for part in args]
    result = CliRunner().invoke(app, invocation)
    assert result.exit_code == 0, result.output
    payload = _assert_citation_envelope(result.stdout)
    if args[0] == "search":
        assert all(item["start_line"] == item["end_line"] for item in payload["citations"])


def test_search_citation_wiring_stays_within_main_size_pin(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["search", "needle", str(tmp_path), "--json", "--citations"])
    assert result.exit_code == 0, result.output
    assert _assert_citation_envelope(result.stdout)["citations"]

    main_path = Path(__file__).resolve().parents[2] / "src/tensor_grep/cli/main.py"
    lines = len(main_path.read_bytes().splitlines())
    assert lines <= 13523, f"citation CLI wiring grew main.py to {lines} lines above pin 13523"


@pytest.mark.parametrize(
    ("args", "path"),
    [
        (["search", "needle", "{root}", "--ndjson", "--citations"], "sample.txt"),
        (["search", "needle", "{root}", "--format", "rg", "--citations"], "sample.txt"),
    ],
)
def test_cli_citations_refuse_incompatible_output_modes(
    tmp_path: Path, monkeypatch, args, path
) -> None:
    (tmp_path / path).write_text("needle\n", encoding="utf-8")
    if "--format" in args:
        monkeypatch.setattr("sys.argv", ["tg", *[part.format(root=str(tmp_path)) for part in args]])
    result = CliRunner().invoke(app, [part.format(root=str(tmp_path)) for part in args])
    assert result.exit_code == 2
    assert "--citations cannot be combined with --ndjson or --format rg" in result.output


def test_mcp_tg_search_citations_envelope_and_confinement(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    try:
        monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
        payload = json.loads(mcp_server.tg_search("needle", path=str(tmp_path), citations=True))
        assert set(payload) == {
            "version",
            "citations",
            "result_incomplete",
            "incomplete_reason",
            "omitted",
        }
        refused = mcp_server.tg_search("needle", path=str(outside), citations=True)
        refusal = json.loads(refused)
        assert refusal["error"]["code"] == "invalid_input"
        assert refusal["path"] == "[refused]"
    finally:
        outside.rmdir()


def test_mcp_citations_wire_wrapper_preserves_error_signal(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    success = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_search", {"pattern": "needle", "path": str(tmp_path), "citations": True}
        )
    )
    if isinstance(success, types.CallToolResult):
        assert success.isError is False
        content = success.content
    else:
        content, _ = success
    _assert_citation_envelope(content[0].text)

    refused = asyncio.run(
        mcp_server.mcp.call_tool(
            "tg_search", {"pattern": "needle", "path": str(tmp_path.parent), "citations": True}
        )
    )
    assert isinstance(refused, types.CallToolResult)
    assert refused.isError is True
    assert json.loads(refused.content[0].text)["error"]["code"] == "invalid_input"


def test_search_citations_text_output_is_one_range_per_line(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("needle\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["search", "needle", str(tmp_path), "--citations", "--cpu"])
    assert result.exit_code == 0, result.output
    assert result.stdout == "sample.txt:1-1\n"


def test_find_citations_use_the_enclosing_symbol_range(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def sample():\n    needle = 'needle'\n    return needle\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["find", "needle", str(tmp_path), "--json", "--citations"])
    assert result.exit_code == 0, result.output
    payload = _assert_citation_envelope(result.stdout)
    assert payload["citations"] == [{"path": "sample.py", "start_line": 1, "end_line": 3}]


@pytest.mark.parametrize("command", ["find", "agent", "context-render"])
def test_scoped_cli_citations_keep_repository_relative_paths(tmp_path: Path, command: str) -> None:
    (tmp_path / ".git").mkdir()
    scope = tmp_path / "src"
    scope.mkdir()
    (scope / "sample.py").write_text(
        "def first():\n    needle = True\n    return needle\n", encoding="utf-8"
    )
    args = (
        ["find", "needle", str(scope), "--json", "--citations"]
        if command == "find"
        else [command, str(scope), "needle", "--json", "--citations", "--no-deadline"]
    )
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    payload = _assert_citation_envelope(result.stdout)
    assert payload["citations"] == [{"path": "src/sample.py", "start_line": 1, "end_line": 3}]
    assert payload["result_incomplete"] is False


@pytest.mark.parametrize("tool", ["tg_find", "tg_agent_capsule", "tg_context_render"])
def test_scoped_mcp_citations_keep_repository_relative_paths(
    tmp_path: Path, monkeypatch, tool: str
) -> None:
    from tensor_grep.cli import mcp_server

    (tmp_path / ".git").mkdir()
    scope = tmp_path / "src"
    scope.mkdir()
    (scope / "sample.py").write_text(
        "def first():\n    needle = True\n    return needle\n", encoding="utf-8"
    )
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    payload = _assert_citation_envelope(
        getattr(mcp_server, tool)(query="needle", path=str(scope), citations=True)
    )
    assert payload["citations"] == [{"path": "src/sample.py", "start_line": 1, "end_line": 3}]
    assert payload["result_incomplete"] is False


@pytest.mark.parametrize(
    ("tool", "options"),
    [
        ("tg_search", {"pattern": "needle"}),
        ("tg_query", {"action": "text", "pattern": "needle"}),
        ("tg_query", {"action": "find", "query": "needle"}),
        ("tg_context", {"action": "render", "query": "needle"}),
        ("tg_context", {"action": "capsule", "query": "needle"}),
    ],
)
def test_scoped_mcp_meta_citations_keep_repository_relative_paths(
    tmp_path: Path, monkeypatch, tool: str, options: dict[str, str]
) -> None:
    from tensor_grep.cli import mcp_server

    (tmp_path / ".git").mkdir()
    scope = tmp_path / "src"
    scope.mkdir()
    (scope / "sample.py").write_text(
        "def first():\n    needle = True\n    return needle\n", encoding="utf-8"
    )
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    payload = _assert_citation_envelope(
        getattr(mcp_server, tool)(path=str(scope), citations=True, **options)
    )
    assert payload["citations"]
    assert all(item["path"] == "src/sample.py" for item in payload["citations"])
    assert payload["result_incomplete"] is False


@pytest.mark.parametrize("command", ["agent", "context-render"])
def test_context_citations_use_symbol_not_snippet_window(tmp_path: Path, command: str) -> None:
    (tmp_path / "sample.py").write_text(
        "def sample():\n    needle = 'needle'\n    return needle\n\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, [command, str(tmp_path), "needle", "--json", "--citations", "--no-deadline"]
    )
    assert result.exit_code == 0, result.output
    payload = _assert_citation_envelope(result.stdout)
    assert payload["citations"] == [{"path": "sample.py", "start_line": 1, "end_line": 3}]


@pytest.mark.parametrize("command", ["agent", "context-render"])
def test_natural_query_cites_parser_backed_symbol_range(tmp_path: Path, command: str) -> None:
    (tmp_path / "sample.py").write_text(
        "def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8"
    )
    result = CliRunner().invoke(
        app,
        [command, str(tmp_path), "find first function", "--json", "--citations", "--no-deadline"],
    )
    assert result.exit_code == 0, result.output
    payload = _assert_citation_envelope(result.stdout)
    assert payload["citations"] == [{"path": "sample.py", "start_line": 1, "end_line": 2}]
    assert payload["result_incomplete"] is False


@pytest.mark.parametrize(
    ("command", "expected_digest"),
    [
        ("search", "6d55c4304269f9658ffb85acc6074cebabf236524c1349817369590f97844c2c"),
        ("find", "9676606f2982f1e886b4d3da5731e9a48e182bd799e2b1088004cbe0897cd997"),
        ("agent", "774d18b5674350c1289c573caa380438e87942e4ca08aa7fd496aa6bcaed6c1b"),
    ],
)
def test_no_flag_json_output_matches_origin_main_golden(
    tmp_path: Path, monkeypatch, command: str, expected_digest: str
) -> None:
    (tmp_path / "sample.py").write_text(
        "def needle_function():\n    value = 'needle'\n    return value\n", encoding="utf-8"
    )
    monkeypatch.setenv("TG_DISABLE_SESSION_DAEMON", "1")
    if command == "search":
        args = ["search", "needle", str(tmp_path), "--json", "--cpu"]
    elif command == "find":
        args = ["find", "needle", str(tmp_path), "--json"]
    else:
        args = ["agent", str(tmp_path), "needle", "--json", "--no-deadline"]
    completed = subprocess.run(
        [sys.executable, "-m", "tensor_grep", *args],
        capture_output=True,
        text=False,
        env=os.environ.copy(),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    output = completed.stdout.replace(str(tmp_path).replace("\\", "\\\\").encode("utf-8"), b"ROOT")
    assert hashlib.sha256(output).hexdigest() == expected_digest, output


def test_no_flag_golden_hash_preserves_raw_newline_bytes(tmp_path: Path, monkeypatch) -> None:
    def fake_run(args, **kwargs):
        if kwargs.get("text"):
            return subprocess.CompletedProcess(args, 0, "line\n", "")
        return subprocess.CompletedProcess(args, 0, b"line\r\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    test_no_flag_json_output_matches_origin_main_golden(
        tmp_path, monkeypatch, "search", hashlib.sha256(b"line\r\n").hexdigest()
    )


def test_native_front_doors_route_citations_away_from_ripgrep() -> None:
    from tensor_grep.cli import bootstrap

    registry = Path(__file__).resolve().parents[2] / "rust_core/src/search_flag_registry.rs"
    registry_text = registry.read_text(encoding="utf-8")
    passthrough = registry_text.split(
        "pub(crate) const SEARCH_PYTHON_PASSTHROUGH_FLAGS: &[&str] = &[", 1
    )[1].split("];", 1)[0]
    assert '"--citations"' in passthrough
    assert "--citations" in bootstrap._TG_ONLY_SEARCH_FLAGS
    assert bootstrap._requires_full_cli(["--citations", "X", "."])


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("tg_find", {"query": "needle"}),
        ("tg_agent_capsule", {"query": "needle", "max_tokens": 1200}),
        ("tg_context_render", {"query": "needle", "max_tokens": 1200}),
    ],
)
def test_mcp_citation_tools_emit_envelope(tmp_path: Path, monkeypatch, tool: str, args) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.py").write_text(
        "def needle_function():\n    needle = True\n    return needle\n", encoding="utf-8"
    )
    result = getattr(mcp_server, tool)(path=str(tmp_path), citations=True, **args)
    _assert_citation_envelope(result)


def test_mcp_meta_tools_forward_citations(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    (tmp_path / "sample.py").write_text("def needle():\n    return True\n", encoding="utf-8")
    query_find = json.loads(
        mcp_server.tg_query(action="find", query="needle", path=str(tmp_path), citations=True)
    )
    query_text = json.loads(
        mcp_server.tg_query(
            action="text", pattern="needle", path=str(tmp_path), citations=True, fixed_strings=True
        )
    )
    context_render = json.loads(
        mcp_server.tg_context(
            action="render", query="needle", path=str(tmp_path), citations=True, max_tokens=1200
        )
    )
    context_capsule = json.loads(
        mcp_server.tg_context(
            action="capsule", query="needle", path=str(tmp_path), citations=True, max_tokens=1200
        )
    )
    expected_keys = {
        "version",
        "citations",
        "result_incomplete",
        "incomplete_reason",
        "omitted",
    }
    assert all(
        set(payload) == expected_keys
        for payload in (query_find, query_text, context_render, context_capsule)
    )


def test_mcp_workspace_query_citations_share_one_envelope(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
        (root / "sample.txt").write_text("needle\n", encoding="utf-8")
    result = mcp_server.tg_query(
        action="text",
        pattern="needle",
        path=str(tmp_path),
        workspace_roots=[str(root) for root in roots],
        citations=True,
        fixed_strings=True,
    )
    payload = _assert_citation_envelope(result)
    assert payload["citations"] == [
        {"path": "a/sample.txt", "start_line": 1, "end_line": 1},
        {"path": "b/sample.txt", "start_line": 1, "end_line": 1},
    ]


def test_workspace_citations_do_not_prefix_repository_relative_paths_twice(tmp_path: Path) -> None:
    from tensor_grep.cli.citations import serialize_workspace_citations

    (tmp_path / ".git").mkdir()
    scope = tmp_path / "src"
    scope.mkdir()
    (scope / "sample.py").write_text("needle\n", encoding="utf-8")
    payload = {"citations": [{"path": "src/sample.py", "start_line": 1, "end_line": 1}]}
    result = serialize_workspace_citations({str(scope): payload}, [], tmp_path)
    assert _assert_citation_envelope(result)["citations"] == [
        {"path": "src/sample.py", "start_line": 1, "end_line": 1}
    ]


@pytest.mark.parametrize(
    ("action", "options"),
    [("text", {"pattern": "needle"}), ("find", {"query": "needle"})],
)
def test_mcp_workspace_query_keeps_repository_relative_citations(
    tmp_path: Path, monkeypatch, action: str, options: dict[str, str]
) -> None:
    from tensor_grep.cli import mcp_server

    (tmp_path / ".git").mkdir()
    scope = tmp_path / "src"
    scope.mkdir()
    (scope / "sample.py").write_text(
        "def sample():\n    needle = True\n    return needle\n", encoding="utf-8"
    )
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    result = mcp_server.tg_query(
        action=action,
        path=str(tmp_path),
        workspace_roots=[str(scope)],
        citations=True,
        **options,
    )
    payload = _assert_citation_envelope(result)
    assert payload["citations"]
    assert all(item["path"] == "src/sample.py" for item in payload["citations"])


def test_mcp_search_citations_disclose_scan_cap(tmp_path: Path, monkeypatch) -> None:
    from tensor_grep.cli import mcp_server
    from tensor_grep.core.result import SearchResult

    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("needle\n", encoding="utf-8")
    fake_backend = MagicMock()
    fake_backend.search.return_value = SearchResult(matches=[], total_files=1, total_matches=1)
    with (
        patch("tensor_grep.cli.mcp_server.Pipeline") as mock_pipeline,
        patch("tensor_grep.cli.mcp_server.DirectoryScanner") as mock_scanner,
    ):
        mock_pipeline.return_value.get_backend.return_value = fake_backend
        mock_pipeline.return_value.selected_backend_name = "RustCoreBackend"
        mock_pipeline.return_value.selected_backend_reason = "test"
        mock_pipeline.return_value.selected_gpu_device_ids = []
        mock_pipeline.return_value.selected_gpu_chunk_plan_mb = []
        mock_scanner.return_value.scan_truncated = False
        mock_scanner.return_value.walk.return_value = [
            str(tmp_path / "a.txt"),
            str(tmp_path / "b.txt"),
        ]
        payload = _assert_citation_envelope(
            mcp_server.tg_search("needle", path=str(tmp_path), max_repo_files=1, citations=True)
        )
    assert payload["result_incomplete"] is True
    assert payload["incomplete_reason"] == "scan_limit"
