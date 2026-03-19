import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

BENCHMARK_JSON_SCRIPTS = [
    "benchmarks/run_benchmarks.py",
    "benchmarks/run_native_cpu_benchmarks.py",
    "benchmarks/run_hot_query_benchmarks.py",
    "benchmarks/run_ast_benchmarks.py",
    "benchmarks/run_ast_multilang_benchmarks.py",
    "benchmarks/run_ast_rewrite_benchmarks.py",
    "benchmarks/run_ast_workflow_benchmarks.py",
    "benchmarks/run_gpu_benchmarks.py",
    "benchmarks/run_gpu_native_benchmarks.py",
    "benchmarks/run_harness_loop_benchmark.py",
    "benchmarks/run_ast_parity_check.py",
    "benchmarks/run_compat_checks.py",
    "benchmarks/run_index_scaling_benchmark.py",
]


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


def test_run_benchmarks_should_target_native_tg_binary(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_cmd", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"])

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["search", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_fallback_to_cli_launcher_when_native_binary_is_missing(monkeypatch):
    module = _load_script_module(
        "run_benchmarks_script_launcher_cmd", "benchmarks/run_benchmarks.py"
    )
    missing_binary = Path("missing-tg.exe")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: missing_binary)
    monkeypatch.setattr(
        module,
        "resolve_tg_cli_launcher",
        lambda *_args, **_kwargs: ["python", "-m", "tensor_grep"],
    )

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"])

    assert cmd == ["python", "-m", "tensor_grep", "search", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_force_cpu_when_native_flag_is_enabled(monkeypatch, tmp_path):
    module = _load_script_module("run_benchmarks_script_native_cmd", "benchmarks/run_benchmarks.py")
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(
        module, "resolve_tg_cli_launcher", lambda *_args, **_kwargs: [str(tg_binary)]
    )

    cmd = module.build_tg_benchmark_cmd(["ERROR", "bench_data"], force_cpu=True)

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["search", "--cpu", "--no-ignore", "ERROR", "bench_data"]


def test_run_benchmarks_should_include_large_file_and_many_file_scenarios(tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_native_scenarios", "benchmarks/run_benchmarks.py"
    )
    tg_binary = tmp_path / "native-tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    scenarios = module.build_benchmark_scenarios(
        bench_dir=Path("bench_data"),
        large_file_path=Path("large_fixture.log"),
        many_file_dir=Path("many_files"),
        force_cpu=True,
        binary=tg_binary,
    )

    names = [scenario["name"] for scenario in scenarios]
    assert "11. Native Large File Search" in names
    assert "12. Native Many-File Search" in names

    large_scenario = next(s for s in scenarios if s["name"] == "11. Native Large File Search")
    many_scenario = next(s for s in scenarios if s["name"] == "12. Native Many-File Search")
    assert large_scenario["tg_cmd"][:3] == [str(tg_binary), "search", "--no-ignore"]
    assert large_scenario["tg_cmd"][-1] == "large_fixture.log"
    assert many_scenario["tg_cmd"][:3] == [str(tg_binary), "search", "--no-ignore"]
    assert many_scenario["tg_cmd"][-1] == "many_files"


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

    timing_samples = iter([
        9.9,
        8.8,
        0.40,
        0.20,
        0.30,
        0.80,
        0.60,
        0.70,
    ])
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
    assert payload["artifact"] == "bench_run_benchmarks"
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
    assert payload["suite"] == "run_hot_query_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["no_regressions"] is True
    assert payload["rows"][0]["status"] == "PASS"
    assert payload["rows"][1]["status"] == "PASS"


def test_run_native_cpu_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script", "benchmarks/run_native_cpu_benchmarks.py"
    )
    monkeypatch.delenv("TENSOR_GREP_NATIVE_CPU_BENCH_DATA_DIR", raising=False)

    path = module.resolve_native_cpu_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "native_cpu_bench_data")


def test_run_native_cpu_benchmarks_should_force_native_cpu_commands(tmp_path):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script_cpu_commands",
        "benchmarks/run_native_cpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    target = tmp_path / "fixture.log"

    search_cmd = module.build_tg_cpu_search_command(tg_binary, "ERROR", target)
    count_cmd = module.build_tg_cpu_count_command(tg_binary, "ERROR", target)

    assert search_cmd[:4] == [str(tg_binary), "search", "--cpu", "--no-ignore"]
    assert count_cmd[:5] == [str(tg_binary), "search", "--cpu", "--no-ignore", "-c"]


