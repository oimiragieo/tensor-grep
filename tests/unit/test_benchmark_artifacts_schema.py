import json
from pathlib import Path

import pytest

ARTIFACT_SCHEMAS: dict[str, tuple[str, ...]] = {
    "bench_run_benchmarks.json": (
        "artifact",
        "environment",
        "generated_at_epoch_s",
        "rows",
        "suite",
    ),
    "bench_run_native_cpu_benchmarks.json": (
        "artifact",
        "environment",
        "passed",
        "rows",
        "suite",
        "thresholds",
    ),
    "bench_hot_query_benchmarks.json": (
        "artifact",
        "environment",
        "generated_at_epoch_s",
        "no_regressions",
        "rows",
        "suite",
    ),
    "bench_run_ast_benchmarks.json": (
        "artifact",
        "environment",
        "generated_at_epoch_s",
        "passed",
        "ratio",
        "suite",
    ),
    "bench_ast_multilang.json": (
        "artifact",
        "environment",
        "passed",
        "rows",
        "suite",
        "thresholds",
    ),
    "bench_ast_rewrite.json": (
        "artifact",
        "environment",
        "passed",
        "phase_timings_s",
        "suite",
    ),
    "bench_run_ast_workflow_benchmarks.json": (
        "artifact",
        "environment",
        "rows",
        "suite",
    ),
    "bench_run_gpu_benchmarks.json": (
        "artifact",
        "devices",
        "environment",
        "rows",
        "suite",
        "timing_backend",
    ),
    "bench_run_gpu_native_benchmarks.json": (
        "artifact",
        "corpus_sizes",
        "environment",
        "rows",
        "suite",
    ),
    "bench_harness_loop.json": (
        "all_passed",
        "artifact",
        "environment",
        "phase_medians_s",
        "rows",
        "suite",
    ),
    "bench_index_scaling.json": (
        "artifact",
        "environment",
        "passed",
        "rows",
        "scales",
        "suite",
    ),
}


def test_benchmark_artifacts_should_exist_and_match_stable_top_level_shapes() -> None:
    artifacts_dir = Path("artifacts")
    if not artifacts_dir.is_dir():
        pytest.skip("generated benchmark artifacts are optional in a clean checkout")

    for file_name, required_keys in ARTIFACT_SCHEMAS.items():
        path = artifacts_dir / file_name
        if not path.exists():
            pytest.skip(f"generated benchmark artifact missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))

        for key in required_keys:
            assert key in payload, f"{file_name} missing required key {key!r}"

        environment = payload.get("environment")
        if environment is not None:
            assert isinstance(environment, dict), f"{file_name} environment must be an object"
            assert "platform" in environment, f"{file_name} environment missing platform"
            assert "machine" in environment, f"{file_name} environment missing machine"

        if "rows" in payload:
            assert isinstance(payload["rows"], list), f"{file_name} rows must be a list"
            assert payload["rows"], f"{file_name} rows must not be empty"
