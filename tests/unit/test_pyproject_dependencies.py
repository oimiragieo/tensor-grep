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
    assert "transformers>=4.40" in deps
    assert "tritonclient[http]" in deps
    assert "tritonclient[all]" not in deps


def test_bench_extra_should_include_stringzilla_for_hot_query_benchmarks() -> None:
    deps = _optional_dependencies()["bench"]

    assert "stringzilla>=4.0" in deps


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
    ]
