import importlib.util
import json
import subprocess
from pathlib import Path


def _load_hot_query_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "benchmarks" / "run_hot_query_benchmarks.py"
    spec = importlib.util.spec_from_file_location("run_hot_query_benchmarks_native", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cpu_hot_query_probe_does_not_force_python_fallback(tmp_path):
    module = _load_hot_query_module()
    script_path = tmp_path / "cpu_probe.py"

    module.write_cpu_probe_script(script_path)

    text = script_path.read_text(encoding="utf-8")
    assert "CPUBackend" in text
    assert "FailingRustBackend" not in text
    assert "tensor_grep.rust_core" not in text
    assert "sys.modules" not in text


def test_cpu_hot_query_row_reports_native_route(monkeypatch, tmp_path):
    module = _load_hot_query_module()
    calls: list[list[str]] = []

    def _fake_check_output(cmd, **_kwargs):
        calls.append([str(part) for part in cmd])
        return json.dumps({
            "matches": 2,
            "routing_reason": "cpu_rust_regex",
            "seconds": 0.01,
        })

    monkeypatch.setattr(subprocess, "check_output", _fake_check_output)

    row = module._run_cpu_hot_query(
        tmp_path / "corpus.log",
        tmp_path / "cache",
        tmp_path / "cpu_probe.py",
    )

    assert row["name"] == "repeated_regex_native"
    assert row["first_reason"] == "cpu_rust_regex"
    assert row["second_reason"] == "cpu_rust_regex"
    assert len(calls) == 2


def test_native_regex_hot_query_gate_allows_small_timer_jitter():
    module = _load_hot_query_module()

    row = module.evaluate_hot_query_row(
        {
            "name": "repeated_regex_native",
            "first_s": 0.0067,
            "second_s": 0.0072,
            "first_reason": "cpu_rust_regex",
            "second_reason": "cpu_rust_regex",
            "matches": 2000,
        },
        max_regression_pct=5.0,
    )

    assert row["status"] == "PASS"
    assert row["regression_tolerance_s"] == 0.002
