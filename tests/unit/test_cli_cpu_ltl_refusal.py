"""CPU-origin temporal refusals retain their cause without retrying the CPU."""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.cli.main import app


@pytest.fixture
def cpu_ltl_cli(tmp_path, monkeypatch):
    source = tmp_path / "sequence.log"
    source.write_text("open request\nmid line\nclose request\n", encoding="utf-8")
    backend = CPUBackend()
    pipeline = SimpleNamespace(
        get_backend=lambda: backend,
        selected_backend_name="CPUBackend",
        selected_backend_reason="forced_cpu",
        selected_gpu_device_ids=[],
        selected_gpu_chunk_plan_mb=[],
    )
    monkeypatch.setattr("tensor_grep.core.pipeline.Pipeline", lambda **kwargs: pipeline)
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(
        "tensor_grep.backends.ripgrep_backend.RipgrepBackend.is_available", lambda self: False
    )
    monkeypatch.setenv("TG_REEXEC_GUARD", "1")
    calls = []
    original_search = CPUBackend.search

    def search(self, *args, **kwargs):
        calls.append(self)
        return original_search(self, *args, **kwargs)

    monkeypatch.setattr(CPUBackend, "search", search)

    def invoke(*args):
        return CliRunner().invoke(
            app, ["search", "open -> eventually close", str(source), "--ltl", "--cpu", *args]
        )

    return invoke, calls, backend


@pytest.mark.parametrize("json_mode", [False, True])
def test_cpu_ltl_missing_engine_refuses_once_with_original_cause(
    cpu_ltl_cli, monkeypatch, json_mode
):
    invoke, calls, backend = cpu_ltl_cli
    engine_calls = []

    def unavailable(self, *args, **kwargs):
        engine_calls.append(self)
        raise ModuleNotFoundError("No module named 'tensor_grep.rust_core'")

    monkeypatch.setattr(CPUBackend, "_rust_match_set", unavailable)
    result = invoke(*(["--json"] if json_mode else []))
    assert calls == [backend], (result.output, result.exception)
    assert engine_calls == [backend]
    assert result.exit_code == 2
    cause = (
        "cannot safely evaluate this --ltl query through CPUBackend without the "
        "linear-time Rust engine (ModuleNotFoundError: No module named "
        "'tensor_grep.rust_core'); install ripgrep or drop --ltl"
    )
    if json_mode:
        payload = json.loads(result.stdout)
        assert payload["error"] == "backend_error"
        assert payload["detail"] == cause
        assert payload["ok"] is False
        assert result.stderr == ""
    else:
        assert result.stdout == ""
        assert result.stderr == f"Error: {cause}\n"
    assert "retried on the CPU backend" not in result.output
    assert "Traceback" not in result.output


def test_cpu_ltl_available_engine_positive_control(cpu_ltl_cli, monkeypatch):
    invoke, calls, backend = cpu_ltl_cli
    patterns = []

    def match_set(self, file_path, pattern, ignore_case, fixed_strings, invert_match):
        patterns.append(pattern)
        return [(1, "open request")] if pattern == "open" else [(3, "close request")]

    monkeypatch.setattr(CPUBackend, "_rust_match_set", match_set)
    result = invoke("--json")
    assert calls == [backend], (result.output, result.exception)
    assert patterns == ["open", "close"]
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total_matches"] > 0
    assert "open request" in str(payload["matches"])
    assert "close request" in str(payload["matches"])
    assert result.stderr == ""
