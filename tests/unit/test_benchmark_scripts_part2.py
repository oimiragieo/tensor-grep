import importlib.util
import json
import subprocess
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
    "benchmarks/build_attempt_ledger.py",
    "benchmarks/run_gpu_benchmarks.py",
    "benchmarks/run_gpu_native_benchmarks.py",
    "benchmarks/run_harness_loop_benchmark.py",
    "benchmarks/run_ast_parity_check.py",
    "benchmarks/run_compat_checks.py",
    "benchmarks/run_index_scaling_benchmark.py",
    "benchmarks/run_context_render_benchmarks.py",
    "benchmarks/run_blast_radius_benchmarks.py",
    "benchmarks/run_provider_navigation_bakeoff.py",
    "benchmarks/run_session_benchmarks.py",
    "benchmarks/run_external_eval.py",
    "benchmarks/analyze_external_profiling.py",
    "benchmarks/normalize_competitor_eval.py",
    "benchmarks/render_patch_scorecard.py",
    "benchmarks/render_provider_navigation_scorecard.py",
    "benchmarks/render_world_class_report.py",
    "benchmarks/run_patch_bakeoff.py",
    "benchmarks/build_attempt_ledger.py",
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


def _passing_native_gpu_scale_summary(module):
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.0},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.5,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.4},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.6,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": size_label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    return module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )


def _native_gpu_scale_summary_with_speed_failure(module):
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.0},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 2.0,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.3},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 2.4,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": size_label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    return module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )


def _passing_many_pattern_payload(module):
    patterns = list(module.DEFAULT_CORRECTNESS_PATTERNS)
    correctness_check = {
        "status": "PASS",
        "matches_equal": True,
        "files_equal": True,
        "rg_matches_equal": True,
        "rg_files_equal": True,
        "rg_match_identity_equal": True,
    }
    payload = {
        "status": "PASS",
        "workload_class": module.NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
        "patterns": patterns,
        "speedup_vs_cpu": 2.0,
        "speedup_vs_rg_multi_pattern": 1.5,
        "gpu_stats": {
            "pipeline": {
                "pattern_count": len(patterns),
                "single_dispatch": True,
            }
        },
        "correctness_check": correctness_check,
    }
    payload["proof_gate"] = module.build_many_pattern_proof_gate(
        multi_pattern=payload,
        correctness_check=correctness_check,
    )
    return payload


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
                "name": "large_file_200mb_fixed_multi_pattern_no_match",
                "target": str(tmp_path / "large_fixture.log"),
                "pattern": "absent 001 | absent 002",
                "rg_time_s": 1.0,
                "tg_time_s": 1.9,
                "rg_samples_s": [1.0, 1.01, 0.99],
                "tg_samples_s": [1.9, 1.92, 1.88],
                "ratio_vs_rg": 1.9,
                "threshold_ratio": None,
                "threshold_pass": None,
                "gated": False,
                "require_tg_faster": False,
                "status": "DIAGNOSTIC",
                "counts_match": True,
            },
            {
                "name": "large_file_200mb_fixed_multi_pattern_count",
                "target": str(tmp_path / "large_fixture.log"),
                "pattern": "ERROR native cpu benchmark sentinel | absent 001",
                "rg_time_s": 1.0,
                "tg_time_s": 2.1,
                "rg_samples_s": [1.0, 1.01, 0.99],
                "tg_samples_s": [2.1, 2.12, 2.08],
                "ratio_vs_rg": 2.1,
                "threshold_ratio": None,
                "threshold_pass": None,
                "gated": False,
                "require_tg_faster": False,
                "status": "DIAGNOSTIC",
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
        "large_file_200mb_fixed_multi_pattern_no_match",
        "large_file_200mb_fixed_multi_pattern_count",
        "many_file_directory",
    ]
    assert [row["status"] for row in payload["rows"]] == [
        "PASS",
        "PASS",
        "PASS",
        "DIAGNOSTIC",
        "DIAGNOSTIC",
        "PASS",
    ]
    assert payload["rows"][0]["ratio_vs_rg"] == 1.04
    assert payload["rows"][1]["ratio_vs_rg"] == 1.12
    assert payload["rows"][2]["ratio_vs_rg"] == 0.92
    assert payload["rows"][3]["ratio_vs_rg"] == 1.9
    assert payload["rows"][4]["ratio_vs_rg"] == 2.1
    assert payload["thresholds"] == {
        "cold_standard_corpus_max_ratio_vs_rg": 1.05,
        "large_file_200mb_max_ratio_vs_rg": 1.15,
        "large_file_200mb_count_requires_tg_faster": True,
        "large_file_200mb_fixed_multi_pattern_rows_are_diagnostic": True,
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


def test_run_harness_loop_json_command_should_accept_exit_one_json(monkeypatch):
    module = _load_script_module(
        "run_harness_loop_benchmark_no_match",
        "benchmarks/run_harness_loop_benchmark.py",
    )

    def _fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["tg"],
            returncode=1,
            stdout=json.dumps({"total_matches": 0, "matches": []}),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    _elapsed_s, payload = module.run_json_command(["tg", "run", "--json", "pattern"])

    assert payload == {"total_matches": 0, "matches": []}


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
            "small": {
                "root": tmp_path / "small",
                "file_count": 12,
                "target_symbol": "create_invoice",
            },
            "medium": {
                "root": tmp_path / "medium",
                "file_count": 48,
                "target_symbol": "create_invoice",
            },
            "large": {
                "root": tmp_path / "large",
                "file_count": 128,
                "target_symbol": "create_invoice",
            },
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
            "medium": {
                "root": tmp_path / "medium",
                "file_count": 48,
                "target_symbol": "create_invoice",
            },
            "large": {
                "root": tmp_path / "large",
                "file_count": 128,
                "target_symbol": "create_invoice",
            },
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
            "medium": {
                "root": tmp_path / "medium",
                "file_count": 48,
                "target_symbol": "create_invoice",
            },
            "large": {
                "root": tmp_path / "large",
                "file_count": 128,
                "target_symbol": "create_invoice",
            },
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


def test_run_gpu_benchmarks_missing_binary_should_emit_gpu_proof_summary(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_benchmarks_script_missing_binary", "benchmarks/run_gpu_benchmarks.py"
    )
    output_path = tmp_path / "bench_gpu_scale_missing.json"
    missing_tg = tmp_path / "missing" / "tg.exe"

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_benchmarks.py",
            "--output",
            str(output_path),
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: missing_tg)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_gpu_sidecar_python", lambda raw=None: None)
    monkeypatch.setattr(module, "resolve_gpu_bench_data_dir", lambda: tmp_path / "gpu_bench_data")

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["native_gpu_unavailable"] is True
    assert payload["gpu_proof_summary"]["status"] == "unsupported"
    assert payload["gpu_proof_summary"]["public_gpu_proof"] is False
    assert payload["gpu_proof_summary"]["public_managed_promotion_ready"] is False
    assert "public_managed_gpu_proof_gate" not in payload
    assert "public_gpu_proof" not in payload
    assert "public_managed_promotion_ready" not in payload


