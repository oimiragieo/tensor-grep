import importlib.util
import json
import subprocess
import sys
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
    assert payload["public_managed_gpu_proof_gate"]["status"] == "NOT_REQUESTED"
    assert payload["public_managed_promotion_ready"] is False
    assert payload["public_gpu_proof"] is False
    assert payload["gpu_proof_summary"]["status"] == "unsupported"
    assert payload["gpu_proof_summary"]["public_managed_proof_gate_status"] == "NOT_REQUESTED"
    assert payload["gpu_proof_summary"]["public_gpu_proof"] is False


def test_run_gpu_native_benchmarks_public_managed_proof_requires_metadata(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_public_managed_proof",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    output_path = tmp_path / "bench_gpu_native_public.json"
    tg_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tg_binary.parent.mkdir(parents=True)
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_native_benchmarks.py",
            "--output",
            str(output_path),
            "--public-managed-proof",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda _binary: {
            "kind": "managed-native",
            "version_status": "matches",
            "expected_version": "1.12.34",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
        },
    )
    monkeypatch.setattr(
        module,
        "run_gpu_native_benchmarks",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "gpu_native_bench_data"),
            "corpus_sizes": [],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "crossover": {
                "exists": False,
                "first_gpu_faster_than_rg": None,
                "summary": "not relevant",
            },
            "scale_gate_summary": {
                **_passing_native_gpu_scale_summary(module),
                "summary": "Native CUDA correctness and speed gates passed.",
            },
            "advanced": {
                "enabled": True,
                "multi_pattern": _passing_many_pattern_payload(module),
            },
            "warnings": [],
            "errors": [],
        },
    )

    exit_code = module.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["public_managed_gpu_proof_gate"]["status"] == "PASS"
    assert payload["public_managed_promotion_ready"] is True
    assert payload["public_gpu_proof"] is True
    assert payload["gpu_proof_summary"]["status"] == "public_promotion_ready"
    assert payload["gpu_proof_summary"]["blockers"] == []
    assert payload["gpu_proof_summary"]["public_gpu_proof"] is True


def test_run_gpu_native_benchmarks_public_managed_proof_requires_many_pattern_gate(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_public_many_pattern_required",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    output_path = tmp_path / "bench_gpu_native_public_many_pattern_required.json"
    tg_binary = tmp_path / ".tensor-grep" / "bin" / "tg.exe"
    tg_binary.parent.mkdir(parents=True)
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_native_benchmarks.py",
            "--output",
            str(output_path),
            "--public-managed-proof",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda _binary: {
            "kind": "managed-native",
            "version_status": "matches",
            "expected_version": "1.12.34",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
        },
    )
    monkeypatch.setattr(
        module,
        "run_gpu_native_benchmarks",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "gpu_native_bench_data"),
            "corpus_sizes": [],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "crossover": {
                "exists": False,
                "first_gpu_faster_than_rg": None,
                "summary": "not relevant",
            },
            "scale_gate_summary": {
                **_passing_native_gpu_scale_summary(module),
                "summary": "Native CUDA route and correctness gates passed.",
            },
            "advanced": {"enabled": False},
            "warnings": [],
            "errors": [],
        },
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    gate = payload["public_managed_gpu_proof_gate"]
    assert gate["status"] == "FAIL"
    assert "many_pattern_proof_gate_missing" in gate["blockers"]
    assert payload["public_managed_promotion_ready"] is False
    assert payload["public_gpu_proof"] is False
    assert "many_pattern_proof_gate_missing" in payload["gpu_proof_summary"]["blockers"]