def test_run_native_cpu_benchmarks_should_report_threshold_statuses(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_native_cpu_benchmarks_script_status", "benchmarks/run_native_cpu_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    output_path = tmp_path / "bench_native_cpu.json"

    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: tmp_path / "bench_data")
    monkeypatch.setattr(module, "generate_test_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "ensure_large_file_fixture",
        lambda *_args, **_kwargs: {
            "path": tmp_path / "large_fixture.log",
            "actual_bytes": 200 * 1024 * 1024,
        },
    )
    monkeypatch.setattr(
        module,
        "ensure_many_file_fixture",
        lambda *_args, **_kwargs: {"path": tmp_path / "many_files", "file_count": 1200},
    )

    benchmark_rows = iter([
        {
            "name": "cold_standard_corpus",
            "target": str(tmp_path / "bench_data"),
            "pattern": "ERROR",
            "rg_time_s": 1.0,
            "tg_time_s": 1.04,
            "rg_samples_s": [1.0, 0.98, 1.04],
            "tg_samples_s": [1.04, 1.01, 1.06],
            "ratio_vs_rg": 1.04,
            "threshold_ratio": 1.05,
            "status": "PASS",
            "counts_match": True,
        },
        {
            "name": "large_file_200mb",
            "target": str(tmp_path / "large_fixture.log"),
            "pattern": "ERROR native cpu benchmark sentinel",
            "rg_time_s": 1.0,
            "tg_time_s": 1.12,
            "rg_samples_s": [1.0, 1.01, 0.99],
            "tg_samples_s": [1.12, 1.14, 1.11],
            "ratio_vs_rg": 1.12,
            "threshold_ratio": 1.15,
            "require_tg_faster": False,
            "status": "PASS",
            "counts_match": True,
        },
        {
            "name": "large_file_200mb_count",
            "target": str(tmp_path / "large_fixture.log"),
            "pattern": "ERROR native cpu benchmark sentinel",
            "rg_time_s": 1.0,
            "tg_time_s": 0.92,
            "rg_samples_s": [1.0, 1.01, 0.99],
            "tg_samples_s": [0.92, 0.94, 0.91],
            "ratio_vs_rg": 0.92,
            "threshold_ratio": 1.0,
            "require_tg_faster": True,
            "status": "PASS",
            "counts_match": True,
        },
        {
            "name": "many_file_directory",
            "target": str(tmp_path / "many_files"),
            "pattern": "ERROR native cpu benchmark sentinel",
            "rg_time_s": 1.0,
            "tg_time_s": 1.03,
            "rg_samples_s": [1.0, 1.01, 0.99],
            "tg_samples_s": [1.03, 1.02, 1.04],
            "ratio_vs_rg": 1.03,
            "threshold_ratio": 1.05,
            "status": "PASS",
            "counts_match": True,
        },
    ])
    monkeypatch.setattr(
        module, "run_native_cpu_benchmark_case", lambda **_kwargs: next(benchmark_rows)
    )
    monkeypatch.setattr(
        "sys.argv",
        ["run_native_cpu_benchmarks.py", "--output", str(output_path)],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_run_native_cpu_benchmarks"
    assert payload["suite"] == "run_native_cpu_benchmarks"
    assert payload["passed"] is True
    assert [row["name"] for row in payload["rows"]] == [
        "cold_standard_corpus",
        "large_file_200mb",
        "large_file_200mb_count",
        "many_file_directory",
    ]
    assert [row["status"] for row in payload["rows"]] == ["PASS", "PASS", "PASS", "PASS"]
    assert payload["rows"][0]["ratio_vs_rg"] == 1.04
    assert payload["rows"][1]["ratio_vs_rg"] == 1.12
    assert payload["rows"][2]["ratio_vs_rg"] == 0.92
    assert payload["thresholds"] == {
        "cold_standard_corpus_max_ratio_vs_rg": 1.05,
        "large_file_200mb_max_ratio_vs_rg": 1.15,
        "large_file_200mb_count_requires_tg_faster": True,
        "many_file_directory_max_ratio_vs_rg": 1.05,
    }


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


def test_run_ast_multilang_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_ast_multilang_benchmarks_script",
        "benchmarks/run_ast_multilang_benchmarks.py",
    )
    monkeypatch.delenv("TENSOR_GREP_AST_MULTILANG_BENCH_DIR", raising=False)

    path = module.resolve_ast_multilang_bench_dir()

    assert path.parts[-2:] == ("artifacts", "bench_ast_multilang")


def test_run_ast_multilang_benchmarks_should_emit_four_language_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_multilang_benchmarks_rows",
        "benchmarks/run_ast_multilang_benchmarks.py",
    )
    output_path = tmp_path / "bench_ast_multilang.json"
    tg_binary = tmp_path / "tg.exe"
    sg_binary = tmp_path / "sg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    for path in (tg_binary, sg_binary, hyperfine_binary):
        path.write_text("binary", encoding="utf-8")

    medians_by_lang = {
        "python": (0.9, 0.4),
        "javascript": (0.8, 0.5),
        "typescript": (0.85, 0.5),
        "rust": (0.75, 0.45),
    }

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_ast_multilang_benchmarks.py",
            "--output",
            str(output_path),
            "--runs",
            "10",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: sg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)
    monkeypatch.setattr(
        module, "resolve_ast_multilang_bench_dir", lambda: tmp_path / "bench_ast_multilang"
    )
    monkeypatch.setattr(
        module,
        "ensure_multilang_ast_bench_corpus",
        lambda output_dir, *, lang, file_count, total_loc, seed: {
            "corpus_dir": output_dir,
            "manifest_path": tmp_path / f"{lang}.manifest.sha256",
            "file_count": file_count,
            "total_loc": total_loc,
            "seed": seed,
            "lang": lang,
        },
    )

    def _fake_run_hyperfine(_hyperfine_path, *, commands, runs, warmup):
        assert runs == 10
        assert warmup == 0
        command_blob = " ".join(commands)
        for lang, (tg_median, sg_median) in medians_by_lang.items():
            if f"--lang {lang}" in command_blob:
                return {
                    "results": [
                        {"command": commands[0], "median": tg_median},
                        {"command": commands[1], "median": sg_median},
                    ]
                }
        raise AssertionError(f"unexpected commands: {commands}")

    monkeypatch.setattr(module, "run_hyperfine", _fake_run_hyperfine)

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_ast_multilang"
    assert payload["suite"] == "run_ast_multilang_benchmarks"
    assert payload["thresholds"]["python_max_ratio"] == 1.1
    assert payload["python_ratio_gate_passed"] is False
    assert payload["passed"] is False
    assert [row["language"] for row in payload["rows"]] == [
        "python",
        "javascript",
        "typescript",
        "rust",
    ]
    assert payload["rows"][0]["tg_median_s"] == 0.9
    assert payload["rows"][0]["sg_median_s"] == 0.4
    assert payload["rows"][0]["ratio"] == 2.25
    assert all("file_count" in row for row in payload["rows"])


