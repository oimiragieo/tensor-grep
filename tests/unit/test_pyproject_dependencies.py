from __future__ import annotations

import tomllib
from pathlib import Path


def _pyproject_payload() -> dict[str, object]:
    root = Path(__file__).resolve().parents[2]
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def _optional_dependencies() -> dict[str, list[str]]:
    payload = _pyproject_payload()
    return payload["project"]["optional-dependencies"]


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


def test_semantic_extra_should_pin_model2vec_and_numpy_no_torch() -> None:
    deps = _optional_dependencies()["semantic"]

    assert "model2vec>=0.5" in deps
    assert "numpy>=1.26" in deps
    # The whole point of model2vec (vs onnx-MiniLM/transformers) is NO torch/GPU dependency.
    assert not any(dep.lower().startswith("torch") for dep in deps)


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
