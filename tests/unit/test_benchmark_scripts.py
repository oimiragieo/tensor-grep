import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
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
    "benchmarks/run_context_render_benchmarks.py",
    "benchmarks/run_blast_radius_benchmarks.py",
    "benchmarks/run_session_benchmarks.py",
    "benchmarks/run_external_eval.py",
    "benchmarks/analyze_external_profiling.py",
    "benchmarks/normalize_competitor_eval.py",
    "benchmarks/render_patch_scorecard.py",
    "benchmarks/render_world_class_report.py",
    "benchmarks/run_patch_bakeoff.py",
    "benchmarks/run_tensor_grep_patch_driver.py",
    "benchmarks/run_gemini_patch_predictions.py",
    "benchmarks/run_copilot_patch_predictions.py",
    "benchmarks/run_claude_patch_predictions.py",
    "benchmarks/run_claude_skill_ab.py",
    "benchmarks/run_claude_skill_ab_matrix.py",
    "benchmarks/run_claude_competitor_eval.py",
    "benchmarks/run_codex_competitor_eval.py",
    "benchmarks/run_copilot_competitor_eval.py",
    "benchmarks/run_gemini_competitor_eval.py",
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
        rg_binary="rg",
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

    benchmark_rows = iter(
        [
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
        ]
    )
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

    responses = iter(
        [
            (0.11, {"total_matches": 3, "matches": [{"file": "a.py", "line": 1, "text": "match"}]}),
            (
                0.22,
                {"total_edits": 3, "edits": [{"file": "a.py"}, {"file": "b.py"}, {"file": "c.py"}]},
            ),
            (0.33, {"plan": {"total_edits": 3}, "verification": None}),
            (0.14, {"total_matches": 0, "matches": []}),
        ]
    )
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

    rows = iter(
        [
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
        ]
    )
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


