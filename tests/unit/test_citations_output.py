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
    assert "cannot be combined with" in result.output.lower()


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
        text=True,
        encoding="utf-8",
        errors="strict",
        env=os.environ.copy(),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    output = completed.stdout.replace(str(tmp_path).replace("\\", "\\\\"), "ROOT")
    assert hashlib.sha256(output.encode("utf-8")).hexdigest() == expected_digest, output


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
