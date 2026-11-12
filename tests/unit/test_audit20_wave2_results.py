"""Focused regressions for backend refusal, Python filters, and map output limits."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tensor_grep.backends.base import BackendExecutionError, PassthroughExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.backend_fallback import search_with_cpu_fallback
from tensor_grep.cli.repo_map_output_budget import apply_repo_map_output_limits
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError
from tensor_grep.io.directory_scanner import DirectoryScanner


def _source(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("alpha\nERROR here\n", encoding="utf-8")
    return str(source)


@pytest.mark.parametrize("error_type", [BackendExecutionError, PassthroughExecutionError])
def test_ast_and_streamed_refusals_never_retry_cpu(tmp_path, monkeypatch, capsys, error_type):
    calls = []
    original = CPUBackend.search

    def retry(self, *args, **kwargs):
        calls.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CPUBackend, "search", retry)
    error = error_type("structural semantics unavailable")
    with pytest.raises(error_type) as caught:
        search_with_cpu_fallback(_source(tmp_path), "ERROR", SearchConfig(ast=True), error)
    assert caught.value is error
    assert calls == []
    assert capsys.readouterr() == ("", "")


def _pipeline(backend, name):
    pipeline = MagicMock()
    pipeline.selected_backend_name = name
    pipeline.selected_backend_reason = "test_route"
    pipeline.selected_gpu_device_ids = []
    pipeline.selected_gpu_chunk_plan_mb = []
    pipeline.get_backend.return_value = backend
    return pipeline


@pytest.mark.parametrize("surface", ["cli", "mcp", "empty_probe"])
def test_cpu_failure_is_not_retried_by_existing_callers(tmp_path, monkeypatch, surface):
    from typer.testing import CliRunner

    from tensor_grep.cli import mcp_arg_validation, mcp_server
    from tensor_grep.cli.main import app

    source = _source(tmp_path)
    calls = []
    error = BackendExecutionError("CPU failed")

    def failed(self, *args, **kwargs):
        calls.append(True)
        raise error

    monkeypatch.setattr(CPUBackend, "search", failed)
    backend = CPUBackend()
    pipeline = _pipeline(backend, "CPUBackend")
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", lambda **kwargs: pipeline)
    monkeypatch.setattr(mcp_server, "Pipeline", lambda **kwargs: pipeline)
    monkeypatch.setenv("TG_REEXEC_GUARD", "1")
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    if surface == "cli":
        result = CliRunner().invoke(app, ["search", "ERROR", source, "--cpu", "--json"])
        assert calls == [True], (result.exit_code, result.output, result.exception)
        assert result.exit_code == 2
        assert json.loads(result.stdout)["error"] == "backend_error"
    elif surface == "mcp":
        output = mcp_server.tg_search("ERROR", source, structured_json=True)
        assert calls == [True], output
        assert output.startswith("tg_search failed: internal error (")
        assert "total_matches" not in output
    else:
        with pytest.raises(BackendExecutionError) as caught:
            mcp_arg_validation.probe_backend(backend, "ERROR", SearchConfig())
        assert calls == [True]
        assert caught.value is error


@pytest.mark.parametrize("origin", ["RustCoreBackend", "NativeGpuBackend"])
def test_plain_backend_failure_retains_matches_and_durable_disclosure(
    tmp_path, monkeypatch, origin
):
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    backend = MagicMock()
    backend.search.side_effect = BackendExecutionError("execution failure")
    pipeline = _pipeline(backend, origin)
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", lambda **kwargs: pipeline)
    monkeypatch.setenv("TG_REEXEC_GUARD", "1")
    result = CliRunner().invoke(app, ["search", "ERROR", _source(tmp_path), "--json"])
    assert result.exit_code == 0, (result.output, result.exception)
    payload = json.loads(result.stdout)
    assert payload["total_matches"] == 1
    assert "ERROR here" in str(payload["matches"])
    assert "retried on the CPU" in payload["fallback_reason"]
    assert payload["routing_reason"].startswith("cpu_")
    assert payload["routing_backend"] == "CPUBackend"
    backend.search.assert_called_once()


def test_negation_only_glob_keeps_other_files(tmp_path):
    for name in ("app.py", "events.log", "readme.md"):
        (tmp_path / name).write_text("ERROR\n", encoding="utf-8")
    scanner = DirectoryScanner(SearchConfig(glob=["!*.log"]))
    assert {Path(path).name for path in scanner.walk(str(tmp_path))} == {"app.py", "readme.md"}


@pytest.mark.parametrize("field, expected", [("file_type", {"app.rs"}), ("type_not", {"app.py"})])
def test_rust_type_name_filters_both_directions(tmp_path, field, expected):
    for name in ("app.rs", "app.py"):
        (tmp_path / name).write_text("ERROR\n", encoding="utf-8")
    scanner = DirectoryScanner(SearchConfig(**{field: ["rust"]}))
    assert {Path(path).name for path in scanner.walk(str(tmp_path))} == expected


@pytest.mark.parametrize("kind", ["empty", "single_file"])
@pytest.mark.parametrize(
    "field, value",
    [
        ("file_type", ["audit20_unknown_type"]),
        ("type_not", ["audit20_unknown_type"]),
        ("iglob", ["*.MD"]),
        ("type_add", ["custom:*.custom"]),
        ("type_clear", "rust"),
    ],
)
def test_unsupported_filters_refused_before_walking(tmp_path, monkeypatch, kind, field, value):
    path = _source(tmp_path) if kind == "single_file" else str(tmp_path)

    def unexpected_walk(*args, **kwargs):
        pytest.fail("unsupported filter reached filesystem walk")

    monkeypatch.setattr("tensor_grep.io.directory_scanner.os.walk", unexpected_walk)
    with pytest.raises(ConfigurationError) as caught:
        list(DirectoryScanner(SearchConfig(**{field: value})).walk(path))
    assert field.replace("_", "-") in str(caught.value).replace("_", "-") or (
        "type" in field and "type" in str(caught.value).lower()
    )


def test_existing_positive_glob_and_extension_filter_still_work(tmp_path):
    for name in ("app.py", "drop.py", "app.rs"):
        (tmp_path / name).write_text("ERROR\n", encoding="utf-8")
    scanner = DirectoryScanner(SearchConfig(glob=["*.py", "!drop.py"], file_type=["py"]))
    assert [Path(path).name for path in scanner.walk(str(tmp_path))] == ["app.py"]


@pytest.mark.parametrize("max_files", [1, 100])
@pytest.mark.parametrize("record_key", ["symbols", "imports"])
def test_map_retains_symbols_and_imports_for_selected_tests(max_files, record_key):
    payload = {
        "files": ["a.py", "b.py"],
        "tests": ["tests/test_a.py", "tests/test_b.py"],
        "symbols": [
            {"file": path, "name": name}
            for path, name in [
                ("a.py", "f"),
                ("b.py", "g"),
                ("tests/test_a.py", "test_f"),
                ("tests/test_b.py", "test_g"),
            ]
        ],
        "imports": [
            {"file": path, "module": "pytest"}
            for path in [
                "a.py",
                "b.py",
                "tests/test_a.py",
                "tests/test_b.py",
            ]
        ],
        "test_matches": [{"path": "tests/test_a.py"}, {"path": "tests/test_b.py"}],
    }
    limited = apply_repo_map_output_limits(payload, max_files=max_files)
    retained = set(limited["files"]) | set(limited["tests"])
    assert {item["path"] for item in limited["test_matches"]} == set(limited["tests"])
    omitted = 1 if max_files == 1 else 0
    assert limited["output_limit"]["omitted_files"] == omitted
    assert limited["output_limit"]["omitted_tests"] == omitted
    assert limited["output_limit"]["possibly_truncated"] is bool(omitted)
    assert {item["file"] for item in limited[record_key]} == retained


def test_map_actually_omitted_records_are_removed():
    payload = {
        "files": ["a.py", "b.py"],
        "tests": ["tests/test_a.py", "tests/test_b.py"],
        "symbols": [{"file": "b.py"}, {"file": "tests/test_b.py"}],
        "imports": [{"file": "b.py"}, {"file": "tests/test_b.py"}],
        "test_matches": [{"path": "tests/test_b.py"}],
    }
    limited = apply_repo_map_output_limits(payload, max_files=1)
    assert limited["files"] == ["a.py"]
    assert limited["tests"] == ["tests/test_a.py"]
    assert limited["symbols"] == limited["imports"] == limited["test_matches"] == []
    assert limited["output_limit"]["omitted_files"] == 1
    assert limited["output_limit"]["omitted_tests"] == 1
    assert limited["output_limit"]["possibly_truncated"] is True
