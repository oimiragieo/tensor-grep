from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import mcp_server, repo_map
from tensor_grep.cli.main import app


def test_repo_map_defs_can_use_lsp_provider(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol: [
            {
                "name": symbol,
                "kind": "function",
                "file": str(module_path.resolve()),
                "line": 1,
                "end_line": 1,
                "provenance": "lsp-python",
            }
        ],
    )

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert payload["definitions"][0]["provenance"] == "lsp-python"


def test_repo_map_refs_hybrid_merges_external_and_native(tmp_path: Path, monkeypatch) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text("def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8")
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol: [])
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(service_path.resolve()),
                "line": 1,
                "end_line": 1,
                "text": "def create_invoice(total: int) -> int:",
                "provenance": "lsp-python",
            }
        ],
    )

    payload = repo_map.build_symbol_refs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert payload["semantic_provider"] == "hybrid"
    assert any(current["provenance"] == "lsp-python" for current in payload["references"])
    assert any(current["file"] == str(consumer_path.resolve()) for current in payload["references"])


def test_repo_map_callers_can_use_lsp_provider(tmp_path: Path, monkeypatch) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text("def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8")
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol: [])
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(consumer_path.resolve()),
                "line": 3,
                "end_line": 3,
                "text": "result = create_invoice(3)",
                "provenance": "lsp-python",
            }
        ],
    )

    payload = repo_map.build_symbol_callers("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert any(current["provenance"] == "lsp-python" for current in payload["callers"])


def test_cli_defs_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "build_symbol_defs_json",
        lambda symbol, path, semantic_provider="native": json.dumps(
            {"symbol": symbol, "path": str(path), "semantic_provider": semantic_provider}
        ),
    )

    result = CliRunner().invoke(app, ["defs", str(tmp_path), "--symbol", "create_invoice", "--provider", "hybrid", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_mcp_defs_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        mcp_server,
        "build_symbol_defs",
        lambda symbol, path, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
            "definitions": [],
        },
    )

    payload = json.loads(mcp_server.tg_symbol_defs("create_invoice", str(tmp_path), provider="lsp"))

    assert payload["semantic_provider"] == "lsp"
