import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


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
    assert calls[0][-1] == calls[1][-1] == r"ERROR.*timeout"


@pytest.mark.parametrize(
    ("first", "second", "expected_routes", "message"),
    [
        (
            {"matches": 2, "routing_reason": "cpu_rust_regex"},
            {"matches": 3, "routing_reason": "cpu_rust_regex"},
            ("cpu_rust_regex", "cpu_rust_regex"),
            "different match counts",
        ),
        (
            {"matches": 0, "routing_reason": "cpu_rust_regex"},
            {"matches": 0, "routing_reason": "cpu_rust_regex"},
            ("cpu_rust_regex", "cpu_rust_regex"),
            "returned no matches",
        ),
        (
            {"matches": 2, "routing_reason": "cpu_python_regex"},
            {"matches": 2, "routing_reason": "cpu_rust_regex"},
            ("cpu_rust_regex", "cpu_rust_regex"),
            "unexpected routes",
        ),
    ],
)
def test_repeat_query_validation_rejects_different_results_or_routes(
    first, second, expected_routes, message
):
    module = _load_hot_query_module()

    error = module._repeat_validation_error("repeated_regex_native", first, second, expected_routes)

    assert error is not None
    assert message in error
    row = module.evaluate_hot_query_row(
        {
            "name": "repeated_regex_native",
            "status": "FAIL",
            "validation_error": error,
        },
        max_regression_pct=5.0,
    )
    assert row["status"] == "FAIL"


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
    assert row["regression_tolerance_s"] == 0.005


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_hot_query_gate_rejects_invalid_regression_thresholds(threshold):
    module = _load_hot_query_module()

    with pytest.raises(ValueError, match="max_regression_pct must be a finite number >= 0"):
        module.evaluate_hot_query_row(
            {"name": "repeated_regex_native", "first_s": 1.0, "second_s": 2.0},
            max_regression_pct=threshold,
        )


def test_hot_query_cli_rejects_nonfinite_threshold_before_benchmark_work(monkeypatch, capsys):
    module = _load_hot_query_module()
    monkeypatch.setattr(sys, "argv", ["run_hot_query_benchmarks.py", "--max-regression-pct", "nan"])
    monkeypatch.setattr(
        module,
        "resolve_hot_bench_data_dir",
        lambda: pytest.fail("invalid threshold must be rejected before benchmark setup"),
    )

    with pytest.raises(SystemExit) as exc_info:
        module.main()

    assert exc_info.value.code == 2
    assert "max_regression_pct must be a finite number >= 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("first_s", "second_s"),
    [(float("inf"), float("inf")), (float("-inf"), 1.0), (1.0, float("nan"))],
)
def test_nonfinite_hot_query_measurement_fails_gate_command(
    monkeypatch, tmp_path, first_s, second_s
):
    module = _load_hot_query_module()
    output_path = tmp_path / "hot.json"
    monkeypatch.setattr(sys, "argv", ["run_hot_query_benchmarks.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "resolve_hot_bench_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_prepare_corpus", lambda _data_dir: tmp_path / "corpus")
    monkeypatch.setattr(module, "write_cpu_probe_script", lambda _path: None)
    monkeypatch.setattr(module, "write_stringzilla_probe_script", lambda _path: None)
    monkeypatch.setattr(
        module,
        "_run_stringzilla_hot_query",
        lambda *_args: {"name": "stringzilla", "first_s": first_s, "second_s": second_s},
    )
    monkeypatch.setattr(
        module,
        "_run_cpu_hot_query",
        lambda *_args: {"name": "cpu", "first_s": 1.0, "second_s": 0.5},
    )

    assert module.main() == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["no_regressions"] is False
    assert payload["rows"][0]["status"] == "FAIL"


def test_unknown_hot_query_measurement_fails_gate_command(monkeypatch, tmp_path):
    module = _load_hot_query_module()
    output_path = tmp_path / "hot.json"
    monkeypatch.setattr(sys, "argv", ["run_hot_query_benchmarks.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "resolve_hot_bench_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "_prepare_corpus", lambda _data_dir: tmp_path / "corpus")
    monkeypatch.setattr(module, "write_cpu_probe_script", lambda _path: None)
    monkeypatch.setattr(module, "write_stringzilla_probe_script", lambda _path: None)
    monkeypatch.setattr(
        module,
        "_run_stringzilla_hot_query",
        lambda *_args: {"name": "stringzilla", "first_s": 0.0, "second_s": 0.0},
    )
    monkeypatch.setattr(
        module,
        "_run_cpu_hot_query",
        lambda *_args: {"name": "cpu", "first_s": 1.0, "second_s": 0.5},
    )

    assert module.main() == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["no_regressions"] is False
    assert payload["rows"][0]["status"] == "UNKNOWN"


def test_native_regex_hot_query_gate_allows_dogfood_scale_timer_jitter():
    module = _load_hot_query_module()

    row = module.evaluate_hot_query_row(
        {
            "name": "repeated_regex_native",
            "first_s": 0.0123,
            "second_s": 0.0153,
            "first_reason": "cpu_rust_regex",
            "second_reason": "cpu_rust_regex",
            "matches": 2000,
        },
        max_regression_pct=5.0,
    )

    assert row["status"] == "PASS"
    assert row["regression_tolerance_s"] >= 0.003
