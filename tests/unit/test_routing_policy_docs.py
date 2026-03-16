from pathlib import Path

DOC_PATH = Path("docs/routing_policy.md")


def test_routing_policy_doc_covers_current_routing_tree() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert "# Routing Policy" in doc
    assert "CpuBackend" in doc
    assert "AstBackend" in doc
    assert "TrigramIndex" in doc
    assert "GpuSidecar" in doc
    assert "ripgrep" in doc
    assert "cold text search" in doc.lower()
    assert ".tg_index" in doc
    assert "pattern >= 3 bytes" in doc
    assert "-v" in doc
    assert "-C" in doc
    assert "-w" in doc
    assert "-g" in doc
    assert "--max-count" in doc
    assert "--index" in doc
    assert "--gpu-device-ids" in doc
    assert "--rewrite" in doc
    assert "cpu-native" in doc
    assert "ast-native" in doc
    assert "index-accelerated" in doc
    assert "gpu-device-ids-explicit" in doc
    assert "handle_ripgrep_search" in doc
    assert "handle_index_search" in doc
    assert "run_index_query" in doc
    assert "handle_gpu_sidecar_search" in doc
    assert "handle_ast_run" in doc
    assert "handle_ast_rewrite" in doc
    assert "handle_ast_rewrite_apply" in doc
    assert (
        "explicit `--index` -> explicit `--gpu-device-ids` -> warm index auto-routing -> "
        "`--json` CPU search -> cold rg passthrough"
        in doc
    )
    assert "Current code-order caveat" not in doc
    assert (
        "warm index auto-routing is evaluated before the explicit `--gpu-device-ids` branch"
        not in doc
    )