def test_run_ast_multilang_benchmarks_should_emit_json_artifact_when_ast_grep_is_missing(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_ast_multilang_benchmarks_missing_ast",
        "benchmarks/run_ast_multilang_benchmarks.py",
    )
    output_path = tmp_path / "bench_ast_multilang.json"
    tg_binary = tmp_path / "tg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    hyperfine_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        ["run_ast_multilang_benchmarks.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_ast_grep_binary", lambda: None)
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)

    exit_code = module.main()

    assert exit_code == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_ast_multilang"
    assert payload["passed"] is False
    assert "ast-grep binary not found" in payload["error"]


def test_run_ast_rewrite_benchmarks_should_require_at_least_five_rewrites_per_file(tmp_path):
    module = _load_script_module(
        "run_ast_rewrite_benchmarks_validation",
        "benchmarks/run_ast_rewrite_benchmarks.py",
    )

    with pytest.raises(ValueError, match="at least 5 matchable patterns per file"):
        module.ensure_rewrite_bench_corpus(
            tmp_path / "bench_ast_rewrite", file_count=100, total_loc=499, seed=42
        )


def test_run_ast_rewrite_benchmarks_should_emit_phase_timings_and_total_rewrites(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_ast_rewrite_benchmarks_rows",
        "benchmarks/run_ast_rewrite_benchmarks.py",
    )
    output_path = tmp_path / "bench_ast_rewrite.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_ast_rewrite_benchmarks.py",
            "--output",
            str(output_path),
            "--files",
            "5000",
            "--loc",
            "250000",
            "--runs",
            "2",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module, "resolve_ast_rewrite_bench_dir", lambda: tmp_path / "bench_ast_rewrite"
    )
    monkeypatch.setattr(
        module,
        "ensure_rewrite_bench_corpus",
        lambda output_dir, *, file_count, total_loc, seed: {
            "corpus_dir": output_dir,
            "manifest_path": tmp_path / "bench_ast_rewrite.manifest.sha256",
            "file_count": file_count,
            "total_loc": total_loc,
            "seed": seed,
            "min_rewrites_per_file": total_loc // file_count,
        },
    )
    monkeypatch.setattr(
        module,
        "run_rewrite_benchmark",
        lambda **_kwargs: {
            "pattern": module.DEFAULT_PATTERN,
            "replacement": module.DEFAULT_REPLACEMENT,
            "runs": 2,
            "total_rewrites": 250000,
            "phase_timings_s": {
                "plan": {"median": 0.75, "samples": [0.8, 0.75]},
                "diff": {"median": 1.2, "samples": [1.25, 1.2]},
                "apply": {"median": 0.95, "samples": [1.0, 0.95]},
            },
            "sg_apply": {"median": 1.05, "samples": [1.1, 1.05]},
            "ratio_tg_vs_sg": 0.905,
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_ast_rewrite"
    assert payload["suite"] == "run_ast_rewrite_benchmarks"
    assert payload["thresholds"]["max_ratio_tg_vs_sg"] == 1.1
    assert payload["file_count"] == 5000
    assert payload["total_loc"] == 250000
    assert payload["total_rewrites"] == 250000
    assert payload["min_rewrites_per_file"] >= 5
    assert payload["phase_timings_s"]["plan"]["median"] == 0.75
    assert payload["phase_timings_s"]["diff"]["median"] == 1.2
    assert payload["phase_timings_s"]["apply"]["median"] == 0.95
    assert payload["passed"] is True


def test_run_ast_rewrite_benchmarks_should_fail_gate_when_sg_is_faster(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_rewrite_benchmarks_ratio_gate",
        "benchmarks/run_ast_rewrite_benchmarks.py",
    )
    output_path = tmp_path / "bench_ast_rewrite.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_ast_rewrite_benchmarks.py",
            "--output",
            str(output_path),
            "--runs",
            "2",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module, "resolve_ast_rewrite_bench_dir", lambda: tmp_path / "bench_ast_rewrite"
    )
    monkeypatch.setattr(
        module,
        "ensure_rewrite_bench_corpus",
        lambda output_dir, *, file_count, total_loc, seed: {
            "corpus_dir": output_dir,
            "manifest_path": tmp_path / "bench_ast_rewrite.manifest.sha256",
            "file_count": file_count,
            "total_loc": total_loc,
            "seed": seed,
            "min_rewrites_per_file": total_loc // file_count,
        },
    )
    monkeypatch.setattr(
        module,
        "run_rewrite_benchmark",
        lambda **_kwargs: {
            "pattern": module.DEFAULT_PATTERN,
            "replacement": module.DEFAULT_REPLACEMENT,
            "runs": 2,
            "total_rewrites": 50000,
            "phase_timings_s": {
                "plan": {"median": 0.5, "samples": [0.5, 0.51]},
                "diff": {"median": 0.6, "samples": [0.6, 0.61]},
                "apply": {"median": 1.21, "samples": [1.2, 1.21]},
            },
            "sg_apply": {"median": 1.0, "samples": [1.0, 1.01]},
            "ratio_tg_vs_sg": 1.21,
        },
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["thresholds"]["max_ratio_tg_vs_sg"] == 1.1
    assert payload["passed"] is False
    assert payload["ratio_gate_passed"] is False


def test_run_harness_loop_iteration_should_require_zero_remaining_matches(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_harness_loop_benchmark_iteration",
        "benchmarks/run_harness_loop_benchmark.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    responses = iter([
        (0.11, {"total_matches": 3, "matches": [{"file": "a.py", "line": 1, "text": "match"}]}),
        (0.22, {"total_edits": 3, "edits": [{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}]}),
        (0.33, {"plan": {"total_edits": 3}, "verification": None}),
        (0.14, {"total_matches": 0, "matches": []}),
    ])
    commands: list[list[str]] = []

    def _fake_run_json_command(command):
        commands.append(command)
        return next(responses)

    monkeypatch.setattr(module, "run_json_command", _fake_run_json_command)

    row = module.run_harness_loop_iteration(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        iteration_index=1,
        pattern=module.DEFAULT_PATTERN,
        replacement=module.DEFAULT_REPLACEMENT,
    )

    assert [command[1] for command in commands] == ["run", "run", "run", "run"]
    assert any("--rewrite" in command for command in commands[1:3])
    assert "--apply" in commands[2]
    assert row == {
        "iteration": 1,
        "search_s": 0.11,
        "plan_s": 0.22,
        "apply_s": 0.33,
        "verify_s": 0.14,
        "initial_matches": 3,
        "planned_edits": 3,
        "applied_edits": 3,
        "remaining_matches": 0,
        "passed": True,
    }


def test_run_harness_loop_benchmark_should_emit_iteration_breakdown(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_harness_loop_benchmark_rows",
        "benchmarks/run_harness_loop_benchmark.py",
    )
    output_path = tmp_path / "bench_harness_loop.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_harness_loop_benchmark.py",
            "--output",
            str(output_path),
            "--iterations",
            "5",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(
        module, "resolve_harness_loop_bench_dir", lambda: tmp_path / "bench_harness_loop"
    )
    monkeypatch.setattr(
        module,
        "ensure_harness_loop_bench_corpus",
        lambda output_dir, *, file_count, total_loc, seed: {
            "corpus_dir": output_dir,
            "manifest_path": tmp_path / "bench_harness_loop.manifest.sha256",
            "file_count": file_count,
            "total_loc": total_loc,
            "seed": seed,
        },
    )
    monkeypatch.setattr(
        module,
        "run_harness_loop_benchmark",
        lambda **_kwargs: {
            "iterations": 5,
            "all_passed": True,
            "rows": [
                {
                    "iteration": 1,
                    "search_s": 0.1,
                    "plan_s": 0.2,
                    "apply_s": 0.3,
                    "verify_s": 0.4,
                    "initial_matches": 10,
                    "planned_edits": 10,
                    "applied_edits": 10,
                    "remaining_matches": 0,
                    "passed": True,
                }
            ],
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_harness_loop"
    assert payload["suite"] == "run_harness_loop_benchmark"
    assert payload["iterations"] == 5
    assert payload["all_passed"] is True
    assert payload["passed"] is True
    assert payload["rows"][0]["verify_s"] == 0.4
    assert payload["rows"][0]["remaining_matches"] == 0


def test_run_index_scaling_benchmark_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_index_scaling_benchmark_script",
        "benchmarks/run_index_scaling_benchmark.py",
    )
    monkeypatch.delenv("TENSOR_GREP_INDEX_SCALING_BENCH_DIR", raising=False)

    path = module.resolve_index_scaling_bench_dir()

    assert path.parts[-2:] == ("artifacts", "bench_index_scaling")


def test_run_index_scaling_benchmark_should_emit_scale_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_index_scaling_benchmark_rows",
        "benchmarks/run_index_scaling_benchmark.py",
    )
    output_path = tmp_path / "bench_index_scaling.json"
    tg_binary = tmp_path / "tg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    hyperfine_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_index_scaling_benchmark.py",
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)
    monkeypatch.setattr(
        module, "resolve_index_scaling_bench_dir", lambda: tmp_path / "bench_index_scaling"
    )
    monkeypatch.setattr(
        module,
        "run_index_scaling_benchmark",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "bench_index_scaling"),
            "rows": [
                {
                    "name": "index_scale_1000_files",
                    "file_count": 1000,
                    "build_time_s": 1.2,
                    "build_within_threshold": True,
                    "index_size_bytes": 4096,
                    "query_median_s": 0.04,
                    "query_correct": True,
                    "queries": [
                        {"pattern": "ERROR timeout", "median_s": 0.03, "matches": 1000},
                        {"pattern": "WARN retry budget", "median_s": 0.04, "matches": 1000},
                        {"pattern": "trace_id=", "median_s": 0.05, "matches": 1000},
                    ],
                },
                {
                    "name": "index_scale_5000_files",
                    "file_count": 5000,
                    "build_time_s": 4.8,
                    "build_within_threshold": True,
                    "index_size_bytes": 16384,
                    "query_median_s": 0.07,
                    "query_correct": True,
                    "queries": [
                        {"pattern": "ERROR timeout", "median_s": 0.06, "matches": 5000},
                        {"pattern": "WARN retry budget", "median_s": 0.07, "matches": 5000},
                        {"pattern": "trace_id=", "median_s": 0.08, "matches": 5000},
                    ],
                },
                {
                    "name": "index_scale_10000_files",
                    "file_count": 10000,
                    "build_time_s": 9.5,
                    "build_within_threshold": True,
                    "index_size_bytes": 32768,
                    "query_median_s": 0.12,
                    "query_correct": True,
                    "queries": [
                        {"pattern": "ERROR timeout", "median_s": 0.1, "matches": 10000},
                        {"pattern": "WARN retry budget", "median_s": 0.12, "matches": 10000},
                        {"pattern": "trace_id=", "median_s": 0.14, "matches": 10000},
                    ],
                },
            ],
            "passed": True,
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_index_scaling"
    assert payload["suite"] == "run_index_scaling_benchmark"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["passed"] is True
    assert [row["file_count"] for row in payload["rows"]] == [1000, 5000, 10000]
    assert all(row["build_time_s"] > 0 for row in payload["rows"])
    assert all(row["index_size_bytes"] > 0 for row in payload["rows"])
    assert all(row["query_median_s"] > 0 for row in payload["rows"])
    assert all(len(row["queries"]) == 3 for row in payload["rows"])