def test_run_context_render_benchmarks_should_emit_fixture_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_context_render_benchmarks_rows",
        "benchmarks/run_context_render_benchmarks.py",
    )
    output_path = tmp_path / "bench_context_render.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_context_render_benchmarks.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_editor_plane_bench_dir", lambda: tmp_path / "editor_plane")
    monkeypatch.setattr(
        module,
        "ensure_editor_plane_fixture_set",
        lambda bench_dir: {
            "small": {"root": tmp_path / "small", "file_count": 12, "target_symbol": "create_invoice"},
            "medium": {"root": tmp_path / "medium", "file_count": 48, "target_symbol": "create_invoice"},
            "large": {"root": tmp_path / "large", "file_count": 128, "target_symbol": "create_invoice"},
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_context_render_fixture",
        lambda fixture, *, repeats, session_repeats: {
            "fixture": fixture["name"],
            "file_count": fixture["file_count"],
            "query": "create invoice",
            "cold_samples_s": [0.12, 0.1, 0.11],
            "cold_median_s": 0.11,
            "warm_session_samples_s": [0.03, 0.02, 0.025],
            "warm_session_median_s": 0.025,
            "session_id": f"session-{fixture['name']}",
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_context_render"
    assert payload["suite"] == "run_context_render_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert [row["fixture"] for row in payload["rows"]] == ["small", "medium", "large"]
    assert all("cold_median_s" in row for row in payload["rows"])
    assert all("warm_session_median_s" in row for row in payload["rows"])


def test_run_blast_radius_benchmarks_should_emit_depth_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_blast_radius_benchmarks_rows",
        "benchmarks/run_blast_radius_benchmarks.py",
    )
    output_path = tmp_path / "bench_blast_radius.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_blast_radius_benchmarks.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_editor_plane_bench_dir", lambda: tmp_path / "editor_plane")
    monkeypatch.setattr(
        module,
        "ensure_editor_plane_fixture_set",
        lambda bench_dir: {
            "medium": {
                "root": tmp_path / "medium",
                "file_count": 48,
                "blast_radius_symbols": [
                    {"symbol": "create_invoice", "depth": 1},
                    {"symbol": "create_invoice", "depth": 2},
                    {"symbol": "create_invoice", "depth": 3},
                ],
            }
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_blast_radius_fixture",
        lambda fixture, *, repeats: [
            {
                "fixture": fixture["name"],
                "symbol": "create_invoice",
                "graph_depth": 1,
                "samples_s": [0.02, 0.018, 0.019],
                "median_s": 0.019,
                "file_count": fixture["file_count"],
            },
            {
                "fixture": fixture["name"],
                "symbol": "create_invoice",
                "graph_depth": 2,
                "samples_s": [0.03, 0.028, 0.029],
                "median_s": 0.029,
                "file_count": fixture["file_count"],
            },
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_blast_radius"
    assert payload["suite"] == "run_blast_radius_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert [row["graph_depth"] for row in payload["rows"]] == [1, 2]


def test_run_session_benchmarks_should_emit_refresh_comparison_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_session_benchmarks_rows",
        "benchmarks/run_session_benchmarks.py",
    )
    output_path = tmp_path / "bench_session.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_session_benchmarks.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_editor_plane_bench_dir", lambda: tmp_path / "editor_plane")
    monkeypatch.setattr(
        module,
        "ensure_editor_plane_fixture_set",
        lambda bench_dir: {
            "medium": {"root": tmp_path / "medium", "file_count": 48, "target_symbol": "create_invoice"},
            "large": {"root": tmp_path / "large", "file_count": 128, "target_symbol": "create_invoice"},
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_session_fixture",
        lambda fixture, *, query_repeats: {
            "fixture": fixture["name"],
            "file_count": fixture["file_count"],
            "open_session_s": 0.14,
            "query_samples_s": [0.03, 0.025, 0.028],
            "query_median_s": 0.028,
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_incremental_refresh_comparison",
        lambda fixture, *, modified_file_counts: [
            {
                "fixture": fixture["name"],
                "modified_file_count": 1,
                "incremental_refresh_s": 0.05,
                "full_rebuild_s": 0.16,
                "ratio": 0.3125,
                "passed_ratio_gate": True,
            },
            {
                "fixture": fixture["name"],
                "modified_file_count": 5,
                "incremental_refresh_s": 0.07,
                "full_rebuild_s": 0.19,
                "ratio": 0.3684,
                "passed_ratio_gate": True,
            },
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_session"
    assert payload["suite"] == "run_session_benchmarks"
    assert payload["generated_at_epoch_s"] > 0
    assert payload["passed"] is True
    assert payload["refresh_ratio_threshold"] == 0.5
    assert len(payload["session_rows"]) == 2
    assert len(payload["refresh_rows"]) == 2


def test_run_session_benchmarks_should_fail_when_incremental_refresh_exceeds_threshold(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_session_benchmarks_ratio_gate",
        "benchmarks/run_session_benchmarks.py",
    )
    output_path = tmp_path / "bench_session.json"

    monkeypatch.setattr(
        "sys.argv",
        ["run_session_benchmarks.py", "--output", str(output_path)],
    )
    monkeypatch.setattr(module, "resolve_editor_plane_bench_dir", lambda: tmp_path / "editor_plane")
    monkeypatch.setattr(
        module,
        "ensure_editor_plane_fixture_set",
        lambda bench_dir: {
            "medium": {"root": tmp_path / "medium", "file_count": 48, "target_symbol": "create_invoice"},
            "large": {"root": tmp_path / "large", "file_count": 128, "target_symbol": "create_invoice"},
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_session_fixture",
        lambda fixture, *, query_repeats: {
            "fixture": fixture["name"],
            "file_count": fixture["file_count"],
            "open_session_s": 0.14,
            "query_samples_s": [0.03, 0.025, 0.028],
            "query_median_s": 0.028,
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_incremental_refresh_comparison",
        lambda fixture, *, modified_file_counts: [
            {
                "fixture": fixture["name"],
                "modified_file_count": 3,
                "incremental_refresh_s": 0.11,
                "full_rebuild_s": 0.20,
                "ratio": 0.55,
                "passed_ratio_gate": False,
            }
        ],
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["refresh_rows"][0]["passed_ratio_gate"] is False


def test_analyze_bakeoff_misses_should_bucket_false_positive_paths(monkeypatch, tmp_path):
    module = _load_script_module(
        "analyze_bakeoff_misses_script",
        "benchmarks/analyze_bakeoff_misses.py",
    )
    input_path = tmp_path / "bench_bakeoff.json"
    output_path = tmp_path / "bakeoff_analysis.json"
    markdown_path = tmp_path / "bakeoff_analysis.md"
    input_path.write_text(
        json.dumps(
            {
                "artifact": "bench_bakeoff",
                "summary": {
                    "scenario_count": 2,
                    "mean_file_hit_rate": 0.75,
                    "mean_file_precision": 0.5,
                },
                "rows": [
                    {
                        "name": "click:blast-radius:open_file",
                        "query_or_symbol": "open_file",
                        "expected_primary_file": "src/click/utils.py",
                        "actual_primary_file": "src/click/utils.py",
                        "false_positive_files": [
                            "repo/examples/demo.py",
                            "repo/src/click/__init__.py",
                            "repo/src/click/_compat.py",
                        ],
                        "file_hit_rate": 0.5,
                        "file_precision": 0.25,
                    },
                    {
                        "name": "click:blast-radius:UsageError",
                        "query_or_symbol": "UsageError",
                        "expected_primary_file": "src/click/exceptions.py",
                        "actual_primary_file": "src/click/exceptions.py",
                        "false_positive_files": [
                            "repo/src/click/formatting.py",
                            "repo/src/click/shell_completion.py",
                        ],
                        "file_hit_rate": 1.0,
                        "file_precision": 0.75,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "analyze_bakeoff_misses.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--markdown",
            str(markdown_path),
        ],
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bakeoff_miss_analysis"
    assert payload["scenario_count"] == 2
    assert payload["scenarios_with_false_positives"] == 2
    assert payload["bucket_counts"]["examples"] == 1
    assert payload["bucket_counts"]["package-entrypoint"] == 1
    assert payload["bucket_counts"]["compat-layer"] == 1
    assert payload["bucket_counts"]["formatting"] == 1
    assert payload["bucket_counts"]["shell-completion"] == 1
    assert payload["worst_scenarios"][0]["name"] == "click:blast-radius:open_file"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Bakeoff Miss Analysis" in markdown
    assert "package-entrypoint" in markdown


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

    cmd = module.build_tg_ast_benchmark_cmd(
        [
            "run",
            "--lang",
            "python",
            "pattern",
            "bench_ast_data",
        ]
    )

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


def test_run_external_eval_should_aggregate_manifest_packs(tmp_path):
    module = _load_script_module("run_external_eval_script", "benchmarks/run_external_eval.py")
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": [],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "manifest_path": str(tmp_path / "manifest.json"),
        "packs": [{"name": "demo", "language": "python", "scenario_pack": str(scenario_pack)}],
    }

    scenario = module.run_bakeoff.load_scenarios(scenario_pack)[0]

    def _fake_evaluate(_scenario, *, profile=False, provider="native"):
        row = {
            "actual_primary_file": "a.py",
            "actual_primary_span": {"start_line": 1, "end_line": 2},
            "actual_dependent_files": [],
            "actual_suggested_edit_files": [],
            "actual_test_files": [],
            "actual_validation_commands": [],
            "context_token_count": 12,
        }
        if profile:
            row["_profiling"] = {"total_elapsed_s": 0.1, "phases": []}
        row["semantic_provider"] = provider
        return module.run_bakeoff.score_scenario(scenario, row)

    module.run_bakeoff.evaluate_scenario = _fake_evaluate
    payload = module.build_external_eval_payload(manifest, profile=True)

    assert payload["artifact"] == "bench_external_eval"
    assert payload["pack_count"] == 1
    assert payload["summary"]["scenario_count"] == 1
    assert payload["by_language"]["python"]["scenario_count"] == 1


def test_analyze_external_profiling_should_rank_dominant_phases():
    module = _load_script_module(
        "analyze_external_profiling_script", "benchmarks/analyze_external_profiling.py"
    )
    payload = {
        "artifact": "bench_bakeoff",
        "rows": [
            {
                "_profiling": {
                    "total_elapsed_s": 1.0,
                    "phases": [
                        {"name": "repo_map_build", "elapsed_s": 0.6, "calls": 1},
                        {"name": "caller_scan", "elapsed_s": 0.4, "calls": 2},
                    ],
                }
            }
        ],
    }

    analysis = module.analyze_external_profiling(payload)

    assert analysis["artifact"] == "bench_external_profile_analysis"
    assert analysis["dominant_phases"][0]["name"] == "repo_map_build"
    assert analysis["dominant_phases"][0]["percent_total_elapsed"] == 60.0


def test_normalize_competitor_eval_should_score_manual_records(tmp_path):
    module = _load_script_module(
        "normalize_competitor_eval_script", "benchmarks/normalize_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": ["b.py"],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": ["tests/test_a.py"],
                        "expected_validation_commands_contain": ["pytest tests/test_a.py"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "scenario_packs": ["scenarios.json"],
        "records": [
            {
                "system": "tensor-grep",
                "scenario_pack": "scenarios.json",
                "scenario_id": "demo",
                "actual_primary_file": "a.py",
                "actual_primary_span": {"start_line": 1, "end_line": 2},
                "actual_dependent_files": ["b.py"],
                "actual_suggested_edit_files": [],
                "actual_test_files": ["tests/test_a.py"],
                "actual_validation_commands": ["python -m pytest tests/test_a.py -q"],
                "context_token_count": 100,
                "wall_clock_seconds": 0.25,
                "deterministic_repeat_match": True,
            }
        ],
    }

    normalized = module.normalize_competitor_eval(payload, base_dir=tmp_path)

    assert normalized["artifact"] == "competitor_eval_normalized"
    assert normalized["by_system"]["tensor-grep"]["scenario_count"] == 1
    row = normalized["records"][0]
    assert row["primary_file_hit"] == 1.0
    assert row["validation_cmd_hit"] == 1.0


def test_normalize_competitor_eval_should_normalize_windows_style_paths(tmp_path):
    module = _load_script_module(
        "normalize_competitor_eval_windows_script", "benchmarks/normalize_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "src/pkg/mod.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": ["tests/test_mod.py"],
                        "expected_suggested_edit_files": ["tests/test_mod.py"],
                        "expected_test_files": ["tests/test_mod.py"],
                        "expected_validation_commands_contain": ["pytest tests/test_mod.py -q"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "scenario_packs": [str(scenario_pack.name)],
        "records": [
            {
                "system": "copilot",
                "scenario_pack": str(scenario_pack.name),
                "scenario_id": "demo",
                "repo": tmp_path.name,
                "language": "python",
                "difficulty": "medium",
                "actual_primary_file": r"src\pkg\mod.py",
                "actual_primary_span": {"start_line": 1, "end_line": 2},
                "actual_dependent_files": [r"tests\test_mod.py"],
                "actual_suggested_edit_files": [r"tests\test_mod.py"],
                "actual_test_files": [r"tests\test_mod.py"],
                "actual_validation_commands": ["pytest tests/test_mod.py -q"],
                "context_token_count": 100,
                "wall_clock_seconds": 1.0,
                "deterministic_repeat_match": False,
                "notes": "",
            }
        ],
    }

    normalized = module.normalize_competitor_eval(payload, base_dir=tmp_path)

    row = normalized["records"][0]
    assert row["primary_file_hit"] == 1.0
    assert row["dependent_file_recall"] == 1.0


def test_render_comparison_scorecard_should_emit_ranked_markdown():
    module = _load_script_module(
        "render_comparison_scorecard_script", "benchmarks/render_comparison_scorecard.py"
    )
    payload = {
        "records": [{}, {}],
        "by_system": {
            "system-b": {
                "mean_overall_score": 0.4,
                "mean_primary_file_hit": 0.5,
                "mean_primary_span_hit": 0.5,
                "mean_wall_clock_seconds": 2.0,
            },
            "system-a": {
                "mean_overall_score": 0.8,
                "mean_primary_file_hit": 1.0,
                "mean_primary_span_hit": 1.0,
                "mean_wall_clock_seconds": 1.0,
            },
        },
    }

    markdown = module.render_scorecard(payload)

    assert markdown.startswith("# Competitor Evaluation Scorecard")
    assert markdown.index("`system-a`") < markdown.index("`system-b`")


def test_render_patch_scorecard_should_emit_summary_and_failures():
    module = _load_script_module("render_patch_scorecard_script", "benchmarks/render_patch_scorecard.py")
    markdown = module.render_patch_scorecard(
        [
            {
                "rows": [
                    {
                        "instance_id": "demo-1",
                        "system": "copilot",
                        "patch_applied": 1.0,
                        "validation_passed": 1.0,
                        "primary_file_hit": 1.0,
                        "primary_span_hit": 1.0,
                        "changed_file_recall": 1.0,
                        "predicted_test_hit_rate": 1.0,
                        "predicted_validation_cmd_hit_rate": 1.0,
                        "apply_error": "",
                    },
                    {
                        "instance_id": "demo-2",
                        "system": "gemini-cli",
                        "patch_applied": 0.0,
                        "validation_passed": 0.0,
                        "primary_file_hit": 0.0,
                        "primary_span_hit": 0.0,
                        "changed_file_recall": 0.0,
                        "predicted_test_hit_rate": 1.0,
                        "predicted_validation_cmd_hit_rate": 1.0,
                        "apply_error": "timeout after 10s",
                    },
                ]
            }
        ]
    )

    assert markdown.startswith("# Patch Evaluation Scorecard")
    assert "`copilot`" in markdown
    assert "`gemini-cli`" in markdown
    assert "timeout after 10s" in markdown


def test_real_patch_fixture_scenarios_should_load_and_score_oracle_predictions(tmp_path):
    driver_module = _load_script_module(
        "run_tensor_grep_patch_driver_real_fixture_script",
        "benchmarks/run_tensor_grep_patch_driver.py",
    )
    bakeoff_module = _load_script_module(
        "run_patch_bakeoff_real_fixture_script",
        "benchmarks/run_patch_bakeoff.py",
    )

    driver_scenarios = driver_module.load_driver_scenarios(
        Path("benchmarks/patch_eval/real_patch_driver_scenarios.json")
    )
    bakeoff_scenarios = bakeoff_module.load_patch_scenarios(
        Path("benchmarks/patch_eval/real_patch_bakeoff_scenarios.json")
    )

    assert len(driver_scenarios) == 12
    assert len(bakeoff_scenarios) == 12

    def _build_git_patch(repo_root: Path, relative_path: str, updated_text: str) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            a_root = temp_root / "a"
            b_root = temp_root / "b"
            shutil.copytree(repo_root, a_root)
            shutil.copytree(repo_root, b_root)
            (b_root / relative_path).write_text(updated_text, encoding="utf-8")
            completed = subprocess.run(
                ["git", "diff", "--no-index", "--", f"a/{relative_path}", f"b/{relative_path}"],
                cwd=temp_root,
                capture_output=True,
                text=True,
                check=False,
            )
            patch = completed.stdout
            patch = patch.replace(f"diff --git a/a/{relative_path} b/b/{relative_path}", f"diff --git a/{relative_path} b/{relative_path}")
            patch = patch.replace(f"--- a/a/{relative_path}", f"--- a/{relative_path}")
            patch = patch.replace(f"+++ b/b/{relative_path}", f"+++ b/{relative_path}")
            return patch

    click_repo = Path("benchmarks/patch_fixtures/click_format_filename")
    click_source = click_repo / "src/click/utils.py"
    click_original = click_source.read_text(encoding="utf-8")
    click_fixed = click_original.replace(
        "        filename = os.fspath(filename)\n",
        "        filename = os.path.basename(filename)\n",
        1,
    )
    click_patch = _build_git_patch(click_repo, "src/click/utils.py", click_fixed)
    click_prediction = {
        "instance_id": "click-format-filename-shorten",
        "system": "oracle",
        "model_patch": click_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    commander_repo = Path("benchmarks/patch_fixtures/commander_human_readable_arg_name")
    commander_source = commander_repo / "lib/argument.js"
    commander_original = commander_source.read_text(encoding="utf-8")
    commander_fixed = commander_original.replace(
        "  return arg.required ? '[' + nameOutput + ']' : '<' + nameOutput + '>';\n",
        "  return arg.required ? '<' + nameOutput + '>' : '[' + nameOutput + ']';\n",
        1,
    )
    commander_patch = _build_git_patch(commander_repo, "lib/argument.js", commander_fixed)
    commander_prediction = {
        "instance_id": "commander-human-readable-arg-name",
        "system": "oracle",
        "model_patch": commander_patch,
        "actual_test_files": ["tests/argument.test.js"],
        "actual_validation_commands": ["node --test tests/argument.test.js"],
    }
    click_unstyle_repo = Path("benchmarks/patch_fixtures/click_unstyle_ansi")
    click_unstyle_source = click_unstyle_repo / "src/click/_compat.py"
    click_unstyle_original = click_unstyle_source.read_text(encoding="utf-8")
    click_unstyle_fixed = click_unstyle_original.replace(
        r're.compile(r"\x1b\[[0-9;]*m")',
        r're.compile(r"\x1b\[[0-9;?]*[A-Za-z]")',
        1,
    )
    click_unstyle_patch = _build_git_patch(click_unstyle_repo, "src/click/_compat.py", click_unstyle_fixed)
    click_unstyle_prediction = {
        "instance_id": "click-unstyle-other-ansi",
        "system": "oracle",
        "model_patch": click_unstyle_patch,
        "actual_test_files": ["tests/test_termui.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    commander_error_repo = Path("benchmarks/patch_fixtures/commander_invalid_argument_error")
    commander_error_source = commander_error_repo / "lib/error.js"
    commander_error_original = commander_error_source.read_text(encoding="utf-8")
    commander_error_fixed = commander_error_original.replace(
        "    super(1, 'commander.invalidOptionArgument', message);\n",
        "    super(1, 'commander.invalidArgument', message);\n",
        1,
    )
    commander_error_patch = _build_git_patch(commander_error_repo, "lib/error.js", commander_error_fixed)
    commander_error_prediction = {
        "instance_id": "commander-invalid-argument-error-code",
        "system": "oracle",
        "model_patch": commander_error_patch,
        "actual_test_files": ["tests/error.test.js"],
        "actual_validation_commands": ["node --test tests/error.test.js"],
    }
    click_secho_repo = Path("benchmarks/patch_fixtures/click_secho_non_text")
    click_secho_source = click_secho_repo / "src/click/termui.py"
    click_secho_original = click_secho_source.read_text(encoding="utf-8")
    click_secho_fixed = click_secho_original.replace(
        "    if message is not None:\n",
        "    if message is not None and not isinstance(message, (bytes, bytearray)):\n",
        1,
    )
    click_secho_patch = _build_git_patch(
        click_secho_repo, "src/click/termui.py", click_secho_fixed
    )
    click_secho_prediction = {
        "instance_id": "click-secho-bytes-pass-through",
        "system": "oracle",
        "model_patch": click_secho_patch,
        "actual_test_files": ["tests/test_termui.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    click_style_repo = Path("benchmarks/patch_fixtures/click_style_non_text")
    click_style_source = click_style_repo / "src/click/termui.py"
    click_style_original = click_style_source.read_text(encoding="utf-8")
    click_style_fixed = click_style_original.replace(
        "def style(\n",
        "def style(\n",
        1,
    ).replace(
        "    bits: list[str] = []\n",
        "    if not isinstance(text, str):\n        text = str(text)\n\n    bits: list[str] = []\n",
        1,
    )
    click_style_patch = _build_git_patch(
        click_style_repo, "src/click/termui.py", click_style_fixed
    )
    click_style_prediction = {
        "instance_id": "click-style-non-text-coercion",
        "system": "oracle",
        "model_patch": click_style_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    click_abort_repo = Path("benchmarks/patch_fixtures/click_abort")
    click_abort_source = click_abort_repo / "src/click/core.py"
    click_abort_original = click_abort_source.read_text(encoding="utf-8")
    click_abort_fixed = click_abort_original.replace(
        '        raise RuntimeError("aborted")\n',
        "        raise Abort()\n",
        1,
    )
    click_abort_patch = _build_git_patch(click_abort_repo, "src/click/core.py", click_abort_fixed)
    click_abort_prediction = {
        "instance_id": "click-abort-raises-abort",
        "system": "oracle",
        "model_patch": click_abort_patch,
        "actual_test_files": ["tests/test_commands.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    click_binary_repo = Path("benchmarks/patch_fixtures/click_get_binary_stream")
    click_binary_source = click_binary_repo / "src/click/utils.py"
    click_binary_original = click_binary_source.read_text(encoding="utf-8")
    click_binary_fixed = click_binary_original.replace(
        "    opener = text_streams.get(name)\n",
        "    opener = binary_streams.get(name)\n",
        1,
    )
    click_binary_patch = _build_git_patch(
        click_binary_repo, "src/click/utils.py", click_binary_fixed
    )
    click_binary_prediction = {
        "instance_id": "click-get-binary-stream-uses-binary-map",
        "system": "oracle",
        "model_patch": click_binary_patch,
        "actual_test_files": ["tests/test_utils.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    commander_strip_repo = Path("benchmarks/patch_fixtures/commander_strip_color")
    commander_strip_source = commander_strip_repo / "lib/help.js"
    commander_strip_original = commander_strip_source.read_text(encoding="utf-8")
    commander_strip_fixed = commander_strip_original.replace(
        r"  const sgrPattern = /\x1b\[\d+(;\d+)*m/g;",
        r"  const sgrPattern = /\x1b\[(\d+(;\d+)*)?m/g;",
        1,
    )
    commander_strip_patch = _build_git_patch(
        commander_strip_repo, "lib/help.js", commander_strip_fixed
    )
    commander_strip_prediction = {
        "instance_id": "commander-strip-color-implicit-reset",
        "system": "oracle",
        "model_patch": commander_strip_patch,
        "actual_test_files": ["tests/help.test.js"],
        "actual_validation_commands": ["node --test tests/help.test.js"],
    }
    commander_dual_repo = Path("benchmarks/patch_fixtures/commander_dual_options")
    commander_dual_source = commander_dual_repo / "lib/option.js"
    commander_dual_original = commander_dual_source.read_text(encoding="utf-8")
    commander_dual_fixed = commander_dual_original.replace(
        "      if (!this.positiveOptions.has(key)) {\n",
        "      if (this.positiveOptions.has(key)) {\n",
        1,
    )
    commander_dual_patch = _build_git_patch(
        commander_dual_repo, "lib/option.js", commander_dual_fixed
    )
    commander_dual_prediction = {
        "instance_id": "commander-dual-options-unrelated-flags",
        "system": "oracle",
        "model_patch": commander_dual_patch,
        "actual_test_files": ["tests/options.dual-options.test.js"],
        "actual_validation_commands": ["node --test tests/options.dual-options.test.js"],
    }
    click_choice_repo = Path("benchmarks/patch_fixtures/click_choice_invalid_message")
    click_choice_source = click_choice_repo / "src/click/types.py"
    click_choice_original = click_choice_source.read_text(encoding="utf-8")
    click_choice_fixed = click_choice_original.replace(
        "        choices_str = \", \".join(map(repr, self.choices))\n"
        "        raise ValueError(f\"{value!r} is not one of {choices_str}.\")\n",
        "        raise ValueError(self.get_invalid_choice_message(value, ctx=ctx))\n\n"
        "    def get_invalid_choice_message(self, value: t.Any, ctx: t.Any) -> str:\n"
        "        choices_str = \", \".join(map(repr, self.choices))\n"
        "        return f\"{value!r} is not one of {choices_str}.\"\n",
        1,
    )
    click_choice_patch = _build_git_patch(
        click_choice_repo, "src/click/types.py", click_choice_fixed
    )
    click_choice_prediction = {
        "instance_id": "click-choice-invalid-message",
        "system": "oracle",
        "model_patch": click_choice_patch,
        "actual_test_files": ["tests/test_types.py"],
        "actual_validation_commands": ["pytest -q"],
    }
    commander_color_repo = Path("benchmarks/patch_fixtures/commander_use_color")
    commander_color_source = commander_color_repo / "lib/command.js"
    commander_color_original = commander_color_source.read_text(encoding="utf-8")
    commander_color_fixed = commander_color_original.replace(
        "function useColor() {\n"
        "  if (process.env.NO_COLOR !== undefined) return false;\n"
        "  if (process.env.FORCE_COLOR || process.env.CLICOLOR_FORCE) return true;\n"
        "  return undefined;\n"
        "}\n",
        "function useColor() {\n"
        "  if (process.env.NO_COLOR) return false;\n"
        "  if (process.env.FORCE_COLOR === '0' || process.env.FORCE_COLOR === 'false') return false;\n"
        "  if (process.env.FORCE_COLOR || process.env.CLICOLOR_FORCE !== undefined) return true;\n"
        "  return undefined;\n"
        "}\n",
        1,
    )
    commander_color_patch = _build_git_patch(
        commander_color_repo, "lib/command.js", commander_color_fixed
    )
    commander_color_prediction = {
        "instance_id": "commander-use-color-env-conventions",
        "system": "oracle",
        "model_patch": commander_color_patch,
        "actual_test_files": ["tests/useColor.test.js"],
        "actual_validation_commands": ["node --test tests/useColor.test.js"],
    }

    payload = bakeoff_module.build_patch_bakeoff_payload(
        bakeoff_scenarios,
        [
            click_prediction,
            commander_prediction,
            click_unstyle_prediction,
            commander_error_prediction,
            click_secho_prediction,
            click_style_prediction,
            click_abort_prediction,
            click_binary_prediction,
            commander_strip_prediction,
            commander_dual_prediction,
            click_choice_prediction,
            commander_color_prediction,
        ],
    )

    assert payload["summary"]["scenario_count"] == 12
    assert payload["summary"]["mean_patch_applied_rate"] == 1.0
    assert payload["summary"]["mean_validation_pass_rate"] == 1.0
    assert payload["summary"]["mean_primary_file_hit_rate"] == 1.0


def test_render_world_class_report_should_include_baseline_and_competitor_sections():
    module = _load_script_module(
        "render_world_class_report_script", "benchmarks/render_world_class_report.py"
    )
    external_eval = {
        "summary": {
            "scenario_count": 29,
            "mean_file_hit_rate": 1.0,
            "mean_span_hit_rate": 1.0,
            "mean_file_precision": 0.9,
            "mean_test_hit_rate": 0.7,
            "mean_validation_cmd_hit_rate": 1.0,
            "mean_false_positive_file_count": 1.2,
            "mean_context_token_count": 700.0,
        },
        "by_language": {
            "python": {"scenario_count": 10, "mean_file_precision": 0.72},
        },
    }
    profiling = {
        "dominant_phases": [
            {"name": "caller_scan", "elapsed_s": 5.0, "avg_elapsed_s": 0.2, "percent_total_elapsed": 25.0}
        ]
    }
    competitor = {
        "by_system": {
            "tensor-grep": {
                "mean_overall_score": 0.9,
                "mean_primary_file_hit": 1.0,
                "mean_primary_span_hit": 1.0,
                "mean_wall_clock_seconds": 2.0,
            }
        }
    }

    report = module.render_world_class_report(
        external_eval=external_eval,
        profiling=profiling,
        competitor=competitor,
    )

    assert report.startswith("# World-Class Evaluation Report")
    assert "## External Baseline" in report
    assert "## Dominant Profiling Phases" in report
    assert "## Competitor Summary" in report


def test_run_claude_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_claude_competitor_eval_script", "benchmarks/run_claude_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": [],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": json.dumps(
                    {
                        "result": json.dumps(
                            {
                                "actual_primary_file": "a.py",
                                "actual_primary_span": {"start_line": 1, "end_line": 2},
                                "actual_dependent_files": [],
                                "actual_suggested_edit_files": [],
                                "actual_test_files": [],
                                "actual_validation_commands": ["pytest -q"],
                                "context_token_count": 123,
                                "notes": "ok",
                            }
                        )
                    }
                )
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="sonnet", permission_mode="bypassPermissions")

    assert payload["artifact"] == "claude_competitor_eval"
    assert payload["suite"] == "run_claude_competitor_eval"
    assert payload["records"][0]["system"] == "claude-code"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_codex_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_codex_competitor_eval_script", "benchmarks/run_codex_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": [],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "demo"}),
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "agent_message",
                                    "text": json.dumps(
                                        {
                                            "actual_primary_file": "a.py",
                                            "actual_primary_span": {"start_line": 1, "end_line": 2},
                                            "actual_dependent_files": [],
                                            "actual_suggested_edit_files": [],
                                            "actual_test_files": [],
                                            "actual_validation_commands": ["pytest -q"],
                                            "context_token_count": 123,
                                            "notes": "ok",
                                        }
                                    ),
                                },
                            }
                        ),
                    ]
                )
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gpt-5-codex")

    assert payload["artifact"] == "codex_competitor_eval"
    assert payload["suite"] == "run_codex_competitor_eval"
    assert payload["records"][0]["system"] == "codex"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_codex_competitor_eval_should_cleanup_ephemeral_agents_file(tmp_path):
    module = _load_script_module(
        "run_codex_competitor_eval_cleanup_script", "benchmarks/run_codex_competitor_eval.py"
    )
    agents_path = tmp_path / "AGENTS.md"

    with module._ephemeral_repo_instructions(tmp_path):
        assert agents_path.exists()

    assert not agents_path.exists()


def test_run_bakeoff_should_pass_provider_to_blast_radius(monkeypatch, tmp_path):
    module = _load_script_module("run_bakeoff_provider_script", "benchmarks/run_bakeoff.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        lambda symbol, path, profile=False, semantic_provider="native": captured.update(
            {"symbol": symbol, "path": str(path), "provider": semantic_provider}
        )
        or {
            "edit_plan_seed": {
                "primary_file": "a.py",
                "primary_span": {"start_line": 1, "end_line": 2},
                "dependent_files": [],
                "suggested_edits": [],
                "validation_tests": [],
                "validation_commands": [],
            },
            "tests": [],
            "token_estimate": 12,
            "semantic_provider": semantic_provider,
        },
    )

    result = module.run_scenario(
        {
            "repo_fixture": str(repo_root),
            "query_or_symbol": "create_invoice",
            "mode": "blast-radius",
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 2},
            "expected_dependent_files": [],
            "expected_suggested_edit_files": [],
            "expected_test_files": [],
            "expected_validation_commands_contain": [],
        },
        provider="hybrid",
    )

    assert captured["provider"] == "hybrid"
    assert result["semantic_provider"] == "hybrid"


def test_run_external_eval_should_include_provider_in_payload(monkeypatch, tmp_path):
    module = _load_script_module("run_external_eval_provider_script", "benchmarks/run_external_eval.py")
    manifest = {"manifest_path": "manifest.json", "packs": [{"name": "demo", "language": "python", "scenario_pack": "demo.json"}]}
    monkeypatch.setattr(
        module,
        "run_pack",
        lambda entry, profile=False, provider="native": {
            "name": entry["name"],
            "language": entry["language"],
            "scenario_pack": entry["scenario_pack"],
            "scenario_count": 1,
            "summary": {
                "scenario_count": 1,
                "mean_file_hit_rate": 1.0,
                "mean_file_precision": 1.0,
                "mean_span_hit_rate": 1.0,
                "mean_test_hit_rate": 1.0,
                "mean_validation_cmd_hit_rate": 1.0,
                "mean_context_token_count": 1.0,
                "mean_false_positive_file_count": 0.0,
            },
            "analysis": {"bucket_counts": {}, "mean_file_precision": 1.0, "scenarios_with_false_positives": 0},
            "rows": [{"language": entry["language"], "file_hit_rate": 1.0, "file_precision": 1.0, "span_hit_rate": 1.0, "test_hit_rate": 1.0, "validation_cmd_hit_rate": 1.0, "context_token_count": 1, "false_positive_files": []}],
            "payload": {},
        },
    )

    payload = module.build_external_eval_payload(manifest, provider="lsp")

    assert payload["semantic_provider"] == "lsp"


def test_run_patch_bakeoff_should_score_applied_patch_and_validation(tmp_path):
    module = _load_script_module("run_patch_bakeoff_script", "benchmarks/run_patch_bakeoff.py")
    repo_root = tmp_path / "repo"
    src_dir = repo_root / "src"
    tests_dir = repo_root / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)
    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    (tests_dir / "test_payments.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def test_create_invoice():\n"
        "    assert create_invoice(2) == 4\n",
        encoding="utf-8",
    )
    patch_text = "\n".join(
        [
            "diff --git a/src/payments.py b/src/payments.py",
            "--- a/src/payments.py",
            "+++ b/src/payments.py",
            "@@ -1,2 +1,2 @@",
            " def create_invoice(total):",
            "-    return total + 1",
            "+    return total + 2",
            "",
        ]
    )
    scenario = {
        "instance_id": "demo-1",
        "repo_fixture": str(repo_root),
        "expected_primary_file": "src/payments.py",
        "expected_primary_span": {"start_line": 1, "end_line": 2},
        "expected_changed_files": ["src/payments.py"],
        "expected_test_files": ["tests/test_payments.py"],
        "validation_commands": [
            "python -c \"import sys; sys.path.insert(0, 'src'); import payments; sys.exit(0 if payments.create_invoice(2) == 4 else 1)\""
        ],
        "expected_validation_commands_contain": ["python -c"],
    }
    prediction = {
        "instance_id": "demo-1",
        "system": "demo",
        "model_patch": patch_text,
        "actual_test_files": ["tests/test_payments.py"],
        "actual_validation_commands": ["python -c \"...\""],
    }

    row = module.evaluate_prediction(scenario, prediction)

    assert row["patch_applied"] is True
    assert row["validation_passed"] is True
    assert row["primary_file_hit"] == 1.0
    assert row["primary_span_hit"] == 1.0
    assert row["changed_file_recall"] == 1.0
    assert row["changed_file_precision"] == 1.0
    assert row["predicted_test_hit_rate"] == 1.0
    assert row["predicted_validation_cmd_hit_rate"] == 1.0


def test_run_patch_bakeoff_should_normalize_truncated_patch_before_apply(tmp_path):
    module = _load_script_module(
        "run_patch_bakeoff_truncated_script", "benchmarks/run_patch_bakeoff.py"
    )
    repo_root = tmp_path / "repo"
    src_dir = repo_root / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "demo.py").write_text(
        "def value():\n    return 'old'\n",
        encoding="utf-8",
    )
    scenario = {
        "instance_id": "demo-truncated",
        "repo_fixture": str(repo_root),
        "expected_primary_file": "src/demo.py",
        "expected_primary_span": {"start_line": 1, "end_line": 2},
        "expected_changed_files": ["src/demo.py"],
        "expected_test_files": [],
        "validation_commands": [],
        "expected_validation_commands_contain": [],
    }
    prediction = {
        "instance_id": "demo-truncated",
        "system": "demo",
        "model_patch": "\n".join(
            [
                "diff --git a/src/demo.py b/src/demo.py",
                "--- a/src/demo.py",
                "+++ b/src/demo.py",
                "@@ -1,2 +1,2 @@",
                " def value():",
                "-    return 'old'",
                "+    return 'new'",
            ]
        ),
        "actual_test_files": [],
        "actual_validation_commands": [],
    }

    row = module.evaluate_prediction(scenario, prediction)

    assert row["patch_applied"] is True
    assert row["primary_file_hit"] == 1.0
    assert row["primary_span_hit"] == 1.0


def test_run_patch_bakeoff_should_build_summary_payload(tmp_path):
    module = _load_script_module("run_patch_bakeoff_payload_script", "benchmarks/run_patch_bakeoff.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "expected_primary_file": "a.py",
            "expected_primary_span": {"start_line": 1, "end_line": 1},
            "expected_changed_files": ["a.py"],
            "expected_test_files": [],
            "validation_commands": [],
            "expected_validation_commands_contain": [],
        }
    ]
    predictions = [{"instance_id": "demo-1", "system": "demo", "model_patch": "", "actual_validation_commands": []}]

    payload = module.build_patch_bakeoff_payload(scenarios, predictions)

    assert payload["suite"] == "run_patch_bakeoff"
    assert payload["summary"]["scenario_count"] == 1
    assert payload["rows"][0]["system"] == "demo"


def test_run_tensor_grep_patch_driver_should_build_patch_ready_records(monkeypatch, tmp_path):
    module = _load_script_module("run_tensor_grep_patch_driver_script", "benchmarks/run_tensor_grep_patch_driver.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        lambda symbol, path, max_files=6, max_sources=6, max_symbols_per_file=6, semantic_provider="native": {
            "semantic_provider": semantic_provider,
            "rendered_context": "def create_invoice(total):\n    return total + 1\n",
            "token_estimate": 42,
            "tests": ["tests/test_payments.py"],
            "edit_plan_seed": {
                "primary_file": "src/payments.py",
                "primary_span": {"start_line": 1, "end_line": 2},
                "dependent_files": ["src/service.py"],
                "suggested_edits": [{"file": "src/service.py"}],
                "validation_tests": ["tests/test_payments.py"],
                "validation_commands": ["pytest -q"],
            },
        },
    )
    scenarios = [
        {
            "instance_id": "demo-1",
            "repo_fixture": str(repo_root),
            "query_or_symbol": "create_invoice",
            "mode": "blast-radius",
            "problem_statement": "Change create_invoice to add 2 instead of 1.",
        }
    ]

    payload = module.build_payload(scenarios, provider="hybrid")

    assert payload["suite"] == "run_tensor_grep_patch_driver"
    assert payload["semantic_provider"] == "hybrid"
    assert payload["records"][0]["actual_primary_file"] == "src/payments.py"
    assert payload["records"][0]["semantic_provider"] == "hybrid"
    prompt = payload["records"][0]["prompt"]
    assert "Prefer editing the repository files directly." in prompt
    assert "include diff --git headers" in prompt
    assert "Do not emit fragile one-line hunks." in prompt
    assert "Do not run the test suite or create caches like .pytest_cache." in prompt


def test_patch_runner_common_should_ignore_ephemeral_files_when_diffing(tmp_path):
    module = _load_script_module("patch_runner_common_script", "benchmarks/patch_runner_common.py")
    before_root = tmp_path / "a"
    work_root = tmp_path / "b"
    (before_root / "src").mkdir(parents=True)
    (work_root / "src").mkdir(parents=True)
    (before_root / ".pytest_cache").mkdir()
    (work_root / ".pytest_cache").mkdir()
    (before_root / "src" / "demo.py").write_text("old\n", encoding="utf-8")
    (work_root / "src" / "demo.py").write_text("new\n", encoding="utf-8")
    (work_root / ".pytest_cache" / ".gitignore").write_text("*\n", encoding="utf-8")
    (work_root / "AGENTS.md").write_text("temp\n", encoding="utf-8")

    patch_text = module.derive_patch_from_repo_changes(before_root, work_root)

    assert "diff --git a/src/demo.py b/src/demo.py" in patch_text
    assert ".pytest_cache" not in patch_text
    assert "AGENTS.md" not in patch_text


def test_patch_runner_common_should_normalize_truncated_model_patch():
    module = _load_script_module("patch_runner_common_normalize_script", "benchmarks/patch_runner_common.py")
    patch_text = "\n".join(
        [
            "diff --git a/src/demo.py b/src/demo.py",
            "index 1111111..2222222 100644",
            "--- a/src/demo.py",
            "+++ b/src/demo.py",
            "@@ -1,3 +1,3 @@",
            " line1",
            "-old",
            "+new",
            " line3",
        ]
    )

    normalized = module.normalize_model_patch_text(patch_text)

    assert normalized.endswith("\n \n")


def test_run_gemini_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module("run_gemini_patch_predictions_script", "benchmarks/run_gemini_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": ["tests/test_demo.py"],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_gemini_command",
        lambda *args, **kwargs: json.dumps(
            {
                "response": "```diff\n"
                "diff --git a/demo.py b/demo.py\n"
                "--- a/demo.py\n"
                "+++ b/demo.py\n"
                "@@ -1 +1 @@\n"
                "-old\n"
                "+new\n"
                "```"
            }
        ),
    )

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash")

    assert payload["suite"] == "run_gemini_patch_predictions"
    assert payload["records"][0]["system"] == "gemini-cli"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_gemini_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module("run_gemini_patch_predictions_timeout_script", "benchmarks/run_gemini_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-timeout",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _raise_timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="gemini", timeout=5)

    monkeypatch.setattr(module, "_run_gemini_command", _raise_timeout)

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash", timeout_seconds=5)

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_gemini_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module("run_gemini_patch_predictions_diff_script", "benchmarks/run_gemini_patch_predictions.py")
    (tmp_path / "demo.py").write_text("old\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-diff",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _edit_repo(repo_root, prompt, **kwargs):
        del prompt, kwargs
        (repo_root / "demo.py").write_text("new\n", encoding="utf-8")
        return json.dumps({"response": "no diff emitted"})

    monkeypatch.setattr(module, "_run_gemini_command", _edit_repo)

    payload = module.build_payload(driver_payload, model="gemini-2.5-flash")

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_gemini_patch_predictions_should_terminate_process_tree_on_timeout(monkeypatch, tmp_path):
    module = _load_script_module("run_gemini_patch_predictions_kill_script", "benchmarks/run_gemini_patch_predictions.py")
    calls: list[tuple[str, object]] = []

    class FakeProc:
        pid = 4242
        returncode = None

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            raise module.subprocess.TimeoutExpired(cmd="gemini", timeout=timeout)

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

    monkeypatch.setattr(module, "resolve_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(("taskkill", list(args[0]))) or type("Proc", (), {"returncode": 0})(),
    )

    try:
        module._run_gemini_command(tmp_path, "prompt", model="gemini-2.5-flash", timeout_seconds=7)
    except module.subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("expected timeout")

    assert ("communicate", 7) in calls
    assert any(call[0] == "taskkill" and "/PID" in call[1] for call in calls)


def test_run_copilot_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module("run_copilot_patch_predictions_script", "benchmarks/run_copilot_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": ["tests/test_demo.py"],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_copilot_command",
        lambda *args, **kwargs: "```diff\n"
        "diff --git a/demo.py b/demo.py\n"
        "--- a/demo.py\n"
        "+++ b/demo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "```",
    )

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert payload["suite"] == "run_copilot_patch_predictions"
    assert payload["records"][0]["system"] == "copilot"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_copilot_patch_predictions_should_strip_invalid_index_lines(monkeypatch, tmp_path):
    module = _load_script_module("run_copilot_patch_predictions_normalize_script", "benchmarks/run_copilot_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_copilot_command",
        lambda *args, **kwargs: "diff --git a/demo.py b/demo.py\n"
        "index XXXXXXX..XXXXXXX 100644\n"
        "--- a/demo.py\n"
        "+++ b/demo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n",
    )

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert "index XXXXXXX..XXXXXXX 100644" not in payload["records"][0]["model_patch"]
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]


def test_run_copilot_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module("run_copilot_patch_predictions_timeout_script", "benchmarks/run_copilot_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-timeout",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _raise_timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="copilot", timeout=5)

    monkeypatch.setattr(module, "_run_copilot_command", _raise_timeout)

    payload = module.build_payload(driver_payload, model="gpt-5.2", timeout_seconds=5)

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_copilot_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module("run_copilot_patch_predictions_diff_script", "benchmarks/run_copilot_patch_predictions.py")
    (tmp_path / "demo.py").write_text("old\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-diff",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _edit_repo(repo_root, prompt, **kwargs):
        del prompt, kwargs
        (repo_root / "demo.py").write_text("new\n", encoding="utf-8")
        return "no diff emitted"

    monkeypatch.setattr(module, "_run_copilot_command", _edit_repo)

    payload = module.build_payload(driver_payload, model="gpt-5.2")

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_claude_patch_predictions_should_build_patch_records(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_patch_predictions_script", "benchmarks/run_claude_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-1",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": ["tests/test_demo.py"],
                "actual_validation_commands": ["pytest -q"],
            }
        ]
    }
    monkeypatch.setattr(
        module,
        "_run_claude_command",
        lambda *args, **kwargs: "```diff\n"
        "diff --git a/demo.py b/demo.py\n"
        "--- a/demo.py\n"
        "+++ b/demo.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "```",
    )

    payload = module.build_payload(driver_payload, model="sonnet", permission_mode="bypassPermissions")

    assert payload["suite"] == "run_claude_patch_predictions"
    assert payload["records"][0]["system"] == "claude-code"
    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert payload["records"][0]["actual_validation_commands"] == ["pytest -q"]


def test_run_claude_patch_predictions_should_prefix_direct_edit_instruction():
    module = _load_script_module(
        "run_claude_patch_predictions_prompt_script",
        "benchmarks/run_claude_patch_predictions.py",
    )

    prompt = module._build_claude_prompt("Return only a diff patch.")

    assert "edit the repository files directly" in prompt
    assert "do not print a summary" in prompt
    assert prompt.endswith("Return only a diff patch.")


def test_run_claude_patch_predictions_should_capture_timeout_as_empty_patch(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_patch_predictions_timeout_script", "benchmarks/run_claude_patch_predictions.py")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-timeout",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _raise_timeout(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(module, "_run_claude_command", _raise_timeout)

    payload = module.build_payload(
        driver_payload,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
    )

    assert payload["records"][0]["model_patch"] == ""
    assert payload["records"][0]["notes"] == "timeout after 5s"


def test_run_claude_patch_predictions_should_fallback_to_repo_diff(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_patch_predictions_diff_script", "benchmarks/run_claude_patch_predictions.py")
    (tmp_path / "demo.py").write_text("old\n", encoding="utf-8")
    driver_payload = {
        "records": [
            {
                "instance_id": "demo-diff",
                "repo_fixture": str(tmp_path),
                "prompt": "Return only a diff patch.",
                "actual_test_files": [],
                "actual_validation_commands": [],
            }
        ]
    }

    def _edit_repo(repo_root, prompt, **kwargs):
        del prompt, kwargs
        (repo_root / "demo.py").write_text("new\n", encoding="utf-8")
        return "no diff emitted"

    monkeypatch.setattr(module, "_run_claude_command", _edit_repo)

    payload = module.build_payload(driver_payload, model="sonnet", permission_mode="bypassPermissions")

    assert "diff --git a/demo.py b/demo.py" in payload["records"][0]["model_patch"]
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "old\n"


def test_run_claude_patch_predictions_should_separate_prompt_from_add_dir(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_claude_patch_predictions_command_script",
        "benchmarks/run_claude_patch_predictions.py",
    )
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("diff --git a/demo.py b/demo.py\n", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or FakeProc(),
    )

    output = module._run_claude_command(
        tmp_path,
        "Return only a diff patch.",
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
    )

    assert output.startswith("diff --git")
    assert "--" in calls[0]
    assert calls[0][-2:] == ["--", "Return only a diff patch."]


def test_run_claude_skill_ab_should_install_project_skill(tmp_path):
    module = _load_script_module("run_claude_skill_ab_skill_script", "benchmarks/run_claude_skill_ab.py")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    installed = module.install_skill_package(repo_root, skill_dir)

    assert installed == repo_root / ".claude" / "skills" / "tensor-grep"
    assert (repo_root / ".claude" / "skills" / "tensor-grep" / "SKILL.md").exists()
    assert (repo_root / ".claude" / "skills" / "tensor-grep" / "REFERENCE.md").exists()


def test_run_claude_skill_ab_should_write_claude_md(tmp_path):
    module = _load_script_module("run_claude_skill_ab_claude_md_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    guidance_path = module.write_claude_md(repo_root)

    assert guidance_path == repo_root / "CLAUDE.md"
    text = guidance_path.read_text(encoding="utf-8")
    assert "Use the tensor-grep project skill" in text
    assert "Do not ask what task to perform" in text
    assert "make the change directly" in text


def test_run_claude_skill_ab_should_install_tg_trace_wrapper(tmp_path):
    module = _load_script_module("run_claude_skill_ab_tg_wrapper_script", "benchmarks/run_claude_skill_ab.py")
    run_root = tmp_path / "run"
    run_root.mkdir()

    wrapper_dir, log_path = module.install_tg_trace_wrapper(run_root)

    assert wrapper_dir == run_root / ".claude-bin"
    assert log_path == run_root / "tg_trace.jsonl"
    assert (wrapper_dir / "tg.cmd").exists()
    assert (wrapper_dir / "tg.ps1").exists()
    assert "TENSOR_GREP_TRACE_LOG" in (wrapper_dir / "tg.ps1").read_text(encoding="utf-8")


def test_run_claude_skill_ab_should_classify_response_shape():
    module = _load_script_module("run_claude_skill_ab_response_shape_script", "benchmarks/run_claude_skill_ab.py")

    assert module.classify_response_shape("What would you like me to do?", "") == "meta_question"
    assert module.classify_response_shape("What would you like me to help you with?", "") == "meta_question"
    assert module.classify_response_shape("What task would you like me to work on?", "") == "meta_question"
    assert module.classify_response_shape("", "diff --git a/x b/x") == "direct_patch"
    assert module.classify_response_shape("Fixed the bug.", "diff --git a/x b/x") == "analysis_then_patch"
    assert module.classify_response_shape("I inspected the repo.", "") == "analysis_only"
    assert module.classify_response_shape("", "") == "empty"


def test_run_claude_skill_ab_should_compute_first_tg_seconds():
    module = _load_script_module("run_claude_skill_ab_first_tg_script", "benchmarks/run_claude_skill_ab.py")

    assert module.first_tg_seconds(100.0, []) is None
    assert module.first_tg_seconds(100.0, [{"timestamp_epoch_s": 100.75}]) == 0.75


def test_run_claude_skill_ab_should_compute_post_edit_deliberation_seconds():
    module = _load_script_module("run_claude_skill_ab_post_edit_script", "benchmarks/run_claude_skill_ab.py")

    assert module.post_edit_deliberation_seconds(None, 10.0) is None
    assert module.post_edit_deliberation_seconds(0.5, None) is None
    assert module.post_edit_deliberation_seconds(0.5, 10.0) == 9.5


def test_run_claude_skill_ab_should_clear_transient_file_change_when_no_final_diff(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_transient_change_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")

    def _fake_run(repo_dir, prompt, **kwargs):
        path = Path(repo_dir) / "demo.py"
        path.write_text("temp\n", encoding="utf-8")
        path.write_text("old\n", encoding="utf-8")
        return "What would you like me to do?"

    monkeypatch.setattr(module, "_run_claude_command", _fake_run)

    payload = module.build_payload(
        {
            "records": [
                {
                    "instance_id": "demo-1",
                    "repo_fixture": str(repo_root),
                    "prompt": "Fix the bug.",
                    "actual_validation_commands": ["pytest -q"],
                }
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
    )

    baseline_trace = payload["trace_records"][0]
    assert baseline_trace["changed_file_count"] == 0
    assert baseline_trace["first_file_change_seconds"] is None


def test_run_claude_skill_ab_prompt_should_require_non_interactive_action(tmp_path):
    module = _load_script_module("run_claude_skill_ab_prompt_script", "benchmarks/run_claude_skill_ab.py")

    prompt = module._build_claude_prompt("Fix the bug.")
    terse_prompt = module._build_claude_prompt("Fix the bug.", terse_output=True)

    assert "edit the repository files directly" in prompt
    assert "do not print a summary" in prompt
    assert prompt.endswith("Fix the bug.")
    assert "stop immediately" in terse_prompt
    assert "Do not print any explanation" in terse_prompt


def test_run_claude_skill_ab_should_prepend_explicit_skill_instruction():
    module = _load_script_module("run_claude_skill_ab_enhanced_prompt_script", "benchmarks/run_claude_skill_ab.py")

    prompt = module.build_system_prompt("Fix the bug.", use_skill=True)
    terse_prompt = module.build_system_prompt("Fix the bug.", use_skill=True, enhanced_output_contract="terse")
    engage_prompt = module.build_system_prompt("Fix the bug.", use_skill=True, enhanced_task_contract="engage")

    assert "Use the tensor-grep project skill" in prompt
    assert prompt.endswith("Fix the bug.")
    assert "stop immediately" in terse_prompt
    assert "Start working on it immediately" in engage_prompt


def test_run_claude_skill_ab_should_resolve_contract_profiles(monkeypatch):
    module = _load_script_module("run_claude_skill_ab_contract_profile_script", "benchmarks/run_claude_skill_ab.py")

    assert module.resolve_contract_profile(
        "current",
        enhanced_output_contract="terse",
        enhanced_task_contract="engage",
    ) == ("terse", "engage")
    assert module.resolve_contract_profile(
        "probe-standard-engage",
        enhanced_output_contract="terse",
        enhanced_task_contract="standard",
    ) == ("standard", "engage")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_claude_skill_ab.py",
            "--input",
            "driver.json",
            "--enhanced-output-contract",
            "terse",
            "--enhanced-task-contract",
            "standard",
            "--enhanced-contract-profile",
            "probe-standard-engage",
        ],
    )

    args = module.parse_args()

    assert args.enhanced_output_contract == "standard"
    assert args.enhanced_task_contract == "engage"


def test_run_claude_skill_ab_should_rewrite_prompt_repo_paths(tmp_path):
    module = _load_script_module("run_claude_skill_ab_rewrite_script", "benchmarks/run_claude_skill_ab.py")
    source_repo = tmp_path / "source"
    copied_repo = tmp_path / "copy"
    source_repo.mkdir()
    copied_repo.mkdir()
    original = (
        f"File: {source_repo}\\src\\demo.py\n"
        f"Context path: {source_repo / 'tests' / 'test_demo.py'}"
    )

    rewritten = module.rewrite_prompt_repo_paths(original, source_repo, copied_repo)

    assert str(source_repo) not in rewritten
    assert str(copied_repo) in rewritten


def test_run_claude_skill_ab_default_work_root_should_live_outside_repo():
    module = _load_script_module("run_claude_skill_ab_work_root_script", "benchmarks/run_claude_skill_ab.py")

    assert Path(module.DEFAULT_WORK_ROOT) != Path(module.ROOT_DIR)
    assert Path(module.DEFAULT_WORK_ROOT).is_absolute()
    assert module.ROOT_DIR not in Path(module.DEFAULT_WORK_ROOT).parents


def test_run_claude_skill_ab_should_build_baseline_and_enhanced_records(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")

    calls: list[tuple[str, str, str, str]] = []

    def _fake_run(repo_dir, prompt, **kwargs):
        has_skill = (Path(repo_dir) / ".claude" / "skills" / "tensor-grep" / "SKILL.md").exists()
        calls.append((str(repo_dir), str(repo_dir), prompt, str(has_skill)))
        if has_skill:
            assert (Path(repo_dir) / "CLAUDE.md").exists()
            (Path(repo_dir) / "demo.py").write_text("new\n", encoding="utf-8")
            Path(kwargs["extra_env"]["TENSOR_GREP_TRACE_LOG"]).write_text(
                '{"argv":["tg","defs","Demo"],"exit_code":0,"duration_seconds":0.125,"timestamp_epoch_s":100.125}\n',
                encoding="utf-8",
            )
        return "ok"

    monkeypatch.setattr(module, "_run_claude_command", _fake_run)

    payload = module.build_payload(
        {
            "records": [
                {
                    "instance_id": "demo-1",
                    "repo_fixture": str(repo_root),
                    "prompt": "Fix the bug.",
                    "actual_validation_commands": ["pytest -q"],
                }
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="terse",
        enhanced_task_contract="engage",
    )

    assert payload["artifact"] == "claude_skill_ab"
    assert payload["enhanced_output_contract"] == "terse"
    assert payload["enhanced_task_contract"] == "engage"
    assert payload["trace_artifact"] == "claude_skill_ab_trace"
    assert len(payload["trace_records"]) == 2
    assert [record["system"] for record in payload["records"]] == [
        "claude-baseline",
        "claude-enhanced",
    ]
    assert payload["records"][0]["model_patch"] == ""
    assert "diff --git a/demo.py b/demo.py" in payload["records"][1]["model_patch"]
    assert calls[0][3] == "False"
    assert calls[1][3] == "True"
    assert "edit the repository files directly" in calls[0][2]
    assert "Use the tensor-grep project skill" not in calls[0][2]
    assert "Use the tensor-grep project skill" in calls[1][2]
    assert payload["trace_records"][0]["use_skill"] is False
    assert payload["trace_records"][1]["use_skill"] is True
    assert payload["trace_records"][1]["enhanced_output_contract"] == "terse"
    assert payload["trace_records"][1]["enhanced_task_contract"] == "engage"
    assert payload["trace_records"][0]["response_shape"] == "analysis_only"
    assert payload["trace_records"][1]["response_shape"] == "analysis_then_patch"
    assert payload["trace_records"][1]["asked_meta_question"] is False
    assert payload["trace_records"][0]["first_patch_seconds"] is None
    assert payload["trace_records"][1]["first_patch_seconds"] is not None
    assert payload["trace_records"][1]["first_file_change_seconds"] is not None
    assert payload["trace_records"][1]["post_edit_deliberation_seconds"] is not None
    assert payload["trace_records"][1]["changed_file_count"] == 1
    assert payload["trace_records"][1]["tg_invocation_count"] == 1
    assert payload["trace_records"][1]["tg_seconds_total"] == 0.125
    assert payload["trace_records"][1]["first_tg_seconds"] is not None
    assert payload["trace_records"][1]["tg_trace_records"][0]["argv"] == ["tg", "defs", "Demo"]
    assert "claude_seconds" in payload["trace_records"][0]["timing"]


def test_run_claude_skill_ab_should_support_partial_resume(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_resume_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"

    seen: list[str] = []

    def _fake_run_ab_record(record, **kwargs):
        seen.append(str(record["instance_id"]))
        return (
            [
                {"instance_id": str(record["instance_id"]), "system": "claude-baseline", "model_patch": "", "wall_clock_seconds": 1.0},
                {"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "model_patch": "diff --git a/x b/x", "wall_clock_seconds": 2.0},
            ],
            [
                {"instance_id": str(record["instance_id"]), "system": "claude-baseline", "response_shape": "analysis_only"},
                {"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "response_shape": "analysis_then_patch"},
            ],
        )

    monkeypatch.setattr(module, "run_ab_record", _fake_run_ab_record)

    partial = module.build_partial_payload(
        [
            {"instance_id": "demo-1", "system": "claude-baseline", "model_patch": "", "wall_clock_seconds": 1.0},
            {"instance_id": "demo-1", "system": "claude-enhanced", "model_patch": "diff --git a/x b/x", "wall_clock_seconds": 2.0},
        ],
        [
            {"instance_id": "demo-1", "system": "claude-baseline", "response_shape": "analysis_only"},
            {"instance_id": "demo-1", "system": "claude-enhanced", "response_shape": "analysis_then_patch"},
        ],
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_payload(
        {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
                {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert len(payload["records"]) == 4
    assert len(payload["trace_records"]) == 4


def test_run_claude_skill_ab_should_checkpoint_per_record(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_checkpoint_script", "benchmarks/run_claude_skill_ab.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "demo.py").write_text("old\n", encoding="utf-8")
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: tensor-grep\ndescription: use tg\n---\n", encoding="utf-8")
    (skill_dir / "REFERENCE.md").write_text("# ref\n", encoding="utf-8")
    output_path = tmp_path / "ab.json"

    monkeypatch.setattr(
        module,
        "run_ab_record",
        lambda record, **kwargs: (
            [
                {"instance_id": str(record["instance_id"]), "system": "claude-baseline", "model_patch": "", "wall_clock_seconds": 1.0},
                {"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "model_patch": "diff --git a/x b/x", "wall_clock_seconds": 2.0},
            ],
            [
                {"instance_id": str(record["instance_id"]), "system": "claude-baseline", "response_shape": "analysis_only"},
                {"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "response_shape": "analysis_then_patch"},
            ],
        ),
    )

    writes: list[int] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append(len(payload["records"]))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    payload = module.build_payload(
        {
            "records": [
                {"instance_id": "demo-1", "repo_fixture": str(repo_root), "prompt": "Fix one."},
                {"instance_id": "demo-2", "repo_fixture": str(repo_root), "prompt": "Fix two."},
            ]
        },
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        skill_dir=skill_dir,
        work_root=tmp_path / "work",
        enhanced_output_contract="standard",
        enhanced_task_contract="engage",
        output_path=output_path,
        resume=False,
    )

    assert len(payload["records"]) == 4
    assert writes == [2, 4]


def test_run_claude_skill_ab_should_pass_prompt_as_positional_argument(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_command_script", "benchmarks/run_claude_skill_ab.py")
    calls: list[list[str]] = []
    kwargs_calls: list[dict[str, object]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(module, "resolve_tg_binary", lambda: "C:/tools/tg.exe")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or kwargs_calls.append(kwargs) or FakeProc(),
    )

    output = module._run_claude_command(
        tmp_path,
        "Say hi in one word.",
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
        extra_env={
            "PATH": "C:/tmp/bin",
            "TENSOR_GREP_REAL": "C:/tools/tg.exe",
            "TENSOR_GREP_TRACE_LOG": "C:/tmp/tg.jsonl",
        },
    )

    assert output == "ok"
    assert "--dangerously-skip-permissions" in calls[0]


def test_run_claude_skill_ab_matrix_should_build_experiment_configs():
    module = _load_script_module("run_claude_skill_ab_matrix_configs_script", "benchmarks/run_claude_skill_ab_matrix.py")

    experiments = module.build_experiment_configs(["standard", "terse"], ["standard", "engage"])

    assert experiments == [
        {"name": "output-standard__task-standard", "enhanced_output_contract": "standard", "enhanced_task_contract": "standard"},
        {"name": "output-standard__task-engage", "enhanced_output_contract": "standard", "enhanced_task_contract": "engage"},
        {"name": "output-terse__task-standard", "enhanced_output_contract": "terse", "enhanced_task_contract": "standard"},
        {"name": "output-terse__task-engage", "enhanced_output_contract": "terse", "enhanced_task_contract": "engage"},
    ]


def test_run_claude_skill_ab_matrix_should_summarize_trace_rows():
    module = _load_script_module("run_claude_skill_ab_matrix_summary_script", "benchmarks/run_claude_skill_ab_matrix.py")

    summary = module.summarize_trace_rows(
        [
            {
                "system": "claude-baseline",
                "asked_meta_question": False,
                "response_shape": "analysis_then_patch",
                "first_tg_seconds": None,
                "first_patch_seconds": 10.0,
                "first_file_change_seconds": 0.2,
                "post_edit_deliberation_seconds": 9.8,
                "tg_invocation_count": 0,
                "tg_seconds_total": 0.0,
                "changed_file_count": 1,
            },
            {
                "system": "claude-enhanced",
                "asked_meta_question": True,
                "response_shape": "meta_question",
                "first_tg_seconds": 1.5,
                "first_patch_seconds": None,
                "first_file_change_seconds": None,
                "post_edit_deliberation_seconds": None,
                "tg_invocation_count": 2,
                "tg_seconds_total": 0.75,
                "changed_file_count": 0,
            },
            {
                "system": "claude-enhanced",
                "asked_meta_question": False,
                "response_shape": "analysis_then_patch",
                "first_tg_seconds": 1.0,
                "first_patch_seconds": 20.0,
                "first_file_change_seconds": 0.1,
                "post_edit_deliberation_seconds": 19.9,
                "tg_invocation_count": 1,
                "tg_seconds_total": 0.25,
                "changed_file_count": 1,
            },
        ]
    )

    assert summary["claude-baseline"]["record_count"] == 1
    assert summary["claude-baseline"]["response_shape_counts"] == {"analysis_then_patch": 1}
    assert summary["claude-enhanced"]["record_count"] == 2
    assert summary["claude-enhanced"]["meta_question_rate"] == 0.5
    assert summary["claude-enhanced"]["mean_first_tg_seconds"] == 1.25
    assert summary["claude-enhanced"]["mean_tg_invocation_count"] == 1.5
    assert summary["claude-enhanced"]["mean_post_edit_deliberation_seconds"] == 19.9


def test_run_claude_skill_ab_matrix_should_build_payload(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_matrix_payload_script", "benchmarks/run_claude_skill_ab_matrix.py")
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    driver_path.write_text(json.dumps({"records": [{"instance_id": "demo-1"}]}), encoding="utf-8")
    scenarios_path.write_text(json.dumps({"scenarios": [{"instance_id": "demo-1"}]}), encoding="utf-8")

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1", "prompt": "Fix it."}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [{"instance_id": "demo-1", "repo_fixture": "x"}],
    )

    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda record, **kwargs: (
            [
                {"instance_id": "demo-1", "system": "claude-baseline", "model_patch": "", "wall_clock_seconds": 10.0},
                {"instance_id": "demo-1", "system": "claude-enhanced", "model_patch": "diff --git a/x b/x", "wall_clock_seconds": 20.0},
            ],
            [
                {"instance_id": "demo-1", "system": "claude-baseline", "response_shape": "analysis_only", "asked_meta_question": False, "tg_invocation_count": 0, "tg_seconds_total": 0.0, "changed_file_count": 0, "first_tg_seconds": None, "first_patch_seconds": None, "first_file_change_seconds": None, "post_edit_deliberation_seconds": None},
                {"instance_id": "demo-1", "system": "claude-enhanced", "response_shape": "analysis_then_patch", "asked_meta_question": False, "tg_invocation_count": 1, "tg_seconds_total": 0.1, "changed_file_count": 1, "first_tg_seconds": 0.5, "first_patch_seconds": 5.0, "first_file_change_seconds": 0.1, "post_edit_deliberation_seconds": 4.9},
            ],
        ),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": "demo-1",
            "system": str(prediction["system"]),
            "patch_applied": prediction["system"] == "claude-enhanced",
            "validation_passed": prediction["system"] == "claude-enhanced",
            "primary_file_hit": float(prediction["system"] == "claude-enhanced"),
            "primary_span_hit": float(prediction["system"] == "claude-enhanced"),
        },
    )

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=1,
        output_contracts=["standard"],
        task_contracts=["engage"],
    )

    assert payload["artifact"] == "claude_skill_ab_matrix"
    assert payload["experiment_count"] == 1
    experiment = payload["experiments"][0]
    assert experiment["name"] == "output-standard__task-engage"
    assert experiment["trace_summary"]["claude-enhanced"]["mean_first_tg_seconds"] == 0.5
    assert experiment["bakeoff_summary"]["scenario_count"] == 2
    assert experiment["system_score_summary"]["claude-enhanced"]["mean_patch_applied_rate"] == 1.0


def test_run_claude_skill_ab_matrix_should_support_partial_and_resume(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_matrix_resume_script", "benchmarks/run_claude_skill_ab_matrix.py")
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(
        json.dumps({"records": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1", "prompt": "Fix it."}, {"instance_id": "demo-2", "prompt": "Fix it."}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [{"instance_id": "demo-1", "repo_fixture": "x"}, {"instance_id": "demo-2", "repo_fixture": "x"}],
    )

    seen: list[tuple[str, str]] = []

    def _fake_build_payload(*_args, **kwargs):
        raise AssertionError("_fake_build_payload should not be used")

    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda record, **kwargs: (
            seen.append(str(record["instance_id"])) or [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "model_patch": "diff --git a/x b/x",
                    "wall_clock_seconds": 20.0,
                }
            ],
            [
                {
                    "instance_id": str(record["instance_id"]),
                    "system": "claude-enhanced",
                    "response_shape": "analysis_then_patch",
                    "asked_meta_question": False,
                    "tg_invocation_count": 0,
                    "tg_seconds_total": 0.0,
                    "changed_file_count": 1,
                    "first_tg_seconds": None,
                    "first_patch_seconds": 5.0,
                    "first_file_change_seconds": 0.1,
                    "post_edit_deliberation_seconds": 4.9,
                }
            ],
        ),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {
            "instance_id": str(prediction["instance_id"]),
            "system": str(prediction["system"]),
            "patch_applied": True,
            "validation_passed": True,
            "primary_file_hit": 1.0,
            "primary_span_hit": 1.0,
        },
    )

    partial = module.build_partial_payload([])
    partial["experiments"].append(
        {
            "name": "output-standard__task-standard",
            "enhanced_output_contract": "standard",
            "enhanced_task_contract": "standard",
            "prediction_records": [{"instance_id": "demo-1", "system": "claude-enhanced", "model_patch": "diff --git a/x b/x"}],
            "trace_records": [{"instance_id": "demo-1", "system": "claude-enhanced", "response_shape": "analysis_then_patch"}],
            "bakeoff_rows": [{"instance_id": "demo-1", "system": "claude-enhanced", "patch_applied": True, "validation_passed": True}],
            "prediction_record_count": 1,
            "trace_record_count": 1,
            "trace_summary": {"claude-enhanced": {"meta_question_rate": 1.0}},
            "bakeoff_summary": {"scenario_count": 1},
            "system_score_summary": {"claude-enhanced": {"mean_patch_applied_rate": 1.0}},
        }
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="sonnet",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=2,
        output_contracts=["standard"],
        task_contracts=["standard"],
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    assert payload["experiment_count"] == 1
    assert [experiment["name"] for experiment in payload["experiments"]] == [
        "output-standard__task-standard",
    ]


def test_run_claude_skill_ab_matrix_should_write_checkpoint_per_experiment(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_matrix_checkpoint_script", "benchmarks/run_claude_skill_ab_matrix.py")
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(json.dumps({"records": [{"instance_id": "demo-1"}]}), encoding="utf-8")
    scenarios_path.write_text(json.dumps({"scenarios": [{"instance_id": "demo-1"}]}), encoding="utf-8")

    monkeypatch.setattr(module.ab_runner, "load_driver_payload", lambda path: {"records": [{"instance_id": "demo-1"}]})
    monkeypatch.setattr(module.patch_bakeoff, "load_patch_scenarios", lambda path: [{"instance_id": "demo-1"}])
    monkeypatch.setattr(
        module.ab_runner,
        "run_ab_record",
        lambda *_args, **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "evaluate_prediction",
        lambda scenario, prediction: {"instance_id": "demo-1", "system": "claude-enhanced", "patch_applied": True, "validation_passed": True},
    )

    writes: list[int] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append(int(payload["experiment_count"]))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=1,
        output_contracts=["standard", "terse"],
        task_contracts=["standard"],
        output_path=output_path,
        resume=False,
    )

    assert payload["experiment_count"] == 2
    assert writes == [1, 2]


def test_run_claude_skill_ab_matrix_should_checkpoint_per_record_and_resume(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_matrix_record_resume_script", "benchmarks/run_claude_skill_ab_matrix.py")
    driver_path = tmp_path / "driver.json"
    scenarios_path = tmp_path / "scenarios.json"
    output_path = tmp_path / "matrix.json"
    driver_path.write_text(
        json.dumps(
            {
                "records": [
                    {"instance_id": "demo-1"},
                    {"instance_id": "demo-2"},
                ]
            }
        ),
        encoding="utf-8",
    )
    scenarios_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {"instance_id": "demo-1"},
                    {"instance_id": "demo-2"},
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.ab_runner,
        "load_driver_payload",
        lambda path: {"records": [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}]},
    )
    monkeypatch.setattr(
        module.patch_bakeoff,
        "load_patch_scenarios",
        lambda path: [{"instance_id": "demo-1"}, {"instance_id": "demo-2"}],
    )

    seen: list[str] = []

    def _fake_run_ab_record(record, **_kwargs):
        seen.append(str(record["instance_id"]))
        return (
            [{"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "model_patch": "diff --git a/x b/x"}],
            [{"instance_id": str(record["instance_id"]), "system": "claude-enhanced", "response_shape": "analysis_then_patch"}],
        )

    monkeypatch.setattr(module.ab_runner, "run_ab_record", _fake_run_ab_record)

    def _fake_evaluate(scenario, prediction):
        return {
            "instance_id": str(scenario["instance_id"]),
            "system": str(prediction["system"]),
            "patch_applied": True,
            "validation_passed": True,
            "primary_file_hit": 1.0,
            "primary_span_hit": 1.0,
        }

    monkeypatch.setattr(module.patch_bakeoff, "evaluate_prediction", _fake_evaluate)

    writes: list[tuple[int, int]] = []

    def _fake_write_json(path, payload):
        if Path(path) == output_path:
            writes.append((int(payload["experiment_count"]), len(payload["experiments"][0]["prediction_records"])))

    monkeypatch.setattr(module, "write_json", _fake_write_json)

    partial = module.build_partial_payload(
        [
            {
                "name": "output-standard__task-standard",
                "enhanced_output_contract": "standard",
                "enhanced_task_contract": "standard",
                "prediction_records": [{"instance_id": "demo-1", "system": "claude-enhanced", "model_patch": "diff --git a/x b/x"}],
                "trace_records": [{"instance_id": "demo-1", "system": "claude-enhanced", "response_shape": "analysis_then_patch"}],
                "bakeoff_rows": [{"instance_id": "demo-1", "system": "claude-enhanced", "patch_applied": True, "validation_passed": True}],
                "prediction_record_count": 1,
                "trace_record_count": 1,
                "trace_summary": {"claude-enhanced": {"record_count": 1}},
                "bakeoff_summary": {"scenario_count": 1},
                "system_score_summary": {"claude-enhanced": {"record_count": 1}},
            }
        ]
    )
    output_path.write_text(json.dumps(partial), encoding="utf-8")

    payload = module.build_matrix_payload(
        input_path=driver_path,
        scenarios_path=scenarios_path,
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=30,
        skill_dir=tmp_path / "skill",
        work_root=tmp_path / "work",
        limit=2,
        output_contracts=["standard"],
        task_contracts=["standard"],
        output_path=output_path,
        resume=True,
    )

    assert seen == ["demo-2"]
    experiment = payload["experiments"][0]
    assert [row["instance_id"] for row in experiment["prediction_records"]] == ["demo-1", "demo-2"]
    assert experiment["prediction_record_count"] == 2
    assert writes == [(1, 2)]


def test_render_claude_skill_ab_matrix_should_render_markdown(tmp_path):
    module = _load_script_module("render_claude_skill_ab_matrix_script", "benchmarks/render_claude_skill_ab_matrix.py")
    payload_path = tmp_path / "matrix.json"
    payload_path.write_text(
        json.dumps(
            {
                "artifact": "claude_skill_ab_matrix",
                "experiments": [
                    {
                        "name": "output-standard__task-standard",
                        "enhanced_output_contract": "standard",
                        "enhanced_task_contract": "standard",
                        "system_score_summary": {
                            "claude-enhanced": {
                                "mean_patch_applied_rate": 0.0,
                                "mean_validation_pass_rate": 0.0,
                            }
                        },
                        "trace_summary": {
                            "claude-enhanced": {
                                "meta_question_rate": 1.0,
                                "mean_post_edit_deliberation_seconds": None,
                                "mean_first_tg_seconds": None,
                            }
                        },
                    },
                    {
                        "name": "output-terse__task-standard",
                        "enhanced_output_contract": "terse",
                        "enhanced_task_contract": "standard",
                        "system_score_summary": {
                            "claude-enhanced": {
                                "mean_patch_applied_rate": 1.0,
                                "mean_validation_pass_rate": 1.0,
                            }
                        },
                        "trace_summary": {
                            "claude-enhanced": {
                                "meta_question_rate": 0.0,
                                "mean_post_edit_deliberation_seconds": 41.545078,
                                "mean_first_tg_seconds": None,
                            }
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    markdown = module.render_markdown([payload_path])

    assert "# Claude Skill A/B Matrix" in markdown
    assert "output-terse__task-standard" in markdown
    assert "Recommended Next Default Probe" in markdown
    assert "meta_question_rate=`0.0`" in markdown


def test_run_claude_skill_ab_should_load_tg_trace_records(tmp_path):
    module = _load_script_module("run_claude_skill_ab_trace_log_script", "benchmarks/run_claude_skill_ab.py")
    log_path = tmp_path / "tg_trace.jsonl"
    log_path.write_text(
        '\n'.join(
            [
                '{"argv":["tg","defs","Demo"],"exit_code":0,"duration_seconds":0.5}',
                '{"argv":["tg","refs","Demo"],"exit_code":0,"duration_seconds":1.25}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )

    records = module.load_tg_trace_records(log_path)

    assert len(records) == 2
    assert records[0]["argv"] == ["tg", "defs", "Demo"]
    assert records[1]["duration_seconds"] == 1.25


def test_run_claude_skill_ab_should_omit_model_flag_when_model_is_empty(monkeypatch, tmp_path):
    module = _load_script_module("run_claude_skill_ab_model_script", "benchmarks/run_claude_skill_ab.py")
    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("ok", "")

    monkeypatch.setattr(module, "resolve_claude_binary", lambda: "claude")
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append(list(command)) or FakeProc(),
    )

    module._run_claude_command(
        tmp_path,
        "Say hi in one word.",
        model="",
        permission_mode="bypassPermissions",
        timeout_seconds=5,
    )

    assert "--model" not in calls[0]


def test_run_claude_skill_ab_default_trace_output_path():
    module = _load_script_module("run_claude_skill_ab_trace_path_script", "benchmarks/run_claude_skill_ab.py")

    trace_path = module.default_trace_output_path(Path("C:/tmp/result.json"))

    assert trace_path == Path("C:/tmp/result_trace.json")


def test_tensor_grep_claude_skill_should_require_non_interactive_action():
    skill_text = Path(".claude/skills/tensor-grep/SKILL.md").read_text(encoding="utf-8")

    assert "do not ask for confirmation" in skill_text
    assert "make the change directly" in skill_text
    assert "want me to apply this?" in skill_text


def test_run_editor_profiling_should_pass_provider_to_blast_radius(monkeypatch, tmp_path):
    module = _load_script_module("run_editor_profiling_provider_script", "benchmarks/run_editor_profiling.py")
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        module.repo_map,
        "build_symbol_blast_radius_render",
        lambda symbol, path, max_depth=3, max_files=6, max_sources=6, profile=True, semantic_provider="native": captured.update(
            {"provider": semantic_provider}
        )
        or {
            "_profiling": {"total_elapsed_s": 0.2, "breakdown_pct": {}, "phases": []},
            "files": [],
            "tests": [],
            "token_estimate": 0,
            "truncated": False,
        },
    )

    row = module.benchmark_blast_radius_fixture(
        {"root": str(repo_root), "name": "demo", "target_symbol": "create_invoice", "file_count": 1},
        repeats=1,
        provider="hybrid",
    )

    assert captured["provider"] == "hybrid"
    assert row["semantic_provider"] == "hybrid"


def test_run_codex_competitor_eval_should_retry_without_schema_when_first_result_is_empty(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_codex_competitor_eval_retry_script", "benchmarks/run_codex_competitor_eval.py"
    )
    scenario = {
        "id": "demo",
        "language": "python",
        "repo_fixture": str(tmp_path),
        "query_or_symbol": "symbol",
        "mode": "blast-radius",
    }
    monkeypatch.setattr(module, "resolve_codex_binary", lambda: "codex")
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        command = list(args[0])
        calls.append(command)
        if "--output-schema" in command:
            stdout = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "demo"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": json.dumps(
                                    {
                                        "actual_primary_file": None,
                                        "actual_primary_span": None,
                                        "actual_dependent_files": [],
                                        "actual_suggested_edit_files": [],
                                        "actual_test_files": [],
                                        "actual_validation_commands": [],
                                        "context_token_count": 0,
                                        "notes": "Awaiting code-edit task to plan against.",
                                    }
                                ),
                            },
                        }
                    ),
                ]
            )
        else:
            stdout = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": "demo"}),
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": json.dumps(
                                    {
                                        "actual_primary_file": "a.py",
                                        "actual_primary_span": {"start_line": 1, "end_line": 2},
                                        "actual_dependent_files": [],
                                        "actual_suggested_edit_files": [],
                                        "actual_test_files": [],
                                        "actual_validation_commands": ["pytest -q"],
                                        "context_token_count": 123,
                                        "notes": "ok",
                                    }
                                ),
                            },
                        }
                    ),
                ]
            )
        return type("Proc", (), {"stdout": stdout})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    record = module.run_codex_scenario(scenario, model="gpt-5-codex", timeout_seconds=30)

    assert record["actual_primary_file"] == "a.py"
    assert len(calls) == 2
    assert any("--output-schema" in command for command in calls)


def test_run_codex_competitor_eval_should_normalize_string_primary_span():
    module = _load_script_module(
        "run_codex_competitor_eval_span_script", "benchmarks/run_codex_competitor_eval.py"
    )

    record = module._normalize_primary_span(
        {
            "actual_primary_file": None,
            "actual_primary_span": "src/pkg/mod.py:10-14",
        }
    )

    assert record["actual_primary_file"] == "src/pkg/mod.py"
    assert record["actual_primary_span"] == {"start_line": 10, "end_line": 14}


def test_run_copilot_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_copilot_competitor_eval_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": [],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_copilot_binary", lambda: "copilot")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": "● "
                + json.dumps(
                    {
                        "actual_primary_file": "a.py",
                        "actual_primary_span": {"start_line": 1, "end_line": 2},
                        "actual_dependent_files": [],
                        "actual_suggested_edit_files": [],
                        "actual_test_files": [],
                        "actual_validation_commands": ["pytest -q"],
                        "context_token_count": 123,
                        "notes": "ok",
                    }
                )
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gpt-5.2")

    assert payload["artifact"] == "copilot_competitor_eval"
    assert payload["suite"] == "run_copilot_competitor_eval"
    assert payload["records"][0]["system"] == "copilot"
    assert payload["records"][0]["actual_primary_file"] == "a.py"


def test_run_copilot_competitor_eval_should_cleanup_ephemeral_agents_file(tmp_path):
    module = _load_script_module(
        "run_copilot_competitor_eval_cleanup_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    agents_path = tmp_path / "AGENTS.md"

    with module._ephemeral_repo_instructions(tmp_path):
        assert agents_path.exists()

    assert not agents_path.exists()


def test_run_copilot_competitor_eval_should_parse_wrapped_final_json():
    module = _load_script_module(
        "run_copilot_competitor_eval_wrapped_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    stdout = "\n".join(
        [
            "● Planning the answer first.",
            "",
            '● {"actual_primary_file":"a.py","actual_primary_span":{"start_li',
            '  ne":1,"end_line":2},"actual_dependent_files":[],"actual_suggested_',
            '  edit_files":[],"actual_test_files":[],"actual_validation_commands":[',
            '  "pytest -q"],"context_token_count":123,"notes":"ok"}',
            "",
        ]
    )

    extracted = module._extract_text_from_copilot_output(stdout)

    assert json.loads(extracted)["actual_primary_file"] == "a.py"


def test_run_copilot_competitor_eval_should_parse_fenced_json_from_mixed_output():
    module = _load_script_module(
        "run_copilot_competitor_eval_fenced_script", "benchmarks/run_copilot_competitor_eval.py"
    )
    stdout = "\n".join(
        [
            "Analyzing repository...",
            "I found the likely target below.",
            "```json",
            '{"actual_primary_file":"b.py","actual_primary_span":{"start_line":10,"end_line":12},"actual_dependent_files":[],"actual_suggested_edit_files":[],"actual_test_files":[],"actual_validation_commands":["pytest -q"],"context_token_count":321,"notes":"ok"}',
            "```",
        ]
    )

    extracted = module._extract_text_from_copilot_output(stdout)

    assert json.loads(extracted)["actual_primary_file"] == "b.py"


def test_run_gemini_competitor_eval_should_build_records_from_scenarios(tmp_path, monkeypatch):
    module = _load_script_module(
        "run_gemini_competitor_eval_script", "benchmarks/run_gemini_competitor_eval.py"
    )
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "demo",
                        "language": "python",
                        "category": "demo",
                        "description": "demo",
                        "repo_fixture": str(tmp_path),
                        "query_or_symbol": "symbol",
                        "mode": "blast-radius",
                        "expected_primary_file": "a.py",
                        "expected_primary_span": {"start_line": 1, "end_line": 2},
                        "expected_dependent_files": [],
                        "expected_suggested_edit_files": [],
                        "expected_test_files": [],
                        "expected_validation_commands_contain": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "resolve_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Proc",
            (),
            {
                "stdout": json.dumps(
                    {
                        "session_id": "demo",
                        "response": json.dumps(
                            {
                                "actual_primary_file": "a.py",
                                "actual_primary_span": {"start_line": 1, "end_line": 2},
                                "actual_dependent_files": [],
                                "actual_suggested_edit_files": [],
                                "actual_test_files": [],
                                "actual_validation_commands": ["pytest -q"],
                                "context_token_count": 123,
                                "notes": "ok",
                            }
                        ),
                        "stats": {},
                    }
                )
            },
        )(),
    )

    payload = module.build_payload(scenario_pack, model="gemini-2.5-flash")

    assert payload["artifact"] == "gemini_competitor_eval"
    assert payload["suite"] == "run_gemini_competitor_eval"
    assert payload["records"][0]["system"] == "gemini-cli"
    assert payload["records"][0]["actual_primary_file"] == "a.py"
