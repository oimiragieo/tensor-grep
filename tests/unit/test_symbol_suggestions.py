from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli.main import app


def test_defs_returns_suggestions_when_unrelated_file_has_syntax_error(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(
        "def compute_tax(amount: float) -> float:\n    return amount * 0.1\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "comput_tx", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["result_incomplete"] is True
    assert payload["not_found"] is False
    assert "compute_tax" in payload["suggestions"]


def test_defs_refs_callers_detail_compact_missing_symbol_under_1536_bytes(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(
        "def compute_tax(amount: float) -> float:\n    return amount * 0.1\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")

    runner = CliRunner()
    for command in ("defs", "refs", "callers"):
        result = runner.invoke(
            app, [command, str(tmp_path), "comput_tx", "--json", "--detail", "compact"]
        )
        assert result.exit_code == 2, f"{command}: {result.output}"
        raw_bytes = len(result.stdout.encode("utf-8"))
        assert raw_bytes < 1536, f"{command} compact output too large: {raw_bytes} bytes"
        payload = json.loads(result.stdout)
        assert payload["result_incomplete"] is True
        assert payload["incomplete_reason"]
        assert payload["not_found"] is False
        assert "provider_status" not in payload
        assert "resolution_gaps" not in payload
        assert payload["resolution_gaps_count"] >= 1
        if command == "defs":
            assert "compute_tax" in payload["suggestions"]


def test_refs_and_callers_unreferenced_defined_symbol_emit_no_suggestions(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(
        "def unused_helper() -> int:\n    return 1\n\ndef unused_helper_v2() -> int:\n    return 2\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    for command, result_key in (("refs", "references"), ("callers", "callers")):
        result = runner.invoke(app, [command, str(tmp_path), "unused_helper", "--json"])
        assert result.exit_code == 0, f"{command}: {result.output}"
        payload = json.loads(result.stdout)
        assert payload["not_found"] is False
        assert payload["result_incomplete"] is False
        assert payload[result_key] == []
        assert payload["suggestions"] == []


def test_compact_missing_symbol_preserves_provenance_and_caps_long_suggestions() -> None:
    from tensor_grep.cli.symbol_compact import (
        _COMPACT_MISSING_MAX_BYTES,
        trim_symbol_compact_payload,
    )

    base_payload = {
        "version": 1,
        "schema_version": 1,
        "command": "defs",
        "symbol": "comput_tx",
        "path": "src/billing.py",
        "definitions": [],
        "not_found": False,
        "result_incomplete": True,
        "incomplete_reason": "syntax error in unrelated file",
        "routing_backend": "ast_native",
        "routing_reason": "tree_sitter_index",
        "sidecar_used": False,
        "scan_remediation": "fix syntax error in broken.py",
        "lsp_evidence_status": "unavailable",
        "graph_completeness": "partial",
        "provider_status": {"native": {"ok": True, "detail": "x" * 600}},
        "resolution_gaps": [{"file": f"f{i}.py", "reason": "parse_error"} for i in range(5)],
        "suggestions": ["compute_tax", "compute_total"],
        "caveat": "long caveat " * 60,
    }
    trimmed = trim_symbol_compact_payload(base_payload, result_key="definitions")
    assert "provider_status" not in trimmed
    assert "resolution_gaps" not in trimmed
    for key in (
        "routing_backend",
        "routing_reason",
        "sidecar_used",
        "scan_remediation",
        "lsp_evidence_status",
        "graph_completeness",
        "incomplete_reason",
        "result_incomplete",
        "not_found",
        "suggestions",
    ):
        assert trimmed[key] == base_payload[key]
    assert trimmed["resolution_gaps_count"] == 5

    long_payload = {
        **base_payload,
        "suggestions": [f"very_long_candidate_symbol_name_{i}_" + ("x" * 60) for i in range(40)],
    }
    capped = trim_symbol_compact_payload(long_payload, result_key="definitions")
    assert capped["compact_truncated"] is True
    serialized_bytes = len(json.dumps(capped, separators=(",", ":")).encode("utf-8"))
    assert serialized_bytes <= _COMPACT_MISSING_MAX_BYTES
    assert serialized_bytes < 1536


def test_compact_missing_symbol_extreme_oversize_retains_core_provenance_intact() -> None:
    from tensor_grep.cli.symbol_compact import (
        _COMPACT_MISSING_MAX_BYTES,
        trim_symbol_compact_payload,
    )

    oversize_payload = {
        "version": 1,
        "schema_version": 1,
        "command": "defs",
        "symbol": "very_long_missing_symbol_" + ("s" * 1500),
        "path": "src/deeply/nested/" + ("p" * 1500) + "/billing.py",
        "definitions": [],
        "not_found": False,
        "result_incomplete": True,
        "incomplete_reason": "syntax_error_and_unresolved_import_in_workspace_file",
        "routing_backend": "ast_native_tree_sitter_fallback_engine",
        "routing_reason": "tree_sitter_index_with_syntax_errors_detected",
        "sidecar_used": False,
        "scan_remediation": "fix syntax error in broken.py " * 80,
        "lsp_evidence_status": "unavailable",
        "graph_completeness": "partial",
        "resolution_gaps": [{"file": f"f{i}.py", "reason": "parse_error"} for i in range(5)],
        "suggestions": [f"candidate_symbol_{i}_" + ("x" * 60) for i in range(40)],
        "extra_diagnostic_blob": "z" * 2000,
    }
    trimmed = trim_symbol_compact_payload(oversize_payload, result_key="definitions")
    serialized_bytes = len(json.dumps(trimmed, separators=(",", ":")).encode("utf-8"))
    assert serialized_bytes <= _COMPACT_MISSING_MAX_BYTES
    assert trimmed["compact_truncated"] is True
    assert trimmed["routing_backend"] == oversize_payload["routing_backend"]
    assert trimmed["routing_reason"] == oversize_payload["routing_reason"]
    assert trimmed["incomplete_reason"] == oversize_payload["incomplete_reason"]
    assert trimmed["sidecar_used"] is False
    assert trimmed["result_incomplete"] is True
    assert trimmed["not_found"] is False
    assert trimmed["schema_version"] == 1
    assert trimmed["command"] == "defs"
    assert trimmed["resolution_gaps_count"] == 5