def test_run_gpu_benchmarks_should_skip_before_corpus_generation_without_operational_gpu(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_benchmarks_script_no_operational_gpu", "benchmarks/run_gpu_benchmarks.py"
    )
    output_path = tmp_path / "bench_gpu_scale.json"
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")

    def _fail_generate_gpu_scale_corpus(*_args, **_kwargs):
        raise AssertionError("corpus generation should be skipped without operational GPUs")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_benchmarks.py",
            "--output",
            str(output_path),
            "--corpus-sizes",
            "1MB",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_gpu_sidecar_python", lambda raw=None: sidecar_python)
    monkeypatch.setattr(module, "resolve_gpu_bench_data_dir", lambda: tmp_path / "gpu_bench_data")
    monkeypatch.setattr(
        module,
        "probe_gpu_devices",
        lambda _sidecar_python: {
            "available": False,
            "torch_version": "2.6.0",
            "devices": [
                {
                    "device_id": 0,
                    "name": "NVIDIA GeForce RTX 5070",
                    "operational": False,
                    "error": "no kernel image is available for execution on the device",
                }
            ],
            "warnings": [],
        },
    )
    monkeypatch.setattr(module, "generate_gpu_scale_corpus", _fail_generate_gpu_scale_corpus)

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "SKIP"
    assert payload["skipped"] is True
    assert payload["rows"] == []
    assert payload["correctness_checks"] == []
    recommendation = payload["gpu_auto_recommendation"]
    assert recommendation["should_add_flag"] is False
    assert "no operational GPU" in recommendation["reason"]
    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["gpu_proof_summary"]["status"] == "unsupported"
    assert payload["gpu_proof_summary"]["public_gpu_proof"] is False
    assert "native_cuda_runtime_unsupported" in payload["gpu_proof_summary"]["blockers"]


def test_run_gpu_benchmarks_gpu_proof_summary_reports_local_promotion_ready():
    module = _load_script_module(
        "run_gpu_benchmarks_script_gpu_proof_summary", "benchmarks/run_gpu_benchmarks.py"
    )
    scale_summary = module.build_scale_gate_summary(
        devices=[
            {
                "device_id": 0,
                "operational": True,
                "tg_runtime_backend": "NativeGpuBackend",
                "tg_runtime_sidecar_used": False,
            }
        ],
        correctness_checks=[
            {
                "device_id": 0,
                "status": "PASS",
                "pattern": pattern,
                "corpus_size_label": size_label,
                "matches_equal": True,
                "files_equal": True,
            }
            for size_label in ("1GB", "5GB")
            for pattern in module.DEFAULT_CORRECTNESS_PATTERNS
        ],
        gpu_auto_recommendation={
            "should_add_flag": True,
            "reason": "GPU beat both baselines at every required scale.",
            "winning_rows": [],
        },
    )

    summary = module.build_gpu_proof_summary(scale_summary)

    assert summary["status"] == "local_promotion_ready"
    assert summary["local_native_gpu_proof"] is True
    assert summary["public_gpu_proof"] is False
    assert summary["blockers"] == []
    assert summary["next_action"] == "run-native-public-managed-proof-before-public-promotion"


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