def test_benchmark_scale_should_record_plain_search_parity(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_index_scaling_benchmark_parity",
        "benchmarks/run_index_scaling_benchmark.py",
    )
    tg_binary = tmp_path / "tg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    corpus_dir = tmp_path / "scale_10000"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / ".tg_index").write_text("index", encoding="utf-8")
    tg_binary.write_text("binary", encoding="utf-8")
    hyperfine_binary.write_text("binary", encoding="utf-8")

    corpus_info = {
        "corpus_dir": corpus_dir,
        "manifest_path": tmp_path / "scale_10000.manifest.sha256",
        "file_count": 10000,
        "lines_per_file": 12,
        "total_lines": 120000,
    }

    def _fake_run_hyperfine(_hyperfine_path, *, commands, runs, warmup, prepare=None):
        assert runs == 3
        assert warmup == 1
        if len(commands) == 1:
            assert prepare is not None
            return {"results": [{"median": 1.25}]}
        return {
            "results": [
                {"median": 0.031},
                {"median": 0.041},
                {"median": 0.051},
            ]
        }

    def _fake_run_count(command):
        rendered = " ".join(str(part) for part in command)
        is_indexed = "--index" in command
        if "ERROR timeout" in rendered:
            return 30000
        if "WARN retry budget" in rendered:
            return 29999 if is_indexed else 30000
        if "trace_id=" in rendered:
            return 120000
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(module, "run_hyperfine_benchmark", _fake_run_hyperfine)
    monkeypatch.setattr(module, "run_count_command", _fake_run_count)

    row = module.benchmark_scale(
        tg_binary=tg_binary,
        hyperfine_binary=hyperfine_binary,
        corpus_info=corpus_info,
        query_patterns=("ERROR timeout", "WARN retry budget", "trace_id="),
        runs=3,
        warmup=1,
    )

    assert row["queries"][0]["plain_matches"] == 30000
    assert row["queries"][0]["counts_match"] is True
    assert row["queries"][1]["plain_matches"] == 30000
    assert row["queries"][1]["counts_match"] is False
    assert row["query_correct"] is False


