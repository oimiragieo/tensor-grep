from pathlib import Path

DOC_PATH = Path("docs/routing_policy.md")


def test_routing_policy_doc_covers_current_routing_tree() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "# Routing Policy" in doc
    assert "routing.rs" in doc
    assert "route_search(config, calibration_data, index_state, gpu_available)" in doc
    assert "NativeCpuBackend" in doc
    assert "NativeGpuBackend" in doc
    assert "AstBackend" in doc
    assert "TrigramIndex" in doc
    assert "GpuSidecar" in doc
    assert "RipgrepBackend" in doc
    assert ".tg_index" in doc
    assert "pattern >= 3 bytes" in doc
    assert "-v" in doc
    assert "-C" in doc
    assert "-w" in doc
    assert "-g" in doc
    assert "--max-count" in doc
    assert "--index" in doc
    assert "--gpu-device-ids" in doc
    assert "--force-cpu" in doc
    assert "--rewrite" in doc
    assert "force_cpu" in doc
    assert "json_output" in doc
    assert "ast-native" in doc
    assert "index-accelerated" in doc
    assert "gpu-device-ids-explicit-native" in doc
    assert "gpu-auto-size-threshold" in doc
    assert "cpu-auto-size-threshold" in doc
    assert "gpu-device-ids-explicit" in doc
    assert "rg_passthrough" in doc
    assert "handle_ripgrep_search" in doc
    assert "handle_index_search" in doc
    assert "run_index_query" in doc
    assert "handle_gpu_sidecar_search" in doc
    assert "handle_gpu_search" in doc
    assert "handle_auto_gpu_search" in doc
    assert "handle_ast_run" in doc
    assert "final fallback" in doc.lower()
    assert "JSON and NDJSON output do **not** bypass a warm compatible index anymore." in doc
