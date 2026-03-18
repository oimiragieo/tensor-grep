from pathlib import Path

README_PATH = Path("README.md")
ROUTING_DOC_PATH = Path("docs/routing_policy.md")


def test_readme_should_point_to_canonical_public_docs() -> None:
    readme = README_PATH.read_text(encoding="utf-8")

    assert "docs/benchmarks.md" in readme
    assert "docs/gpu_crossover.md" in readme
    assert "docs/routing_policy.md" in readme
    assert "docs/harness_api.md" in readme
    assert "docs/harness_cookbook.md" in readme
    assert "tg calibrate" in readme
    assert "tg search --ndjson" in readme
    assert "tg mcp" in readme
    assert "native CPU engine" in readme
    assert "native GPU engine" in readme
    assert "benchmark-governed" in readme


def test_routing_policy_should_describe_current_native_and_fallback_routes() -> None:
    doc = ROUTING_DOC_PATH.read_text(encoding="utf-8")

    assert "# Routing Policy" in doc
    assert "NativeCpuBackend" in doc
    assert "NativeGpuBackend" in doc
    assert "TrigramIndex" in doc
    assert "AstBackend" in doc
    assert "GpuSidecar" in doc
    assert "RipgrepBackend" in doc
    assert "--index" in doc
    assert "--gpu-device-ids" in doc
    assert "--force-cpu" in doc
    assert "Warm non-stale compatible `.tg_index`" in doc
    assert "calibrated threshold" in doc
