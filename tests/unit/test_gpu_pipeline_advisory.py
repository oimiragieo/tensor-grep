"""GPU pipeline advisory behavior tests."""

from tests.unit.gpu_benchmark_test_helpers import _load_script_module


def test_gpu_pipeline_helpers_should_summarize_bottleneck_stage():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_summary", "benchmarks/run_gpu_benchmarks.py"
    )

    sample = module.extract_gpu_pipeline_breakdown(
        {
            "pipeline": {
                "host_file_read_time_ms": 1000.0,
                "host_preprocess_time_ms": 50.0,
                "host_to_pinned_copy_time_ms": 100.0,
                "transfer_time_ms": 200.0,
                "kernel_time_ms": 180.0,
                "cpu_staging_time_ms": 90.0,
                "wall_time_ms": 1500.0,
            }
        },
        source="scale_native_stats",
        source_label="1GB GPU 0 native stats",
        size_label="1GB",
        process_median_s=3.0,
    )

    assert sample["source"] == "scale_native_stats"
    assert sample["size_label"] == "1GB"
    assert sample["stage_times_ms"]["host_file_read"] == 1000.0
    assert sample["stage_times_ms"]["host_preprocess"] == 50.0
    assert sample["stage_times_ms"]["unattributed_process_or_host_tail"] == 350.0

    summary = module.summarize_gpu_pipeline_bottlenecks([sample])

    assert summary["status"] == "ADVISORY"
    assert summary["sample_count"] == 1
    assert summary["pipeline_sample_sources"] == ["scale_native_stats"]
    assert summary["dominant_stage"] == "host_file_read"
    assert summary["dominant_stage_share_pct"] > 45.0
    assert summary["stage_totals_ms"]["unattributed_process_or_host_tail"] == 350.0
    next_steps = module.build_gpu_readiness_next_steps(summary)
    assert next_steps[0]["target"] == "host_file_read"
    assert "before changing CUDA kernels" in next_steps[0]["action"]


def test_gpu_pipeline_helpers_should_report_not_available_without_samples():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_missing", "benchmarks/run_gpu_benchmarks.py"
    )

    summary = module.summarize_gpu_pipeline_bottlenecks([])

    assert summary == {
        "status": "NOT_AVAILABLE",
        "sample_count": 0,
        "pipeline_sample_sources": [],
        "dominant_stage": None,
        "dominant_stage_share_pct": None,
        "stage_totals_ms": {},
        "samples": [],
        "reason": "No native GPU pipeline samples were available.",
    }
    assert module.build_gpu_readiness_next_steps(summary) == []


def test_gpu_pipeline_next_steps_should_not_optimize_from_runtime_probe_only():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_runtime_only", "benchmarks/run_gpu_benchmarks.py"
    )
    sample = module.extract_gpu_pipeline_breakdown(
        {
            "pipeline": {
                "host_file_read_time_ms": 1.0,
                "transfer_time_ms": 2.0,
                "kernel_time_ms": 10.0,
                "wall_time_ms": 13.0,
            }
        },
        source="runtime_probe",
        source_label="small runtime probe",
    )

    summary = module.summarize_gpu_pipeline_bottlenecks([sample])
    next_steps = module.build_gpu_readiness_next_steps(summary)

    assert summary["status"] == "ADVISORY"
    assert summary["pipeline_sample_sources"] == ["runtime_probe"]
    assert next_steps[0]["target"] == "scale_native_stats"
    assert "actual-scale" in next_steps[0]["action"]


def test_run_gpu_native_runtime_probe_should_preserve_pipeline_metrics(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_probe_pipeline",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()

    def _fake_run_command(command, **_kwargs):
        stdout = (
            '{"routing_backend":"NativeGpuBackend",'
            '"routing_reason":"gpu-device-ids-explicit-native",'
            '"sidecar_used":false,'
            '"pipeline":{'
            '"cpu_staging_bytes":1234,'
            '"pageable_host_staging_bytes":0,'
            '"host_file_read_time_ms":1.25,'
            '"host_preprocess_time_ms":0.5,'
            '"host_to_pinned_copy_time_ms":0.0,'
            '"transfer_time_ms":2.0,'
            '"kernel_time_ms":3.0'
            "}}"
        )
        return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    result = module.probe_native_gpu_runtime_backend(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        pattern="gpu benchmark sentinel",
        device_id=0,
        env={},
        timeout_s=5,
    )

    assert result["status"] == "PASS"
    assert result["routing_backend"] == "NativeGpuBackend"
    assert result["sidecar_used"] is False
    assert result["pipeline"]["cpu_staging_bytes"] == 1234
    assert result["pipeline"]["host_to_pinned_copy_time_ms"] == 0.0


def test_run_gpu_native_pipeline_collector_should_prefer_actual_scale_stats():
    module = _load_script_module(
        "run_gpu_native_benchmarks_pipeline_collector",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    rows = [
        {
            "size_label": "1GB",
            "tg_gpu": {
                "native_stats": {"process_median_s": 3.0},
                "native_stats_pipeline": {
                    "host_file_read_time_ms": 1000.0,
                    "host_preprocess_time_ms": 50.0,
                    "transfer_time_ms": 100.0,
                    "kernel_time_ms": 90.0,
                    "wall_time_ms": 1500.0,
                },
                "runtime_probe_pipeline": {
                    "kernel_time_ms": 9999.0,
                    "wall_time_ms": 9999.0,
                },
            },
        }
    ]
    samples = module.collect_gpu_native_pipeline_samples(rows, {"enabled": False})
    summary = module.summarize_gpu_pipeline_bottlenecks(samples)

    assert len(samples) == 1
    assert samples[0]["source"] == "scale_native_stats"
    assert summary["pipeline_sample_sources"] == ["scale_native_stats"]
    assert summary["dominant_stage"] == "host_file_read"
    assert summary["stage_totals_ms"]["unattributed_process_or_host_tail"] == 450.0


def test_gpu_bottleneck_advisory_should_not_change_promotion_summary():
    module = _load_script_module(
        "run_gpu_benchmarks_advisory_not_promotion",
        "benchmarks/run_gpu_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 0.25},
            "tg_cpu": {"status": "PASS", "median_s": 0.24},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 9.2,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 0.28},
            "tg_cpu": {"status": "PASS", "median_s": 0.23},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 9.4,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
    ]
    correctness_checks = [
        {
            "device_id": 0,
            "corpus_size_label": size_label,
            "pattern": "gpu benchmark sentinel",
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    advisory_summary = module.summarize_gpu_pipeline_bottlenecks([
        module.extract_gpu_pipeline_breakdown(
            {
                "pipeline": {
                    "host_file_read_time_ms": 1000.0,
                    "kernel_time_ms": 1.0,
                    "wall_time_ms": 1001.0,
                }
            },
            source="scale_native_stats",
        )
    ])

    recommendation = module.analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=("gpu benchmark sentinel",),
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
        correctness_checks=correctness_checks,
        gpu_auto_recommendation=recommendation,
        correctness_patterns=("gpu benchmark sentinel",),
    )

    assert advisory_summary["status"] == "ADVISORY"
    assert recommendation["should_add_flag"] is False
    assert scale_summary["promotion_ready"] is False