def test_run_index_scaling_benchmark_should_fail_when_10k_build_exceeds_threshold(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_index_scaling_benchmark_threshold",
        "benchmarks/run_index_scaling_benchmark.py",
    )

    monkeypatch.setattr(
        module,
        "generate_index_scaling_corpus",
        lambda output_dir, *, file_count, lines_per_file, seed: {
            "corpus_dir": output_dir,
            "manifest_path": output_dir.parent / f"{output_dir.name}.manifest.sha256",
            "file_count": file_count,
            "lines_per_file": lines_per_file,
            "total_lines": file_count * lines_per_file,
            "seed": seed,
        },
    )

    rows = iter([
        {
            "name": "index_scale_1000_files",
            "file_count": 1000,
            "build_time_s": 1.0,
            "index_size_bytes": 1024,
            "query_median_s": 0.01,
            "query_correct": True,
            "build_within_threshold": True,
            "queries": [{"pattern": "ERROR timeout"}] * 3,
        },
        {
            "name": "index_scale_5000_files",
            "file_count": 5000,
            "build_time_s": 5.0,
            "index_size_bytes": 4096,
            "query_median_s": 0.03,
            "query_correct": True,
            "build_within_threshold": True,
            "queries": [{"pattern": "ERROR timeout"}] * 3,
        },
        {
            "name": "index_scale_10000_files",
            "file_count": 10000,
            "build_time_s": 61.0,
            "index_size_bytes": 8192,
            "query_median_s": 0.05,
            "query_correct": True,
            "build_within_threshold": False,
            "queries": [{"pattern": "ERROR timeout"}] * 3,
        },
    ])
    monkeypatch.setattr(module, "benchmark_scale", lambda **_kwargs: next(rows))

    result = module.run_index_scaling_benchmark(
        tg_binary=tmp_path / "tg.exe",
        hyperfine_binary=tmp_path / "hyperfine.exe",
        bench_dir=tmp_path / "bench_index_scaling",
        scales=(1000, 5000, 10000),
        lines_per_file=12,
        seed=42,
        query_patterns=("ERROR timeout", "WARN retry budget", "trace_id="),
        runs=3,
        warmup=1,
    )

    assert result["rows"][-1]["build_within_threshold"] is False
    assert result["passed"] is False


def test_run_index_scaling_benchmark_should_require_at_least_one_10k_scale(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_index_scaling_benchmark_requires_10k",
        "benchmarks/run_index_scaling_benchmark.py",
    )
    output_path = tmp_path / "bench_index_scaling.json"
    tg_binary = tmp_path / "tg.exe"
    hyperfine_binary = tmp_path / "hyperfine.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    hyperfine_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_index_scaling_benchmark.py",
            "--output",
            str(output_path),
            "--scales",
            "1000,5000,9000",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_hyperfine_binary", lambda: hyperfine_binary)
    monkeypatch.setattr(
        module,
        "run_index_scaling_benchmark",
        lambda **_kwargs: pytest.fail("benchmark should not run without a 10k+ scale"),
    )

    exit_code = module.main()

    assert exit_code == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert "10000" in payload["error"]