def test_run_gpu_native_benchmarks_public_managed_proof_fails_without_managed_metadata(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_native_benchmarks_script_public_managed_proof_fails",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    output_path = tmp_path / "bench_gpu_native_public_fail.json"
    tg_binary = tmp_path / "rust_core" / "target" / "release" / "tg.exe"
    tg_binary.parent.mkdir(parents=True)
    tg_binary.write_text("binary", encoding="utf-8")

    monkeypatch.setattr(
        "sys.argv",
        [
            "run_gpu_native_benchmarks.py",
            "--output",
            str(output_path),
            "--public-managed-proof",
        ],
    )
    monkeypatch.setattr(module, "resolve_tg_binary", lambda binary=None: tg_binary)
    monkeypatch.setattr(module, "resolve_rg_binary", lambda: "rg")
    monkeypatch.setattr(
        module,
        "inspect_native_tg_binary",
        lambda _binary: {
            "kind": "in-tree-release",
            "version_status": "matches",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_metadata_status": "present",
        },
    )
    monkeypatch.setattr(
        module,
        "run_gpu_native_benchmarks",
        lambda **_kwargs: {
            "bench_dir": str(tmp_path / "gpu_native_bench_data"),
            "corpus_sizes": [],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "crossover": {
                "exists": False,
                "first_gpu_faster_than_rg": None,
                "summary": "not relevant",
            },
            "scale_gate_summary": {
                **_passing_native_gpu_scale_summary(module),
                "summary": "Native CUDA correctness and speed gates passed.",
            },
            "advanced": {
                "enabled": True,
                "multi_pattern": _passing_many_pattern_payload(module),
            },
            "warnings": [],
            "errors": [],
        },
    )

    exit_code = module.main()

    assert exit_code == 1
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["public_managed_gpu_proof_gate"]["status"] == "FAIL"
    assert "not_managed_native_frontdoor" in payload["public_managed_gpu_proof_gate"]["blockers"]
    assert payload["public_managed_promotion_ready"] is False
    assert payload["public_gpu_proof"] is False
    assert payload["gpu_proof_summary"]["status"] == "public_promotion_blocked"
    assert "not_managed_native_frontdoor" in payload["gpu_proof_summary"]["blockers"]
    assert payload["gpu_proof_summary"]["local_native_gpu_proof"] is True
    assert payload["gpu_proof_summary"]["public_gpu_proof"] is False


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


def test_run_gpu_native_benchmarks_should_build_fair_rg_multi_pattern_command(tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_fair_rg_multi_pattern",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    corpus_dir = tmp_path / "corpus"
    patterns = ["ERROR timeout", "WARN retry", "Database connection timeout"]

    command = module.build_rg_multi_pattern_search_command("rg", patterns, corpus_dir)

    assert command == [
        "rg",
        "--no-ignore",
        "-F",
        "-e",
        "ERROR timeout",
        "-e",
        "WARN retry",
        "-e",
        "Database connection timeout",
        str(corpus_dir),
    ]


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


def test_run_hot_query_benchmarks_should_build_stringzilla_probe_script(tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_stringzilla_probe",
        "benchmarks/run_hot_query_benchmarks.py",
    )
    script_path = tmp_path / "stringzilla_probe.py"

    module.write_stringzilla_probe_script(script_path)

    text = script_path.read_text(encoding="utf-8")
    assert "StringZillaBackend" in text
    assert "missing_dependency" in text
    assert "SearchConfig(fixed_strings=True)" in text


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


def test_run_hot_query_benchmarks_should_skip_with_install_hint_when_stringzilla_is_missing(
    monkeypatch, tmp_path, capsys
):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_missing_stringzilla",
        "benchmarks/run_hot_query_benchmarks.py",
    )
    monkeypatch.setattr(module, "resolve_hot_bench_data_dir", lambda: tmp_path / "hot")
    monkeypatch.setattr(module, "_prepare_corpus", lambda data_dir: data_dir / "hot_corpus.log")
    monkeypatch.setattr(module, "write_cpu_probe_script", lambda _path: None)
    monkeypatch.setattr(module, "write_stringzilla_probe_script", lambda _path: None)

    monkeypatch.setattr(
        module,
        "_run_stringzilla_hot_query",
        lambda *_args, **_kwargs: {
            "name": "repeated_fixed_string",
            "status": "SKIP",
            "skip_reason": 'install with `uv pip install -e ".[bench]"`',
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
    monkeypatch.setattr("sys.argv", ["run_hot_query_benchmarks.py", "--output", str(output_path)])

    exit_code = module.main()

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "repeated_fixed_string" in captured.out
    assert "SKIP" in captured.out
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["no_regressions"] is True
    assert payload["rows"][0]["status"] == "SKIP"
    assert 'uv pip install -e ".[bench]"' in payload["rows"][0]["skip_reason"]


def test_run_hot_query_benchmarks_should_run_stringzilla_probe_in_subprocess(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_hot_query_benchmarks_script_stringzilla_subprocess",
        "benchmarks/run_hot_query_benchmarks.py",
    )
    probe_script = tmp_path / "stringzilla_probe.py"
    corpus_path = tmp_path / "corpus.log"
    cache_dir = tmp_path / "cache"
    captured: dict[str, object] = {}

    def _fake_check_output(cmd, *, text, env):
        captured.setdefault("calls", []).append((cmd, env))
        pattern = cmd[-1]
        if pattern == "ERROR timeout":
            return json.dumps({
                "available": True,
                "seconds": 0.4,
                "routing_reason": "stringzilla_fixed_strings_index",
                "matches": 2000,
            })
        return json.dumps({
            "available": True,
            "seconds": 0.01,
            "routing_reason": "stringzilla_fixed_strings_index_cache",
            "matches": 2000,
        })

    monkeypatch.setattr(module.subprocess, "check_output", _fake_check_output)

    payload = module._run_stringzilla_hot_query(corpus_path, cache_dir, probe_script)

    assert payload["first_s"] == 0.4
    assert payload["second_s"] == 0.01
    assert payload["second_reason"] == "stringzilla_fixed_strings_index_cache"
    calls = captured["calls"]
    assert len(calls) == 2
    assert calls[0][0][1] == str(probe_script)
    assert calls[0][1]["TENSOR_GREP_STRING_INDEX_DIR"] == str(cache_dir)


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


def test_check_regression_should_report_rg_comparator_drift(monkeypatch, tmp_path, capsys):
    module = _load_script_module(
        "check_regression_script_rg_drift", "benchmarks/check_regression.py"
    )
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    payload = {
        "suite": "run_benchmarks",
        "environment": {"platform": "windows", "machine": "amd64"},
        "rows": [{"name": "x", "tg_time_s": 1.0, "rg_time_s": 0.9}],
    }
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path.write_text(
        json.dumps({
            **payload,
            "rows": [{"name": "x", "tg_time_s": 1.0, "rg_time_s": 1.2}],
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
    stdout = capsys.readouterr().out

    assert exit_code == 0
    assert "rg_time_s" in stdout
    assert "drift" in stdout


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


def test_check_regression_should_allow_same_environment_explicit_baseline_for_candidate_gate(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "check_regression_script_same_env_candidate", "benchmarks/check_regression.py"
    )
    baseline_path = tmp_path / "base.json"
    current_path = tmp_path / "head.json"
    payload = {
        "suite": "run_benchmarks",
        "environment": {
            "platform": "windows",
            "machine": "amd64",
            "python_version": "3.12.12",
        },
        "rows": [{"name": "x", "tg_time_s": 1.00, "rg_time_s": 0.80}],
    }
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    current_path.write_text(
        json.dumps({
            **payload,
            "rows": [{"name": "x", "tg_time_s": 1.03, "rg_time_s": 0.81}],
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
            "--max-regression-pct",
            "5",
        ],
    )

    assert module.main() == 0


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


def test_run_external_eval_should_aggregate_manifest_packs(tmp_path):
    module = _load_script_module("run_external_eval_script", "benchmarks/run_external_eval.py")
    scenario_pack = tmp_path / "scenarios.json"
    scenario_pack.write_text(
        json.dumps({
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
        }),
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
        json.dumps({
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
        }),
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
        json.dumps({
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
        }),
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
    module = _load_script_module(
        "render_patch_scorecard_script", "benchmarks/render_patch_scorecard.py"
    )
    markdown = module.render_patch_scorecard([
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
    ])

    assert markdown.startswith("# Patch Evaluation Scorecard")
    assert "`copilot`" in markdown
    assert "`gemini-cli`" in markdown
    assert "timeout after 10s" in markdown
