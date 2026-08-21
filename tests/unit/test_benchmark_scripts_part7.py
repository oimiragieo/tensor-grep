import importlib.util
import json
from pathlib import Path

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


def test_build_external_agent_patch_driver_scorecard_should_score_compactness_and_validation_fit(
    tmp_path,
):
    module = _load_script_module(
        "build_external_agent_patch_driver_scorecard_script",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    comparison_path = tmp_path / "comparison.json"
    comparison_path.write_text(
        json.dumps({
            "artifact": "external_agent_patch_driver_comparison",
            "systems": [
                {
                    "system": "gemini",
                    "primary_file": "glob.ts",
                    "follow_up_count": 5,
                    "parallel_read_group_count": 3,
                    "estimated_saved_read_steps": 2,
                    "validation_commands": ["uv run pytest -q"],
                },
                {
                    "system": "codex",
                    "primary_file": "fuzzy_file_search.rs",
                    "follow_up_count": 5,
                    "parallel_read_group_count": 3,
                    "estimated_saved_read_steps": 2,
                    "validation_commands": ["cargo test"],
                },
                {
                    "system": "wide",
                    "primary_file": "reader.py",
                    "follow_up_count": 9,
                    "parallel_read_group_count": 4,
                    "estimated_saved_read_steps": 5,
                    "validation_commands": ["pytest -q"],
                },
            ],
            "common_contract": {"next_action": "run patch system"},
        }),
        encoding="utf-8",
    )

    payload = module.build_scorecard_payload(module.load_comparison(comparison_path))

    assert payload["artifact"] == "external_agent_patch_driver_scorecard"
    assert payload["summary"]["system_count"] == 3
    assert payload["by_system"]["gemini"]["compactness_target_met"] is True
    assert payload["by_system"]["gemini"]["validation_fit"] == "weak"
    assert payload["by_system"]["gemini"]["parallel_read_reduction_score"] > 0.0
    assert payload["by_system"]["codex"]["validation_fit"] == "strong"
    assert payload["by_system"]["wide"]["compactness_score"] < 1.0
    assert payload["summary"]["mean_parallel_read_reduction_score"] > 0.0


def test_build_external_agent_patch_driver_scorecard_cli_should_write_output(tmp_path):
    module = _load_script_module(
        "build_external_agent_patch_driver_scorecard_cli_script",
        "benchmarks/build_external_agent_patch_driver_scorecard.py",
    )
    comparison_path = tmp_path / "comparison.json"
    output_path = tmp_path / "scorecard.json"
    comparison_path.write_text(
        json.dumps({
            "artifact": "external_agent_patch_driver_comparison",
            "systems": [
                {
                    "system": "codex",
                    "primary_file": "fuzzy_file_search.rs",
                    "follow_up_count": 5,
                    "parallel_read_group_count": 3,
                    "estimated_saved_read_steps": 2,
                    "validation_commands": ["cargo test"],
                }
            ],
            "common_contract": {"next_action": "run patch system"},
        }),
        encoding="utf-8",
    )

    exit_code = module.main(["--input", str(comparison_path), "--output", str(output_path)])

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert written["by_system"]["codex"]["validation_fit"] == "strong"


def test_run_cold_path_attribution_should_write_output(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_cold_path_attribution_script",
        "benchmarks/run_cold_path_attribution.py",
    )

    bench_dir = tmp_path / "bench_data_root"
    generated: list[tuple[str, int, int]] = []
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: bench_dir)
    monkeypatch.setattr(
        module,
        "generate_test_data",
        lambda directory, num_files, lines_per_file: generated.append((
            directory,
            num_files,
            lines_per_file,
        )),
    )
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda binary=None: (tmp_path / "tg.exe", "explicit_arg"),
    )

    def fake_build_tg_cmd(tg_args, *, binary=None, return_mode=False, launcher_mode="auto"):
        if launcher_mode == "python_module_launcher":
            return [
                str(tmp_path / "python.exe"),
                "-m",
                "tensor_grep",
                "search",
                *tg_args,
            ], "python_module_launcher"
        return [str(binary or tmp_path / "tg.exe"), "search", *tg_args], launcher_mode

    monkeypatch.setattr(module, "build_tg_benchmark_cmd_with_mode", fake_build_tg_cmd)
    timing_cmds: list[list[str]] = []
    monkeypatch.setattr(
        module,
        "collect_timing_samples",
        lambda cmd, *args, **kwargs: (
            timing_cmds.append(list(cmd)) or 0.123,
            [0.120, 0.123, 0.126],
        ),
    )

    def fake_run_cmd_capture(cmd, *, env_overrides=None):
        trace_path = Path(env_overrides["TG_STARTUP_TRACE_PATH"])
        trace_path.write_text(
            json.dumps({"phase": "startup", "marker": "trace-file"}),
            encoding="utf-8",
        )
        return 0, "ignored stdout"

    monkeypatch.setattr(module, "run_cmd_capture", fake_run_cmd_capture)

    output_path = tmp_path / "cold-path.json"
    exit_code = module.main(["--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["artifact"] == "bench_cold_path_attribution"
    assert payload["suite"] == "cold_path_attribution"
    assert payload["warnings"]
    assert "python_module" in payload["warnings"][0]
    assert payload["environment"]["tg_launcher_command_kinds"] == {
        "explicit_binary": "native_exe",
        "discovered_cli_binary": "native_exe",
        "python_module_launcher": "python_module",
    }
    assert {row["launcher_mode"] for row in payload["rows"]} == set(module.DEFAULT_LAUNCHER_MODES)
    assert payload["rows"][0]["name"] == "1. Simple String Match [explicit_binary]"
    assert payload["rows"][0]["tg_launcher_command_kind"] == "native_exe"
    assert payload["rows"][0]["warnings"] == []
    assert payload["rows"][0]["phase_trace"] == {"phase": "startup", "marker": "trace-file"}
    assert all(str(bench_dir) in cmd for cmd in timing_cmds)
    assert generated == [(str(bench_dir), 2, 2_000_000)]


def test_run_cold_path_attribution_should_keep_rg_baseline_per_scenario(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_cold_path_attribution_rg_script",
        "benchmarks/run_cold_path_attribution.py",
    )

    bench_dir = tmp_path / "bench_data_root"
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: bench_dir)
    monkeypatch.setattr(module, "generate_test_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda binary=None: (tmp_path / "tg.exe", "explicit_arg"),
    )
    timing_cmds: list[list[str]] = []
    monkeypatch.setattr(
        module,
        "collect_timing_samples",
        lambda cmd, *args, **kwargs: (
            timing_cmds.append(list(cmd)) or 0.200,
            [0.200, 0.201, 0.199],
        ),
    )

    def fake_run_cmd_capture(cmd, *, env_overrides=None):
        trace_path = Path(env_overrides["TG_STARTUP_TRACE_PATH"])
        trace_path.write_text(
            json.dumps({"phase": "startup", "marker": "trace-file"}),
            encoding="utf-8",
        )
        return 0, "ignored stdout"

    monkeypatch.setattr(module, "run_cmd_capture", fake_run_cmd_capture)

    output_path = tmp_path / "cold-path.json"
    module.main(["--output", str(output_path), "--launcher-mode", "explicit_binary"])
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert len(payload["rows"]) == len(module.SCENARIOS)
    for row in payload["rows"]:
        assert row["launcher_mode"] == "explicit_binary"
        assert row["name"].endswith("[explicit_binary]")
        assert row["phase_trace"] == {"phase": "startup", "marker": "trace-file"}
        assert row["rg_time_s"] == 0.200
    assert all(str(bench_dir) in cmd for cmd in timing_cmds)


def test_run_cold_path_attribution_should_drop_stale_trace_files(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_cold_path_attribution_stale_trace_script",
        "benchmarks/run_cold_path_attribution.py",
    )

    bench_dir = tmp_path / "bench_data_root"
    scenario = {
        "name": "1. Simple String Match",
        "rg_args": ["rg", "ERROR", "bench_data"],
        "tg_args": ["tg", "search", "ERROR", "bench_data"],
    }
    stale_trace = bench_dir / "1._simple_string_match-explicit_binary.json"
    stale_trace.parent.mkdir(parents=True, exist_ok=True)
    stale_trace.write_text(json.dumps({"phase": "startup", "marker": "stale"}), encoding="utf-8")

    monkeypatch.setattr(module, "SCENARIOS", [scenario])
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: bench_dir)
    monkeypatch.setattr(module, "generate_test_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda binary=None: (tmp_path / "tg.exe", "explicit_arg"),
    )
    monkeypatch.setattr(module, "collect_timing_samples", lambda *args, **kwargs: (0.1, [0.1]))
    monkeypatch.setattr(
        module, "run_cmd_capture", lambda *args, **kwargs: (0, "plain search stdout")
    )

    output_path = tmp_path / "cold-path.json"
    exit_code = module.main(["--output", str(output_path), "--launcher-mode", "explicit_binary"])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["rows"][0]["phase_trace"] is None


