import importlib.util
import json
from pathlib import Path


def _load_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module("run_benchmarks_script", "benchmarks/run_benchmarks.py")
    monkeypatch.delenv("TENSOR_GREP_BENCH_DATA_DIR", raising=False)

    path = module.resolve_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "bench_data")


def test_run_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script", "benchmarks/run_benchmarks.py")
    override = tmp_path / "bench_override"
    monkeypatch.setenv("TENSOR_GREP_BENCH_DATA_DIR", str(override))

    path = module.resolve_bench_data_dir()

    assert path == override.resolve()


def test_run_ast_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module("run_ast_benchmarks_script", "benchmarks/run_ast_benchmarks.py")
    monkeypatch.delenv("TENSOR_GREP_AST_BENCH_DATA_DIR", raising=False)

    path = module.resolve_ast_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "bench_ast_data")


def test_run_ast_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module("run_ast_benchmarks_script", "benchmarks/run_ast_benchmarks.py")
    override = tmp_path / "bench_ast_override"
    monkeypatch.setenv("TENSOR_GREP_AST_BENCH_DATA_DIR", str(override))

    path = module.resolve_ast_bench_data_dir()

    assert path == override.resolve()


def test_check_regression_should_refuse_cross_environment_comparison_by_default(
    monkeypatch, tmp_path
):
    module = _load_script_module("check_regression_script", "benchmarks/check_regression.py")
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "linux", "machine": "x86_64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.2}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_regression.py",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 2


def test_check_regression_should_allow_cross_environment_comparison_with_override(
    monkeypatch, tmp_path
):
    module = _load_script_module("check_regression_script", "benchmarks/check_regression.py")
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "linux", "machine": "x86_64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.05}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_regression.py",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--allow-env-mismatch",
            "--max-regression-pct",
            "20",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