@pytest.mark.parametrize("rel_path", BENCHMARK_JSON_SCRIPTS)
def test_benchmark_scripts_should_declare_suite_and_generated_at_epoch_s(rel_path: str):
    root = Path(__file__).resolve().parents[2]
    source = (root / rel_path).read_text(encoding="utf-8")

    assert '"suite"' in source
    assert '"generated_at_epoch_s"' in source


def test_run_ast_benchmarks_should_target_native_tg_binary(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_benchmarks_script_cmd", "benchmarks/run_ast_benchmarks.py"
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd = module.build_tg_ast_benchmark_cmd([
        "run",
        "--lang",
        "python",
        "pattern",
        "bench_ast_data",
    ])

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["run", "--lang", "python", "pattern", "bench_ast_data"]


def test_run_ast_benchmarks_should_default_to_ten_percent_ratio_gate(monkeypatch):
    module = _load_script_module(
        "run_ast_benchmarks_script_gate", "benchmarks/run_ast_benchmarks.py"
    )
    monkeypatch.setattr("sys.argv", ["run_ast_benchmarks.py"])

    args = module.parse_args()

    assert args.max_ratio == 1.1


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


def test_run_ast_workflow_benchmarks_should_target_native_tg_binary_for_run(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_cmd",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)

    cmd = module.build_tg_ast_workflow_cmd(["run", "--lang", "python", "pattern", "."])

    assert cmd[0] == str(tg_binary)
    assert cmd[1:] == ["run", "--lang", "python", "pattern", "."]


def test_run_ast_workflow_benchmarks_should_use_sidecar_for_scan_test():
    module = _load_script_module(
        "run_ast_workflow_benchmarks_script_sidecar",
        "benchmarks/run_ast_workflow_benchmarks.py",
    )

    cmd = module.build_sidecar_ast_workflow_cmd(["scan", "--config", "sgconfig.yml"])

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
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["run_ast_workflow_benchmarks.py"])
    monkeypatch.setattr(module, "resolve_tg_binary", lambda *_args, **_kwargs: tg_binary)
    monkeypatch.setattr(module, "resolve_ast_workflow_bench_dir", lambda: tmp_path / "bench")

    def _fake_run_cmd_capture(cmd, cwd):
        # Native binary: [tg.exe, run, ...]
        # Sidecar: [python, -m, tensor_grep.cli.bootstrap, scan/test, ...]
        for token in cmd:
            if token == "run":
                return 0.15, 0
            if token == "scan":
                return 0.25, 0
            if token == "test":
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
    assert payload["artifact"] == "bench_run_ast_workflow_benchmarks"
    assert payload["suite"] == "run_ast_workflow_benchmarks"
    rows = payload["rows"]
    assert len(rows) == 3
    assert rows[0]["name"] == "ast_run_workflow"
    assert rows[0]["backend"] == "native"
    assert rows[0]["tg_time_s"] == 0.15
    assert rows[1]["name"] == "ast_scan_workflow"
    assert rows[1]["backend"] == "sidecar"
    assert rows[1]["tg_time_s"] == 0.25
    assert rows[2]["name"] == "ast_test_workflow"
    assert rows[2]["backend"] == "sidecar"
    assert rows[2]["tg_time_s"] == 0.4


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


def test_run_gpu_benchmarks_should_parse_corpus_sizes_with_units():
    module = _load_script_module(
        "run_gpu_benchmarks_script_sizes", "benchmarks/run_gpu_benchmarks.py"
    )

    sizes = module.parse_corpus_sizes("1MB, 10MB,100MB,1GB")

    assert sizes == (1024 * 1024, 10 * 1024 * 1024, 100 * 1024 * 1024, 1024 * 1024 * 1024)


def test_run_gpu_benchmarks_should_emit_scale_rows_and_correctness(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_benchmarks_script_rows", "benchmarks/run_gpu_benchmarks.py"
    )
    output_path = tmp_path / "bench_gpu_scale.json"
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_benchmarks.py",
            "--output",
            str(output_path),
            "--corpus-sizes",
            "1MB,10MB,100MB,1GB",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_gpu_sidecar_python", lambda raw=None: sidecar_python)
    monkeypatch.setattr(module, "resolve_gpu_bench_data_dir", lambda: tmp_path / "gpu_bench_data")
    monkeypatch.setattr(
        module,
        "run_gpu_scale_benchmarks",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "gpu_bench_data"),
            "corpus_sizes": [
                {"label": "1MB", "bytes": 1024 * 1024},
                {"label": "10MB", "bytes": 10 * 1024 * 1024},
                {"label": "100MB", "bytes": 100 * 1024 * 1024},
                {"label": "1GB", "bytes": 1024 * 1024 * 1024},
            ],
            "devices": [
                {"device_id": 0, "name": "NVIDIA GeForce RTX 4070", "operational": True},
                {
                    "device_id": 1,
                    "name": "NVIDIA GeForce RTX 5070",
                    "operational": False,
                    "error": "no kernel image is available for execution on the device",
                },
            ],
            "rows": [
                {
                    "size_label": "1MB",
                    "size_bytes": 1024 * 1024,
                    "actual_bytes": 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 0.01},
                    "tg_cpu": {"status": "PASS", "median_s": 0.02},
                    "gpu": [
                        {"device_id": 0, "status": "PASS", "median_s": 0.5},
                        {"device_id": 1, "status": "UNSUPPORTED", "median_s": None},
                    ],
                },
                {
                    "size_label": "10MB",
                    "size_bytes": 10 * 1024 * 1024,
                    "actual_bytes": 10 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 0.09},
                    "tg_cpu": {"status": "PASS", "median_s": 0.11},
                    "gpu": [
                        {"device_id": 0, "status": "PASS", "median_s": 0.42},
                        {"device_id": 1, "status": "UNSUPPORTED", "median_s": None},
                    ],
                },
                {
                    "size_label": "100MB",
                    "size_bytes": 100 * 1024 * 1024,
                    "actual_bytes": 100 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 0.8},
                    "tg_cpu": {"status": "PASS", "median_s": 0.91},
                    "gpu": [
                        {"device_id": 0, "status": "PASS", "median_s": 1.2},
                        {"device_id": 1, "status": "UNSUPPORTED", "median_s": None},
                    ],
                },
                {
                    "size_label": "1GB",
                    "size_bytes": 1024 * 1024 * 1024,
                    "actual_bytes": 1024 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 8.2},
                    "tg_cpu": {"status": "PASS", "median_s": 8.6},
                    "gpu": [
                        {"device_id": 0, "status": "PASS", "median_s": 8.0},
                        {"device_id": 1, "status": "UNSUPPORTED", "median_s": None},
                    ],
                },
            ],
            "correctness_checks": [
                {
                    "device_id": 0,
                    "pattern": "Database connection timeout",
                    "matches_equal": True,
                    "files_equal": True,
                },
                {
                    "device_id": 0,
                    "pattern": "WARN retry budget exhausted",
                    "matches_equal": True,
                    "files_equal": True,
                },
                {
                    "device_id": 0,
                    "pattern": "trace_id=",
                    "matches_equal": True,
                    "files_equal": True,
                },
            ],
            "gpu_auto_recommendation": {
                "should_add_flag": False,
                "reason": "No device beat rg by 20% at any measured scale.",
            },
            "warnings": [
                "RTX 5070 is present but unsupported by the current CUDA-enabled PyTorch build.",
            ],
            "errors": [],
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["suite"] == "run_gpu_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert [entry["label"] for entry in payload["corpus_sizes"]] == ["1MB", "10MB", "100MB", "1GB"]
    assert len(payload["rows"]) == 4
    assert all("gpu" in row for row in payload["rows"])
    assert len(payload["correctness_checks"]) == 3
    assert payload["gpu_auto_recommendation"]["should_add_flag"] is False
    assert payload["warnings"]