def test_run_cold_path_attribution_should_warn_for_non_native_launcher(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_cold_path_attribution_launcher_warning_script",
        "benchmarks/run_cold_path_attribution.py",
    )

    bench_dir = tmp_path / "bench_data_root"
    python_launcher = tmp_path / "python.exe"
    python_launcher.write_text("python\n", encoding="utf-8")
    monkeypatch.setattr(module.sys, "executable", str(python_launcher))
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
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: bench_dir)
    monkeypatch.setattr(module, "generate_test_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda binary=None: (tmp_path / "tg.exe", "explicit_arg"),
    )
    monkeypatch.setattr(module, "collect_timing_samples", lambda *args, **kwargs: (0.1, [0.1]))
    monkeypatch.setattr(module, "run_cmd_capture", lambda *args, **kwargs: (0, ""))

    output_path = tmp_path / "cold-path.json"
    exit_code = module.main([
        "--output",
        str(output_path),
        "--launcher-mode",
        "python_module_launcher",
    ])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["environment"]["tg_launcher_command_kinds"] == {
        "python_module_launcher": "python_module"
    }
    assert payload["warnings"]
    assert "python_module" in payload["warnings"][0]
    assert payload["rows"][0]["tg_launcher_command_kind"] == "python_module"
    assert payload["rows"][0]["warnings"] == payload["warnings"]


