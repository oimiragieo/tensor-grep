from __future__ import annotations

import tomllib
from pathlib import Path


def _pyproject_payload() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _optional_dependencies() -> dict[str, list[str]]:
    payload = _pyproject_payload()
    return payload["project"]["optional-dependencies"]


def _dependencies() -> list[str]:
    payload = _pyproject_payload()
    return payload["project"]["dependencies"]


def test_nlp_extra_should_use_http_triton_client_not_all() -> None:
    deps = _optional_dependencies()["nlp"]
    assert "transformers>=5.3.0" in deps  # CVE-2026-4372 fixed in 5.3.0
    assert "tritonclient[http]" in deps
    assert "tritonclient[all]" not in deps


def test_bench_extra_should_include_stringzilla_for_hot_query_benchmarks() -> None:
    deps = _optional_dependencies()["bench"]

    assert "stringzilla>=4.0" in deps


def test_ast_dev_bench_extras_include_tree_sitter_go_for_path_a_stage1() -> None:
    # PATH A Stage 1 (Go symbol graph, first language expansion beyond the original four):
    # tree-sitter-go must ship in every extra that already carries the other tree-sitter
    # grammar packages, or an --all-extras --locked export silently drops Go support.
    deps = _optional_dependencies()
    for extra_name in ("ast", "dev", "bench"):
        assert "tree-sitter-go" in deps[extra_name], f"tree-sitter-go missing from [{extra_name}]"


def test_ast_extra_pins_pygls_floor_matching_lsp_server_import() -> None:
    # cli/lsp_server.py imports `from pygls.lsp.server import LanguageServer`, a module path
    # that exists only in pygls 2.x (pygls 1.x has no `pygls.lsp.server` module at all -- its
    # LanguageServer lives at `pygls.server`). The `ast` extra's pygls floor must match what the
    # code actually requires, or `pip install "tensor-grep[ast]"` can resolve a pygls 1.x that
    # ImportErrors the moment `tg lsp` runs (found by the #663 Opus gate).
    deps = _optional_dependencies()["ast"]

    assert "pygls>=2.0" in deps
    assert "pygls>=1.3.0" not in deps


def test_semantic_extra_should_pin_model2vec_and_numpy_no_torch() -> None:
    deps = _optional_dependencies()["semantic"]

    assert "model2vec>=0.5" in deps
    assert "numpy>=1.26" in deps
    # The whole point of model2vec (vs onnx-MiniLM/transformers) is NO torch/GPU dependency.
    assert not any(dep.lower().startswith("torch") for dep in deps)


def test_rerank_extra_pins_onnxruntime_and_tokenizers_no_torch() -> None:
    deps = _optional_dependencies()["rerank"]

    assert "tensor-grep[semantic]" in deps  # the rerank stage sits on top of the RRF-fused pool
    assert "onnxruntime>=1.20" in deps
    assert "tokenizers>=0.21" in deps
    # The late-interaction rerank stage runs CPU ONNX inference (design doc "Inference") -- no
    # torch/transformers/PyLate at runtime, and never the GPU build of onnxruntime.
    assert not any(dep.lower().startswith("torch") for dep in deps)
    assert not any("onnxruntime-gpu" in dep.lower() for dep in deps)
    assert not any("pylate" in dep.lower() for dep in deps)


def test_bare_dependencies_should_not_carry_gpu_only_or_unconfigured_observability_deps() -> None:
    # The tests above only ever validate [project.optional-dependencies] -- this is how pyarrow
    # (GPU-only: CuDFBackend's zero-copy ingestion, gated behind `import cudf` succeeding first)
    # and opentelemetry-sdk/-exporter-otlp (unconfigured: no TracerProvider is ever set up, so
    # every `trace.get_tracer` call site is an ImportError-guarded no-op) drifted into the bare
    # [project.dependencies] list unnoticed, dragging grpcio+protobuf and pyarrow's native wheel
    # onto every non-GPU install. Pin the intent so they can't drift back in.
    deps = _dependencies()

    assert not any(dep.lower().startswith("pyarrow") for dep in deps), (
        "pyarrow is GPU-only -- it belongs in the [gpu] extra, not the bare dependency list"
    )
    assert not any(dep.lower().startswith("opentelemetry-sdk") for dep in deps), (
        "opentelemetry-sdk is unconfigured (no TracerProvider) -- must not be a bare dependency"
    )
    assert not any(dep.lower().startswith("opentelemetry-exporter-otlp") for dep in deps), (
        "opentelemetry-exporter-otlp is unconfigured (no TracerProvider) -- must not be a bare dependency"
    )
    # opentelemetry-api stays: it's the minimal no-op scaffold backing the 6 ImportError-guarded
    # `trace.get_tracer` call sites (cudf_backend.py x2, cybert_backend.py x2, pipeline.py,
    # cli/main.py), kept for future real instrumentation without re-adding the heavy SDK chain.
    assert any(dep.lower().startswith("opentelemetry-api") for dep in deps)

    # The gpu extra must carry pyarrow so the GPU zero-copy path still resolves it; gpu-win is
    # torch-only (no cudf), so the pyarrow zero-copy path never runs there and must stay absent.
    optional_deps = _optional_dependencies()
    assert any(dep.lower().startswith("pyarrow") for dep in optional_deps["gpu"])
    assert not any(dep.lower().startswith("pyarrow") for dep in optional_deps["gpu-win"])


def test_ruff_should_extend_default_excludes_for_repo_specific_bench_dirs() -> None:
    ruff_config = _pyproject_payload()["tool"]["ruff"]

    assert "exclude" not in ruff_config
    assert ruff_config["extend-exclude"] == [
        "bench_data",
        "bench_ast_data",
        "gpu_bench_data",
        "benchmarks/bench_data",
        "benchmarks/bench_ast_data",
        "benchmarks/gpu_bench_data",
        "benchmarks/external_repos",
        ".claude/skills",
    ]


def test_uv_cache_keys_should_include_rust_native_inputs_without_forced_reinstall() -> None:
    uv_config = _pyproject_payload()["tool"]["uv"]
    cache_keys = uv_config["cache-keys"]
    file_entries = {
        str(entry["file"])
        for entry in cache_keys
        if isinstance(entry, dict) and isinstance(entry.get("file"), str)
    }

    assert "pyproject.toml" in file_entries
    assert "rust_core/Cargo.toml" in file_entries
    assert "rust_core/Cargo.lock" in file_entries
    assert "rust_core/src/**/*.rs" in file_entries
    assert "tensor-grep" not in uv_config.get("reinstall-package", [])