def test_run_gpu_native_benchmarks_should_default_data_dir_to_artifacts(monkeypatch):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script", "benchmarks/run_gpu_native_benchmarks.py"
    )
    monkeypatch.delenv("TENSOR_GREP_GPU_NATIVE_BENCH_DATA_DIR", raising=False)

    path = module.resolve_gpu_native_bench_data_dir()

    assert path.parts[-2:] == ("artifacts", "gpu_native_bench_data")


def test_run_gpu_native_benchmarks_should_emit_rows_correctness_and_error_tests(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_rows", "benchmarks/run_gpu_native_benchmarks.py"
    )
    output_path = tmp_path / "bench_gpu_native.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_native_benchmarks.py",
            "--output",
            str(output_path),
            "--corpus-sizes",
            "10MB,100MB,500MB,1GB",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "run_gpu_native_benchmarks",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "gpu_native_bench_data"),
            "corpus_sizes": [
                {"label": "10MB", "bytes": 10 * 1024 * 1024},
                {"label": "100MB", "bytes": 100 * 1024 * 1024},
                {"label": "500MB", "bytes": 500 * 1024 * 1024},
                {"label": "1GB", "bytes": 1024 * 1024 * 1024},
            ],
            "rows": [
                {
                    "size_label": "10MB",
                    "size_bytes": 10 * 1024 * 1024,
                    "actual_bytes": 10 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 0.05, "throughput_bytes_s": 209715200.0},
                    "tg_cpu": {
                        "status": "PASS",
                        "median_s": 0.08,
                        "throughput_bytes_s": 131072000.0,
                    },
                    "tg_gpu": {
                        "status": "PASS",
                        "median_s": 0.12,
                        "throughput_bytes_s": 87381333.33,
                        "ratio_vs_rg": 2.4,
                    },
                },
                {
                    "size_label": "100MB",
                    "size_bytes": 100 * 1024 * 1024,
                    "actual_bytes": 100 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 0.6, "throughput_bytes_s": 174762666.67},
                    "tg_cpu": {
                        "status": "PASS",
                        "median_s": 0.7,
                        "throughput_bytes_s": 149796571.43,
                    },
                    "tg_gpu": {
                        "status": "PASS",
                        "median_s": 0.55,
                        "throughput_bytes_s": 190650181.82,
                        "ratio_vs_rg": 0.9167,
                    },
                },
                {
                    "size_label": "500MB",
                    "size_bytes": 500 * 1024 * 1024,
                    "actual_bytes": 500 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 3.6, "throughput_bytes_s": 145635555.56},
                    "tg_cpu": {
                        "status": "PASS",
                        "median_s": 3.1,
                        "throughput_bytes_s": 169125161.29,
                    },
                    "tg_gpu": {
                        "status": "PASS",
                        "median_s": 2.2,
                        "throughput_bytes_s": 238312727.27,
                        "ratio_vs_rg": 0.6111,
                    },
                },
                {
                    "size_label": "1GB",
                    "size_bytes": 1024 * 1024 * 1024,
                    "actual_bytes": 1024 * 1024 * 1024,
                    "rg": {"status": "PASS", "median_s": 7.4, "throughput_bytes_s": 145104516.76},
                    "tg_cpu": {
                        "status": "PASS",
                        "median_s": 6.9,
                        "throughput_bytes_s": 155588915.48,
                    },
                    "tg_gpu": {
                        "status": "PASS",
                        "median_s": 4.9,
                        "throughput_bytes_s": 219130326.53,
                        "ratio_vs_rg": 0.6622,
                    },
                },
            ],
            "correctness_checks": [
                {
                    "size_label": "10MB",
                    "matches_equal": True,
                    "cpu_total_matches": 12,
                    "gpu_total_matches": 12,
                },
                {
                    "size_label": "100MB",
                    "matches_equal": True,
                    "cpu_total_matches": 120,
                    "gpu_total_matches": 120,
                },
                {
                    "size_label": "500MB",
                    "matches_equal": True,
                    "cpu_total_matches": 600,
                    "gpu_total_matches": 600,
                },
                {
                    "size_label": "1GB",
                    "matches_equal": True,
                    "cpu_total_matches": 1200,
                    "gpu_total_matches": 1200,
                },
            ],
            "error_tests": {
                "invalid_device": {"status": "PASS", "exit_code": 2},
                "nvrtc_failure": {"status": "PASS", "exit_code": 2},
                "timeout": {"status": "PASS", "exit_code": 2, "simulated": True},
                "malformed_inputs": {
                    "status": "PASS",
                    "exit_code": 0,
                    "cpu_total_matches": 2,
                    "gpu_total_matches": 2,
                },
            },
            "crossover": {
                "exists": True,
                "first_gpu_faster_than_rg": "100MB",
                "summary": "GPU first beats rg at 100MB.",
            },
            "warnings": [
                "Timeout coverage is currently simulation-backed via TG_TEST_CUDA_BEHAVIOR."
            ],
            "errors": [],
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["suite"] == "run_gpu_native_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert [entry["label"] for entry in payload["corpus_sizes"]] == [
        "10MB",
        "100MB",
        "500MB",
        "1GB",
    ]
    assert len(payload["rows"]) == 4
    assert payload["rows"][0]["tg_gpu"]["ratio_vs_rg"] == 2.4
    assert len(payload["correctness_checks"]) == 4
    assert payload["error_tests"]["invalid_device"]["status"] == "PASS"
    assert payload["error_tests"]["nvrtc_failure"]["status"] == "PASS"
    assert payload["error_tests"]["timeout"]["simulated"] is True
    assert payload["crossover"]["exists"] is True
    assert payload["crossover"]["first_gpu_faster_than_rg"] == "100MB"


