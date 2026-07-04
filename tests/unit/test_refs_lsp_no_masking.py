"""P0-1 of the warm-LSP moat: a partial / under-indexed `--provider lsp` references result must NOT
discard the correct native answer (dogfood v1.20.0: `tg refs --provider lsp` returned 2 of 14 and
marked it authoritative -- a silent wrong-output / fail-closed-contract violation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tensor_grep.cli import repo_map


def test_partial_lsp_refs_do_not_discard_native_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "module.py").write_text("def create_invoice():\n    return 1\n", encoding="utf-8")
    (tmp_path / "a.py").write_text(
        "from module import create_invoice\n\n\ndef use_a():\n    return create_invoice()\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "from module import create_invoice\n\n\ndef use_b():\n    return create_invoice()\n",
        encoding="utf-8",
    )
    # Keep definitions native (isolate the references path).
    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(
        repo_map, "_external_definitions", lambda root, symbol, native_definitions, **kwargs: []
    )
    # LSP under-returns: only ONE of the two native call sites (the 2-of-14 shape).
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions, **kwargs: [
            {
                "file": str((tmp_path / "a.py").resolve()),
                "line": 5,
                "end_line": 5,
                "text": "create_invoice()",
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_operation": "textDocument/references",
            }
        ],
    )
    rm = repo_map.build_repo_map(tmp_path)
    payload = repo_map.build_symbol_refs_from_map(rm, "create_invoice", semantic_provider="lsp")

    ref_files = {Path(r["file"]).name for r in payload["references"]}
    # Native truth NOT discarded: BOTH caller files present (pre-fix only a.py survived the replace).
    assert "a.py" in ref_files
    assert "b.py" in ref_files, "a partial LSP result must not discard the native ref b.py"
    # ...and the partial is NOT mis-reported as a clean lsp-only proof.
    assert payload["provider_agreement"]["agreement_status"] != "lsp-only"
