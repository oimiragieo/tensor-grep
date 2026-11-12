"""Python CLI/MCP filter refusals; CliRunner deliberately bypasses bootstrap."""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli import mcp_arg_validation, mcp_server
from tensor_grep.cli.main import app


def _path(tmp_path, kind):
    if kind == "single_file":
        source = tmp_path / "app.py"
        source.write_text("ERROR here\n", encoding="utf-8")
        return str(source)
    return str(tmp_path)


@pytest.fixture
def cpu_search(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_REEXEC_GUARD", "1")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # MCP has no force-CPU argument; retain real scanning and CPU execution.
    pipeline = SimpleNamespace(
        selected_backend_name="CPUBackend",
        selected_backend_reason="test_cpu_route",
        selected_gpu_device_ids=[],
        selected_gpu_chunk_plan_mb=[],
        get_backend=lambda: CPUBackend(),
    )
    monkeypatch.setattr(mcp_server, "Pipeline", lambda **kwargs: pipeline)


@pytest.mark.parametrize("kind", ["empty", "single_file"])
@pytest.mark.parametrize("filter_args", [["--iglob", "*.MD"], ["--type", "audit20unknown"]])
def test_cli_filter_refusal_is_structured_exit_two(tmp_path, cpu_search, kind, filter_args):
    result = CliRunner().invoke(
        app, ["search", "ERROR", _path(tmp_path, kind), "--cpu", "--json", *filter_args]
    )
    assert result.exit_code == 2, (result.output, result.exception)
    payload = json.loads(result.stdout)
    assert payload["error"] == "configuration_error"
    assert "not supported" in payload["detail"]
    assert "total_matches" not in payload


@pytest.mark.parametrize("kind", ["empty", "single_file"])
def test_mcp_unknown_filter_is_structured_invalid_input(tmp_path, cpu_search, kind):
    output = mcp_server.tg_search(
        "ERROR", _path(tmp_path, kind), type_filter="audit20unknown", structured_json=True
    )
    assert not output.startswith("tg_search failed:"), output
    payload = json.loads(output)
    assert payload["error"] == {
        "code": "invalid_input",
        "message": mcp_arg_validation.FILE_TYPE_UNKNOWN_MESSAGE,
    }
    assert "audit20unknown" not in payload["error"]["message"]
    assert payload["matches"] == []
    assert payload["total_matches"] == payload["total_files"] == 0
    assert payload["result_incomplete"] is True


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_supported_filter_still_searches_real_cpu(tmp_path, cpu_search, surface):
    source = _path(tmp_path, "single_file")
    if surface == "cli":
        result = CliRunner().invoke(app, ["search", "ERROR", source, "--cpu", "--json", "-t", "py"])
        assert result.exit_code == 0, (result.output, result.exception)
        output = result.stdout
    else:
        output = mcp_server.tg_search("ERROR", source, type_filter="py", structured_json=True)
    payload = json.loads(output)
    assert payload["total_matches"] == 1
    assert "ERROR here" in str(payload["matches"])
    assert "error" not in payload
