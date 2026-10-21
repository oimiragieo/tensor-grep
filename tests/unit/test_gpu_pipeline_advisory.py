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
