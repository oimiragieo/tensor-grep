from pathlib import Path


def test_benchmark_docs_should_include_local_dependency_setup_and_triton_note() -> None:
    doc = Path("docs/benchmarks.md").read_text(encoding="utf-8")
    assert "uv sync --extra dev --extra ast" in doc
    assert "uv sync --extra dev --extra bench --extra nlp" in doc
    assert "Triton" in doc
    assert "cyBERT" in doc


def test_benchmark_docs_should_publish_the_current_benchmark_matrix() -> None:
    doc = Path("docs/benchmarks.md").read_text(encoding="utf-8")

    assert "## Benchmark Matrix" in doc
    assert "run_benchmarks.py" in doc
    assert "run_native_cpu_benchmarks.py" in doc
    assert "run_hot_query_benchmarks.py" in doc
    assert "run_ast_benchmarks.py" in doc
    assert "run_ast_multilang_benchmarks.py" in doc
    assert "run_ast_rewrite_benchmarks.py" in doc
    assert "run_ast_workflow_benchmarks.py" in doc
    assert "run_gpu_benchmarks.py" in doc
    assert "run_gpu_native_benchmarks.py" in doc
    assert "run_harness_loop_benchmark.py" in doc
    assert "run_index_scaling_benchmark.py" in doc
    assert "artifacts/bench_run_benchmarks.json" in doc
    assert "artifacts/bench_run_native_cpu_benchmarks.json" in doc
    assert "artifacts/bench_hot_query_benchmarks.json" in doc
    assert "artifacts/bench_run_ast_benchmarks.json" in doc
    assert "artifacts/bench_ast_multilang.json" in doc
    assert "artifacts/bench_ast_rewrite.json" in doc
    assert "artifacts/bench_run_ast_workflow_benchmarks.json" in doc
    assert "artifacts/bench_run_gpu_benchmarks.json" in doc
    assert "artifacts/bench_run_gpu_native_benchmarks.json" in doc
    assert "artifacts/bench_harness_loop.json" in doc
    assert "artifacts/bench_index_scaling.json" in doc


def test_benchmark_docs_should_describe_artifact_and_baseline_governance() -> None:
    doc = Path("docs/benchmarks.md").read_text(encoding="utf-8")

    assert "## Artifact Conventions" in doc
    assert "## Acceptance Rules" in doc
    assert "suite" in doc
    assert "artifact" in doc
    assert "environment" in doc
    assert "generated_at_epoch_s" in doc
    assert "Do not update benchmark docs or claims" in doc
    assert "Gate (`<= 1.1`)" in doc
    assert "max_ratio_tg_vs_sg" in doc
