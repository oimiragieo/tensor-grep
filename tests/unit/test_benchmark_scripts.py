import importlib.util
import json
import subprocess
import sys
import zipfile
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


def test_run_benchmarks_should_extract_windows_rg_zip_when_rg_missing(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_rg_zip", "benchmarks/run_benchmarks.py")
    bench_dir = tmp_path / "benchmarks"
    archive = bench_dir / "rg.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("ripgrep-14.1.0-x86_64-pc-windows-msvc/rg.exe", "fake rg")

    monkeypatch.setattr(module, "__file__", str(bench_dir / "run_benchmarks.py"))
    monkeypatch.setattr(module.shutil, "which", lambda _binary: None)
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")

    resolved = Path(module.resolve_rg_binary())

    assert resolved == bench_dir / "ripgrep-14.1.0-x86_64-pc-windows-msvc" / "rg.exe"
    assert resolved.read_text(encoding="utf-8") == "fake rg"


def test_run_benchmarks_should_record_three_samples_and_median(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_samples", "benchmarks/run_benchmarks.py")
    monkeypatch.setattr("sys.argv", ["run_benchmarks.py"])
    monkeypatch.setattr(module, "SCENARIOS", [{
        "name": "1. Simple String Match",
        "rg_args": ["rg", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "ERROR", "bench_data"],
    }])
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)

    timing_samples = iter(
        [
            9.9,
            8.8,
            0.40,
            0.20,
            0.30,
            0.80,
            0.60,
            0.70,
        ]
    )
    timing_calls: list[list[str]] = []
    capture_calls: list[list[str]] = []

    def _fake_run_cmd_timing(cmd, capture_stdout=False):
        timing_calls.append(cmd)
        return next(timing_samples)

    def _fake_run_cmd_capture(cmd):
        capture_calls.append(cmd)
        if cmd[0] == "rg":
            return 0.0, "rg result"
        return 0.0, "tg result"

    monkeypatch.setattr(module, "run_cmd_timing", _fake_run_cmd_timing)
    monkeypatch.setattr(module, "run_cmd_capture", _fake_run_cmd_capture)

    captured: dict[str, object] = {}

    def _fake_write_json(path, payload):
        captured["path"] = path
        captured["payload"] = payload

    monkeypatch.setattr("tensor_grep.perf_guard.ensure_artifacts_dir", lambda _root: tmp_path)
    monkeypatch.setattr("tensor_grep.perf_guard.write_json", _fake_write_json)

    module.main()

    assert len(timing_calls) == 8
    assert len(capture_calls) == 2
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["timing_samples_per_scenario"] == 3
    rows = payload["rows"]
    assert rows == [
        {
            "name": "1. Simple String Match",
            "rg_samples_s": [0.4, 0.2, 0.3],
            "rg_time_s": 0.3,
            "tg_samples_s": [0.8, 0.6, 0.7],
            "tg_time_s": 0.7,
            "parity": "PASS",
        }
    ]


def test_run_benchmarks_should_honor_output_and_milestone_args(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_args", "benchmarks/run_benchmarks.py")
    monkeypatch.setattr(
        module,
        "SCENARIOS",
        [
            {
                "name": "1. Simple String Match",
                "rg_args": ["rg", "ERROR", "bench_data"],
                "tg_args": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "compare_results", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "run_cmd_timing", lambda *_args, **_kwargs: 0.25)
    monkeypatch.setattr(module, "run_cmd_capture", lambda cmd: (0.0, "ok"))
    output_path = tmp_path / "bench_m2.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_benchmarks.py",
            "--output",
            str(output_path),
            "--milestone",
            "m2",
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["milestone"] == "m2"


def test_run_hot_query_benchmarks_should_report_regression_status(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_status", "benchmarks/run_hot_query_benchmarks.py"
    )
    monkeypatch.setattr(module, "resolve_hot_bench_data_dir", lambda: tmp_path / "hot")
    monkeypatch.setattr(module, "_prepare_corpus", lambda data_dir: data_dir / "hot_corpus.log")
    monkeypatch.setattr(module, "write_cpu_probe_script", lambda _path: None)
    monkeypatch.setattr(
        module,
        "_run_stringzilla_hot_query",
        lambda *_args, **_kwargs: {
            "name": "repeated_fixed_string",
            "first_s": 1.0,
            "second_s": 0.2,
            "first_reason": "index_build",
            "second_reason": "index_hit",
            "matches": 2000,
        },
    )
    monkeypatch.setattr(
        module,
        "_run_cpu_hot_query",
        lambda *_args, **_kwargs: {
            "name": "repeated_regex_prefilter",
            "first_s": 0.8,
            "second_s": 0.3,
            "first_reason": "regex_scan",
            "second_reason": "regex_prefilter_hit",
            "matches": 2000,
        },
    )
    output_path = tmp_path / "bench_hot.json"
    monkeypatch.setattr(
        "sys.argv",
        ["run_hot_query_benchmarks.py", "--output", str(output_path)],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["no_regressions"] is True
    assert payload["rows"][0]["status"] == "PASS"
    assert payload["rows"][1]["status"] == "PASS"


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


def test_run_ast_benchmarks_should_target_native_tg_binary(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_benchmarks_script_cmd", "benchmarks/run_ast_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd = module.build_tg_ast_benchmark_cmd(["run", "--lang", "python", "pattern", "bench_ast_data"])

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["run", "--lang", "python", "pattern", "bench_ast_data"]


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
    assert "sys.path.insert" in text


def test_run_hot_query_benchmarks_should_run_directly_without_site_packages(tmp_path):
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(root / "benchmarks" / "run_hot_query_benchmarks.py"),
            "--help",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Benchmark hot repeated-query cache paths." in result.stdout


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
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "linux", "machine": "x86_64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.2}],
            }
        ),
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
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "linux", "machine": "x86_64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.05}],
            }
        ),
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


def test_check_regression_should_use_five_percent_default_threshold(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_default_threshold", "benchmarks/check_regression.py"
    )
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.06}],
            }
        ),
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

    assert exit_code == 1