def test_run_cold_path_attribution_should_refuse_stale_binary_by_default(
    monkeypatch, tmp_path, capsys
):
    module = _load_script_module(
        "run_cold_path_attribution_stale_binary_script",
        "benchmarks/run_cold_path_attribution.py",
    )
    tg_binary = tmp_path / "repo" / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True, exist_ok=True)
    tg_binary.write_text("stale\n", encoding="utf-8")
    output_path = tmp_path / "cold-path.json"
    monkeypatch.setattr(
        module,
        "resolve_tg_binary_with_source",
        lambda binary=None: (tg_binary, "default_binary_path"),
    )
    monkeypatch.setattr(
        module,
        "benchmark_binary_warnings",
        lambda _binary: ["tensor-grep benchmark warning: stale in-tree native tg binary"],
    )

    exit_code = module.main(["--output", str(output_path)])

    assert exit_code == 2
    assert not output_path.exists()
    assert "refusing claim-quality benchmark" in capsys.readouterr().err


def test_run_benchmarks_should_record_host_provenance(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_benchmarks_script_host_provenance",
        "benchmarks/run_benchmarks.py",
    )
    bench_dir = tmp_path / "bench_data"
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_bench_data_dir", lambda: bench_dir)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module, "resolve_tg_binary_with_source", lambda binary=None: (tg_binary, "explicit_arg")
    )
    monkeypatch.setattr(module, "generate_test_data", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        module,
        "build_benchmark_scenarios",
        lambda **kwargs: [
            {
                "name": "1. Simple String Match",
                "rg_cmd": ["rg", "ERROR", "bench_data"],
                "tg_cmd": ["tg", "search", "ERROR", "bench_data"],
            }
        ],
    )
    monkeypatch.setattr(module, "run_cmd_timing", lambda *args, **kwargs: 0.1)
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *args, **kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(module, "compare_results", lambda *args, **kwargs: True)

    output_path = tmp_path / "bench_run.json"
    monkeypatch.setattr("sys.argv", ["run_benchmarks.py", "--output", str(output_path)])
    exit_code = module.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["benchmark_host_key"] == module.benchmark_host_key(payload["environment"])
    assert payload["host_provenance"]["benchmark_host_key"] == payload["benchmark_host_key"]
    assert payload["host_provenance"]["platform"] == payload["environment"]["platform"]


def test_run_cold_path_attribution_should_record_host_provenance(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_cold_path_attribution_script_host_provenance",
        "benchmarks/run_cold_path_attribution.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module, "resolve_tg_binary_with_source", lambda binary=None: (tg_binary, "explicit_arg")
    )
    monkeypatch.setattr(
        module, "collect_timing_samples", lambda *args, **kwargs: (0.1, [0.1, 0.1, 0.1])
    )
    monkeypatch.setattr(module, "run_cmd_capture", lambda *args, **kwargs: (0, ""))
    monkeypatch.setattr(
        module,
        "_scenario_commands",
        lambda **kwargs: (
            [
                {
                    "scenario": "1. Simple String Match",
                    "launcher_mode": "explicit_binary",
                    "resolved_launcher_mode": "explicit_binary",
                    "tg_launcher_command_kind": "native_exe",
                    "rg_time_s": 0.1,
                    "rg_samples_s": [0.1, 0.1, 0.1],
                    "tg_time_s": 0.1,
                    "tg_samples_s": [0.1, 0.1, 0.1],
                    "phase_trace": None,
                    "warnings": [],
                }
            ],
            {"explicit_binary": "native_exe"},
            [],
        ),
    )

    output_path = tmp_path / "bench_cold.json"
    exit_code = module.main(["--output", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["benchmark_host_key"] == module.benchmark_host_key(payload["environment"])
    assert payload["host_provenance"]["benchmark_host_key"] == payload["benchmark_host_key"]
    assert payload["host_provenance"]["tg_binary_source"] == "explicit_arg"
    assert payload["host_provenance"]["tg_launcher_command_kinds"] == {
        "explicit_binary": "native_exe"
    }
