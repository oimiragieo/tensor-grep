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


def test_run_benchmarks_should_target_bootstrap_entrypoint():
    module = _load_script_module("run_benchmarks_script_cmd", "benchmarks/run_benchmarks.py")

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"])

    assert cmd[:3] == [module.sys.executable, "-m", "tensor_grep.cli.bootstrap"]
    assert cmd[3:] == ["search", "--no-ignore", "ERROR", "bench_data"]


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


def test_run_ast_benchmarks_should_target_bootstrap_entrypoint():
    module = _load_script_module(
        "run_ast_benchmarks_script_cmd", "benchmarks/run_ast_benchmarks.py"
    )

    cmd = module.build_tg_ast_benchmark_cmd(["run", "--ast", "pattern", "bench_ast_data"])

    assert cmd[:3] == [module.sys.executable, "-m", "tensor_grep.cli.bootstrap"]
    assert cmd[3:] == ["run", "--ast", "pattern", "bench_ast_data"]


def test_run_ast_workflow_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script", "benchmarks/run_ast_workflow_benchmarks.py"
    )
    monkeypatch.delenv("TENSOR_GREP_AST_WORKFLOW_BENCH_DIR", raising=False)

    path = module.resolve_ast_workflow_bench_dir()

    assert path.parts[-2:] == ("artifacts", "bench_ast_workflow")


def test_run_ast_workflow_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_override",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )
    override = tmp_path / "bench_ast_workflow_override"
    monkeypatch.setenv("TENSOR_GREP_AST_WORKFLOW_BENCH_DIR", str(override))

    path = module.resolve_ast_workflow_bench_dir()

    assert path == override.resolve()


def test_run_ast_workflow_benchmarks_should_target_bootstrap_entrypoint():
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_cmd",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )

    cmd = module.build_tg_ast_workflow_cmd(["scan", "--config", "sgconfig.yml"])

    assert cmd[:3] == [module.sys.executable, "-m", "tensor_grep.cli.bootstrap"]
    assert cmd[3:] == ["scan", "--config", "sgconfig.yml"]


def test_run_ast_workflow_benchmarks_should_generate_rule_tests(tmp_path):
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_project",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )

    module.generate_ast_workflow_project(tmp_path, rule_count=2, file_count=1)

    config_text = (tmp_path / "scan_project" / "sgconfig.yml").read_text(encoding="utf-8")
    assert "testDirs:" in config_text
    test_text = (tmp_path / "scan_project" / "tests" / "test_000.yml").read_text(encoding="utf-8")
    assert (tmp_path / "scan_project" / "tests" / "test_000.yml").exists()
    assert "invalid:\n  - |\n" in test_text


def test_run_ast_workflow_benchmarks_should_emit_run_scan_and_test_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_rows",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "src"))
    monkeypatch.setattr(module, "resolve_ast_workflow_bench_dir", lambda: tmp_path / "bench")

    def _fake_run_cmd_capture(cmd, cwd):
        if cmd[3] == "run":
            return 0.15, 0
        if cmd[3] == "scan":
            return 0.25, 0
        if cmd[3] == "test":
            return 0.40, 0
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(module, "run_cmd_capture", _fake_run_cmd_capture)

    captured: dict[str, object] = {}

    def _fake_write_json(path, payload):
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr("tensor_grep.perf_guard.write_json", _fake_write_json)

    exit_code = module.main()

    assert exit_code == 0
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["suite"] == "run_ast_workflow_benchmarks"
    rows = payload["rows"]
    assert rows == [
        {"name": "ast_run_workflow", "tg_time_s": 0.15, "exit_code": 0},
        {"name": "ast_scan_workflow", "tg_time_s": 0.25, "exit_code": 0},
        {"name": "ast_test_workflow", "tg_time_s": 0.4, "exit_code": 0},
    ]


def test_run_gpu_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module("run_gpu_benchmarks_script", "benchmarks/run_gpu_benchmarks.py")
    monkeypatch.delenv("TENSOR_GREP_GPU_BENCH_DATA_DIR", raising=False)

    path = module.resolve_gpu_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "gpu_bench_data")


def test_run_gpu_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module("run_gpu_benchmarks_script", "benchmarks/run_gpu_benchmarks.py")
    override = tmp_path / "bench_gpu_override"
    monkeypatch.setenv("TENSOR_GREP_GPU_BENCH_DATA_DIR", str(override))

    path = module.resolve_gpu_bench_data_dir()

    assert path == override.resolve()


def test_run_hot_query_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_hot_query_benchmarks_script", "benchmarks/run_hot_query_benchmarks.py"
    )
    monkeypatch.delenv("TENSOR_GREP_HOT_BENCH_DATA_DIR", raising=False)

    path = module.resolve_hot_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "hot_bench_data")