def test_run_gpu_native_benchmarks_should_emit_advanced_sections_when_enabled(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_advanced", "benchmarks/run_gpu_native_benchmarks.py"
    )
    output_path = tmp_path / "bench_gpu_native_advanced.json"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_native_benchmarks.py",
            "--output",
            str(output_path),
            "--advanced",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")

    def _fake_run_gpu_native_benchmarks(**kwargs):
        captured.update(kwargs)
        return {
            "bench_dir": str(tmp_path / "gpu_native_bench_data"),
            "corpus_sizes": [
                {"label": "10MB", "bytes": 10 * 1024 * 1024},
                {"label": "100MB", "bytes": 100 * 1024 * 1024},
                {"label": "500MB", "bytes": 500 * 1024 * 1024},
                {"label": "1GB", "bytes": 1024 * 1024 * 1024},
            ],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "crossover": {
                "exists": True,
                "first_gpu_faster_than_rg": "500MB",
                "summary": "GPU first beats rg at 500MB.",
            },
            "throughput_target": {
                "met": True,
                "winning_rows": [{"size_label": "500MB", "speedup_vs_rg": 12.4}],
            },
            "advanced": {
                "enabled": True,
                "stream_overlap": {"status": "PASS", "benefit_pct": 18.2},
                "transfer_throughput": {
                    "status": "PASS",
                    "pinned": {"throughput_bytes_per_s": 12_500_000_000.0},
                    "pageable": {"throughput_bytes_per_s": 6_200_000_000.0},
                },
                "multi_pattern": {"status": "PASS", "speedup_vs_cpu": 2.7},
                "multi_gpu": {"status": "PASS", "improvement_pct": 18.6},
                "long_lines": {"status": "PASS", "gpu_speedup_vs_cpu": 1.4},
                "cuda_graphs": {"status": "PASS", "wall_time_reduction_pct": 11.8},
                "oom_validation": {
                    "status": "PASS",
                    "requested_bytes": 13 * 1024 * 1024 * 1024,
                    "stderr": "CUDA out of memory while allocating 13.00 GiB",
                },
            },
            "warnings": [],
            "errors": [],
        }

    monkeypatch.setattr(module, "run_gpu_native_benchmarks", _fake_run_gpu_native_benchmarks)

    exit_code = module.main()

    assert exit_code == 0
    assert captured["advanced"] is True
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["advanced"]["enabled"] is True
    assert payload["throughput_target"]["met"] is True
    assert payload["advanced"]["multi_gpu"]["improvement_pct"] == 18.6
    assert payload["advanced"]["oom_validation"]["status"] == "PASS"


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


def test_check_regression_should_use_five_percent_default_threshold(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_default_threshold", "benchmarks/check_regression.py"
    )
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.06}],
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
        json.dumps({
            **payload,
            "rows": [{"name": "repeated_fixed_string", "first_s": 1.02, "second_s": 0.43}],
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


def test_check_regression_should_resolve_auto_milestone_baseline(monkeypatch, tmp_path):
    module = _load_script_module(
        "check_regression_script_auto_milestone", "benchmarks/check_regression.py"
    )
    milestones_dir = tmp_path / "benchmarks"
    milestones_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = milestones_dir / "baseline_m1.json"
    baseline_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "milestone": "m1",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.0}],
        }),
        encoding="utf-8",
    )
    current_path = tmp_path / "current.json"
    current_path.write_text(
        json.dumps({
            "suite": "run_benchmarks",
            "milestone": "m2",
            "environment": {"platform": "windows", "machine": "amd64"},
            "rows": [{"name": "x", "tg_time_s": 1.04}],
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
