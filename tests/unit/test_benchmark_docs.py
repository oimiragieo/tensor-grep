from pathlib import Path


def test_benchmark_docs_should_include_local_dependency_setup_and_triton_note() -> None:
    doc = Path("docs/benchmarks.md").read_text(encoding="utf-8")
    assert "uv sync --extra dev --extra ast" in doc
    assert "uv sync --extra dev --extra bench --extra nlp" in doc
    assert "Triton" in doc
    assert "cyBERT" in doc
