"""Tests for DF2-13 (primary_target.symbol fallback), DF2-11 (\\\\?\\ strip), and ENT-09."""

from __future__ import annotations

import json
from pathlib import Path

from tensor_grep.cli import evidence_receipt, ledger_store, mcp_server, repo_map
from tensor_grep.cli.repo_map_input_provenance import decode_source_bytes, read_text_with_receipt


def test_evidence_emit_and_ledger_record_fallback_to_primary_target_symbol(tmp_path: Path) -> None:
    capsule_path = tmp_path / "nl_capsule.json"
    capsule_path.write_text(
        json.dumps({
            "query": "how does token verification work",
            "primary_target": {"file": "src/auth.py", "symbol": "verify_token", "line": 10},
        }),
        encoding="utf-8",
    )

    receipt = evidence_receipt.build_evidence_receipt(tmp_path, capsule_path=capsule_path)
    assert receipt["blast_radius"]["symbol"] == "verify_token"

    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    recorded = ledger_store.record_finding(str(tmp_path), receipt_path=str(receipt_path))
    assert recorded["finding"]["symbol"] == "verify_token"

    found = ledger_store.find_findings(str(tmp_path), symbol="verify_token")
    assert found["count"] == 1
    assert found["findings"][0]["symbol"] == "verify_token"

    # Direct ledger record on NL capsule without intermediate evidence receipt also resolves symbol.
    direct = ledger_store.record_finding(
        str(tmp_path), receipt_path=str(capsule_path), artifact_kind="context-pack"
    )
    assert direct["finding"]["symbol"] == "verify_token"


def test_mcp_capabilities_strips_windows_extended_path_prefix(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_resolve_native_tg_binary_for_mcp",
        lambda: (Path(r"\\?\C:\tools\tg.exe"), None),
    )
    payload = json.loads(mcp_server.tg_mcp_capabilities(summary=False))
    assert payload["native_tg"]["path"] == r"C:\tools\tg.exe"


def test_utf16le_bom_and_latin1_decoding_and_lsp_budget(tmp_path: Path) -> None:
    utf16_file = tmp_path / "utf16.py"
    utf16_file.write_bytes(b"\xff\xfe" + "def hello():\n    return 'caf\xe9'\n".encode("utf-16-le"))
    assert "def hello():" in read_text_with_receipt(utf16_file)

    latin1_file = tmp_path / "latin1.py"
    latin1_file.write_bytes(b"# caf\xe9\ndef bonjour():\n    return 1\n")
    decoded = read_text_with_receipt(latin1_file)
    assert "caf\xe9" in decoded
    assert "def bonjour():" in decoded
    assert decode_source_bytes(b"\xef\xbb\xbfx = 1\n") == "x = 1\n"

    assert repo_map._DEFAULT_LSP_OPERATION_BUDGET_SECONDS >= 10.0