def test_run_hot_query_benchmarks_should_honor_data_dir_override(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_override", "benchmarks/run_hot_query_benchmarks.py"
    )
    override = tmp_path / "hot_bench_override"
    monkeypatch.setenv("TENSOR_GREP_HOT_BENCH_DATA_DIR", str(override))

    path = module.resolve_hot_bench_data_dir()

    assert path == override.resolve()


def test_run_hot_query_benchmarks_should_build_cpu_probe_script(tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_probe", "benchmarks/run_hot_query_benchmarks.py"
    )
    script_path = tmp_path / "cpu_probe.py"

    module.write_cpu_probe_script(script_path)

    text = script_path.read_text(encoding="utf-8")
    assert "CPUBackend" in text
    assert "force python fallback" in text


def test_run_gpu_benchmarks_should_skip_cybert_when_triton_is_unreachable():
    module = _load_script_module("run_gpu_benchmarks_script", "benchmarks/run_gpu_benchmarks.py")

    assert module._is_skippable_cybert_exception(
        RuntimeError("CyBERT inference failed: [Errno 10061] connection refused")
    )
    assert module._is_skippable_cybert_exception(
        RuntimeError("CyBERT inference failed: connection refused")
    )
    assert module._is_skippable_cybert_exception(
        RuntimeError("CyBERT inference failed: actively refused it")
    )
    assert not module._is_skippable_cybert_exception(
        RuntimeError("CyBERT inference failed: invalid tensor shape")
    )


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


def test_check_regression_should_resolve_auto_baseline_for_windows_platform(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_auto_windows", "benchmarks/check_regression.py"
    )
    baselines_dir = tmp_path / "benchmarks" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baselines_dir / "run_benchmarks.windows.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.05}],
        }),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_regression.py",
            "--baseline",
            "auto",
            "--current",
            str(current_path),
            "--max-regression-pct",
            "20",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0


def test_check_regression_should_fail_when_auto_baseline_platform_is_unavailable(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "check_regression_script_auto_missing", "benchmarks/check_regression.py"
    )
    (tmp_path / "benchmarks" / "baselines").mkdir(parents=True, exist_ok=True)
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "darwin", "machine": "arm64"},
            "rows": [{"name": "x", "tg_time_s": 1.05}],
        }),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_regression.py",
            "--baseline",
            "auto",
            "--current",
            str(current_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 2


def test_summarize_benchmarks_should_resolve_auto_baseline_for_windows_platform(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "summarize_benchmarks_auto_windows", "benchmarks/summarize_benchmarks.py"
    )
    baselines_dir = tmp_path / "benchmarks" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baselines_dir / "run_benchmarks.windows.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.05}],
        }),
        encoding="utf-8",
    )
    output_path = tmp_path / "summary.md"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "summarize_benchmarks.py",
            "--baseline",
            "auto",
            "--current",
            str(current_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    assert output_path.exists()
    assert "run_benchmarks.windows.json" in output_path.read_text(encoding="utf-8")


def test_summarize_benchmarks_should_fail_when_auto_baseline_platform_is_unavailable(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "summarize_benchmarks_auto_missing", "benchmarks/summarize_benchmarks.py"
    )
    (tmp_path / "benchmarks" / "baselines").mkdir(parents=True, exist_ok=True)
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "darwin", "machine": "arm64"},
            "rows": [{"name": "x", "tg_time_s": 1.05}],
        }),
        encoding="utf-8",
    )
    output_path = tmp_path / "summary.md"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "summarize_benchmarks.py",
            "--baseline",
            "auto",
            "--current",
            str(current_path),
            "--output",
            str(output_path),
        ],
    )

    try:
        module.main()
        raise AssertionError("Expected SystemExit for unsupported auto baseline platform")
    except SystemExit as exc:
        assert "Unsupported platform for --baseline auto" in str(exc)


def test_run_ast_benchmarks_should_emit_json_artifact_when_ast_grep_is_missing(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_ast_benchmarks_missing_ast", "benchmarks/run_ast_benchmarks.py"
    )
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: None)
    monkeypatch.setattr(module, "generate_ast_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_ast_bench_data_dir", lambda: tmp_path / "bench_ast_data")

    captured: dict[str, object] = {}

    def _fake_write_json(path, payload):
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr("tensor_grep.perf_guard.write_json", _fake_write_json)

    exit_code = module.main()

    assert exit_code == 0
    assert captured["path"] == tmp_path / "bench_run_ast_benchmarks.json"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["suite"] == "run_ast_benchmarks"
    assert payload["rows"] == []
    assert payload["parity_failures"] == 0