def test_check_regression_should_compare_hot_query_benchmarks(monkeypatch, tmp_path):
    module = _load_script_module("check_regression_script_hot", "benchmarks/check_regression.py")
    baseline_path = tmp_path / "baseline_hot.json"
    current_path = tmp_path / "current_hot.json"
    payload = {
        "suite": "run_hot_query_benchmarks",
        "environment": {"platform": "windows", "machine": "amd64"},
        "rows": [{"name": "repeated_fixed_string", "first_s": 1.0, "second_s": 0.4}],
    }
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path.write_text(
        json.dumps(
            {
                **payload,
                "rows": [{"name": "repeated_fixed_string", "first_s": 1.02, "second_s": 0.43}],
            }
        ),
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

    assert exit_code == 1

def test_check_regression_should_run_directly_without_site_packages(tmp_path):
    root = Path(__file__).resolve().parents[2]
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    payload = {
        "suite": "run_benchmarks",
        "environment": {"platform": "windows", "machine": "amd64"},
        "rows": [{"name": "1. Simple String Match", "tg_time_s": 1.0, "rg_time_s": 0.5}],
    }
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(root / "benchmarks" / "check_regression.py"),
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "No benchmark regressions detected." in result.stdout


def test_check_regression_should_resolve_auto_baseline_for_windows_platform(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_auto_windows", "benchmarks/check_regression.py"
    )
    baselines_dir = tmp_path / "benchmarks" / "baselines"
    baselines_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baselines_dir / "run_benchmarks.windows.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.05}],
            }
        ),
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


def test_check_regression_should_resolve_auto_milestone_baseline(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_auto_milestone", "benchmarks/check_regression.py"
    )
    milestones_dir = tmp_path / "benchmarks"
    milestones_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = milestones_dir / "baseline_m1.json"
    baseline_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "milestone": "m1",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "milestone": "m2",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.04}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_regression.py",
            "--baseline",
            "auto",
            "--milestone",
            "m1",
            "--current",
            str(current_path),
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
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "darwin", "machine": "arm64"},
                "rows": [{"name": "x", "tg_time_s": 1.05}],
            }
        ),
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
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "windows", "machine": "amd64"},
                "rows": [{"name": "x", "tg_time_s": 1.05}],
            }
        ),
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
        json.dumps(
            {
                "suite": "run_benchmarks",
                "environment": {"platform": "darwin", "machine": "arm64"},
                "rows": [{"name": "x", "tg_time_s": 1.05}],
            }
        ),
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
    output_path = tmp_path / "bench_ast_m3.json"
    tg_binary = tmp_path / "tg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    hyperfine_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["run_ast_benchmarks.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: None)
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)
    monkeypatch.setattr(
        module,
        "ensure_ast_bench_corpus",
        lambda *_args, **_kwargs: {
            "corpus_dir": tmp_path / "bench_ast_data",
            "manifest_path": tmp_path / "bench_ast_data.manifest.sha256",
            "file_count": 1000,
            "total_loc": 50000,
        },
    )

    exit_code = module.main()

    assert exit_code == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_ast_m3"
    assert payload["passed"] is False
    assert "ast-grep binary not found" in payload["error"]
