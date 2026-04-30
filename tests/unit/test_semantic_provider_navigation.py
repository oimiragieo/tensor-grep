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
        lambda root, symbol, **kwargs: [
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
    assert payload["provider_agreement"]["agreement_status"] == "lsp-only"
    assert payload["provider_status"]["mode"] == "lsp"


def test_repo_map_source_can_use_lsp_provider(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol, **kwargs: [
            {
                "name": symbol,
                "kind": "function",
                "file": str(module_path.resolve()),
                "line": 1,
                "end_line": 2,
                "provenance": "lsp-python",
            }
        ],
    )

    payload = repo_map.build_symbol_source("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["provider_status"]["mode"] == "lsp"


def test_repo_map_refs_hybrid_merges_external_and_native(tmp_path: Path, monkeypatch) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
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
    assert payload["provider_agreement"]["agreement_status"] in {"diverged", "agreed"}
    assert payload["provider_status"]["mode"] == "hybrid"


def test_repo_map_callers_can_use_lsp_provider(tmp_path: Path, monkeypatch) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
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
    assert payload["provider_agreement"]["agreement_status"] == "lsp-only"


def test_repo_map_callers_hybrid_can_expand_python_alias_wrapper_calls(
    tmp_path: Path, monkeypatch
) -> None:
    impl_path = tmp_path / "_termui_impl.py"
    wrapper_path = tmp_path / "termui.py"
    impl_path.write_text('def getchar(echo: bool) -> str:\n    return "y"\n', encoding="utf-8")
    wrapper_path.write_text(
        "from _termui_impl import getchar as f\n"
        "_getchar = f\n\n"
        "def prompt(echo: bool) -> str:\n"
        "    return _getchar(echo)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(wrapper_path.resolve()),
                "line": 1,
                "end_line": 1,
                "text": "from _termui_impl import getchar as f",
                "provenance": "lsp-python",
            }
        ],
    )

    native_payload = repo_map.build_symbol_callers("getchar", tmp_path, semantic_provider="native")
    hybrid_payload = repo_map.build_symbol_callers("getchar", tmp_path, semantic_provider="hybrid")

    assert not any(
        current["file"] == str(wrapper_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(wrapper_path.resolve()) and current["line"] == 5
        for current in hybrid_payload["callers"]
    )
    assert any(current["provenance"] == "lsp-python" for current in hybrid_payload["callers"])


def test_repo_map_blast_radius_hybrid_can_include_alias_wrapper_callers(
    tmp_path: Path, monkeypatch
) -> None:
    impl_path = tmp_path / "_termui_impl.py"
    wrapper_path = tmp_path / "termui.py"
    impl_path.write_text('def getchar(echo: bool) -> str:\n    return "y"\n', encoding="utf-8")
    wrapper_path.write_text(
        "from _termui_impl import getchar as f\n"
        "_getchar = f\n\n"
        "def prompt(echo: bool) -> str:\n"
        "    return _getchar(echo)\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(wrapper_path.resolve()),
                "line": 1,
                "end_line": 1,
                "text": "from _termui_impl import getchar as f",
                "provenance": "lsp-python",
            }
        ],
    )

    native_payload = repo_map.build_symbol_blast_radius(
        "getchar", tmp_path, semantic_provider="native"
    )
    hybrid_payload = repo_map.build_symbol_blast_radius(
        "getchar", tmp_path, semantic_provider="hybrid"
    )

    assert not any(
        current["file"] == str(wrapper_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(wrapper_path.resolve()) for current in hybrid_payload["callers"]
    )
    assert str(wrapper_path.resolve()) in hybrid_payload["files"]


def test_repo_map_callers_hybrid_can_expand_js_ts_import_alias_wrappers(
    tmp_path: Path, monkeypatch
) -> None:
    payments_path = tmp_path / "payments.ts"
    service_path = tmp_path / "service.ts"
    payments_path.write_text(
        "export function createInvoiceAliasWrapper(total: number) {\n    return total + 1;\n}\n",
        encoding="utf-8",
    )
    service_path.write_text(
        'import { createInvoiceAliasWrapper as invoice } from "./payments";\n'
        "const runInvoice = invoice;\n\n"
        "export function buildReceipt(total: number) {\n"
        "  return runInvoice(total);\n"
        "}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    native_payload = repo_map.build_symbol_callers(
        "createInvoiceAliasWrapper", tmp_path, semantic_provider="native"
    )
    hybrid_payload = repo_map.build_symbol_callers(
        "createInvoiceAliasWrapper", tmp_path, semantic_provider="hybrid"
    )

    assert not any(
        current["file"] == str(service_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(service_path.resolve()) and current["line"] == 5
        for current in hybrid_payload["callers"]
    )
    assert any(
        "fallback" in str(current.get("provenance", "")) for current in hybrid_payload["callers"]
    )


def test_repo_map_blast_radius_hybrid_can_include_js_ts_alias_wrapper_callers(
    tmp_path: Path, monkeypatch
) -> None:
    payments_path = tmp_path / "payments.ts"
    service_path = tmp_path / "service.ts"
    payments_path.write_text(
        "export function createInvoiceAliasWrapper(total: number) {\n    return total + 1;\n}\n",
        encoding="utf-8",
    )
    service_path.write_text(
        'import { createInvoiceAliasWrapper as invoice } from "./payments";\n'
        "const runInvoice = invoice;\n\n"
        "export function buildReceipt(total: number) {\n"
        "  return runInvoice(total);\n"
        "}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    native_payload = repo_map.build_symbol_blast_radius(
        "createInvoiceAliasWrapper", tmp_path, semantic_provider="native"
    )
    hybrid_payload = repo_map.build_symbol_blast_radius(
        "createInvoiceAliasWrapper", tmp_path, semantic_provider="hybrid"
    )

    assert not any(
        current["file"] == str(service_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(service_path.resolve()) for current in hybrid_payload["callers"]
    )
    assert str(service_path.resolve()) in hybrid_payload["files"]


def test_repo_map_callers_hybrid_can_expand_rust_use_alias_wrappers(
    tmp_path: Path, monkeypatch
) -> None:
    lib_path = tmp_path / "lib.rs"
    module_path = tmp_path / "payments.rs"
    service_path = tmp_path / "service.rs"
    lib_path.write_text("mod payments;\nmod service;\n", encoding="utf-8")
    module_path.write_text(
        "pub fn create_invoice_provider_rust(total: i32) -> i32 {\n    total + 1\n}\n",
        encoding="utf-8",
    )
    service_path.write_text(
        "use crate::payments::create_invoice_provider_rust as invoice;\n\n"
        "pub fn build_receipt(total: i32) -> i32 {\n"
        "    let run_invoice = invoice;\n"
        "    run_invoice(total)\n"
        "}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    native_payload = repo_map.build_symbol_callers(
        "create_invoice_provider_rust", tmp_path, semantic_provider="native"
    )
    hybrid_payload = repo_map.build_symbol_callers(
        "create_invoice_provider_rust", tmp_path, semantic_provider="hybrid"
    )

    assert not any(
        current["file"] == str(service_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(service_path.resolve()) and current["line"] == 5
        for current in hybrid_payload["callers"]
    )
    assert any(
        "fallback" in str(current.get("provenance", "")) for current in hybrid_payload["callers"]
    )


def test_repo_map_blast_radius_hybrid_can_include_rust_alias_wrapper_callers(
    tmp_path: Path, monkeypatch
) -> None:
    lib_path = tmp_path / "lib.rs"
    module_path = tmp_path / "payments.rs"
    service_path = tmp_path / "service.rs"
    lib_path.write_text("mod payments;\nmod service;\n", encoding="utf-8")
    module_path.write_text(
        "pub fn create_invoice_provider_rust(total: i32) -> i32 {\n    total + 1\n}\n",
        encoding="utf-8",
    )
    service_path.write_text(
        "use crate::payments::create_invoice_provider_rust as invoice;\n\n"
        "pub fn build_receipt(total: i32) -> i32 {\n"
        "    let run_invoice = invoice;\n"
        "    run_invoice(total)\n"
        "}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    native_payload = repo_map.build_symbol_blast_radius(
        "create_invoice_provider_rust", tmp_path, semantic_provider="native"
    )
    hybrid_payload = repo_map.build_symbol_blast_radius(
        "create_invoice_provider_rust", tmp_path, semantic_provider="hybrid"
    )

    assert not any(
        current["file"] == str(service_path.resolve()) for current in native_payload["callers"]
    )
    assert any(
        current["file"] == str(service_path.resolve()) for current in hybrid_payload["callers"]
    )
    assert str(service_path.resolve()) in hybrid_payload["files"]


def test_repo_map_impact_propagates_semantic_provider(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "build_symbol_defs_from_map",
        lambda repo_map_payload, symbol, semantic_provider="native": {
            "path": str(tmp_path.resolve()),
            "definitions": [
                {
                    "name": symbol,
                    "kind": "function",
                    "file": str(module_path.resolve()),
                    "line": 1,
                    "end_line": 1,
                    "provenance": "lsp-python",
                }
            ],
            "files": [str(module_path.resolve())],
            "semantic_provider": semantic_provider,
        },
    )

    payload = repo_map.build_symbol_impact("create_invoice", tmp_path, semantic_provider="hybrid")

    assert payload["semantic_provider"] == "hybrid"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["provider_agreement"]["mode"] == "hybrid"


def test_repo_map_blast_radius_propagates_semantic_provider(tmp_path: Path, monkeypatch) -> None:
    service_path = tmp_path / "service.py"
    service_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol, **kwargs: [
            {
                "name": symbol,
                "kind": "function",
                "file": str(service_path.resolve()),
                "line": 1,
                "end_line": 1,
                "provenance": "lsp-python",
            }
        ],
    )
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    payload = repo_map.build_symbol_blast_radius(
        "create_invoice", tmp_path, semantic_provider="lsp"
    )

    assert payload["semantic_provider"] == "lsp"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["provider_agreement"]["mode"] == "lsp"


def test_cli_defs_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        repo_map,
        "build_symbol_defs_json",
        lambda symbol, path, semantic_provider="native": json.dumps({
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app, ["defs", str(tmp_path), "--symbol", "create_invoice", "--provider", "hybrid", "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_cli_impact_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_impact_json",
        lambda symbol, path, semantic_provider="native": json.dumps({
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app, ["impact", str(tmp_path), "--symbol", "create_invoice", "--provider", "lsp", "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "lsp"


def test_cli_source_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_source_json",
        lambda symbol, path, semantic_provider="native": json.dumps({
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app,
        ["source", str(tmp_path), "--symbol", "create_invoice", "--provider", "hybrid", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_cli_blast_radius_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_json",
        lambda symbol, path, max_depth=3, semantic_provider="native": json.dumps({
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app,
        [
            "blast-radius",
            str(tmp_path),
            "--symbol",
            "create_invoice",
            "--provider",
            "hybrid",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_cli_blast_radius_plan_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_plan_json",
        lambda symbol, path, max_depth=3, max_files=3, max_symbols=5, semantic_provider="native": (
            json.dumps({
                "symbol": symbol,
                "path": str(path),
                "max_depth": max_depth,
                "max_files": max_files,
                "max_symbols": max_symbols,
                "semantic_provider": semantic_provider,
            })
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "blast-radius-plan",
            str(tmp_path),
            "--symbol",
            "create_invoice",
            "--provider",
            "hybrid",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_cli_blast_radius_render_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_render_json",
        lambda symbol, path, max_depth=3, max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full", profile=False, semantic_provider="native": (
            json.dumps({
                "symbol": symbol,
                "path": str(path),
                "max_depth": max_depth,
                "semantic_provider": semantic_provider,
            })
        ),
    )

    result = CliRunner().invoke(
        app,
        [
            "blast-radius-render",
            str(tmp_path),
            "--symbol",
            "create_invoice",
            "--provider",
            "lsp",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "lsp"


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


def test_mcp_impact_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_impact",
        lambda symbol, path, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
            "definitions": [],
            "files": [],
            "tests": [],
            "imports": [],
            "symbols": [],
        },
    )

    payload = json.loads(
        mcp_server.tg_symbol_impact("create_invoice", str(tmp_path), provider="hybrid")
    )

    assert payload["semantic_provider"] == "hybrid"


def test_mcp_source_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_source",
        lambda symbol, path, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
            "definitions": [],
            "sources": [],
            "files": [],
        },
    )

    payload = json.loads(
        mcp_server.tg_symbol_source("create_invoice", str(tmp_path), provider="lsp")
    )

    assert payload["semantic_provider"] == "lsp"


def test_mcp_blast_radius_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_blast_radius",
        lambda symbol, path, max_depth=3, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "semantic_provider": semantic_provider,
            "definitions": [],
            "callers": [],
            "files": [],
            "tests": [],
        },
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius(
            "create_invoice", str(tmp_path), max_depth=2, provider="lsp"
        )
    )

    assert payload["semantic_provider"] == "lsp"


def test_mcp_blast_radius_plan_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_plan",
        lambda symbol, path, max_depth=3, max_files=3, max_symbols=5, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "max_files": max_files,
            "max_symbols": max_symbols,
            "semantic_provider": semantic_provider,
            "definitions": [],
            "callers": [],
            "files": [],
            "tests": [],
        },
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_plan(
            "create_invoice", str(tmp_path), max_depth=2, provider="hybrid"
        )
    )

    assert payload["semantic_provider"] == "hybrid"


def test_mcp_blast_radius_render_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_blast_radius_render",
        lambda symbol, path, max_depth=3, max_files=3, max_sources=5, max_symbols_per_file=6, max_render_chars=None, optimize_context=False, render_profile="full", profile=False, semantic_provider="native": {
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "semantic_provider": semantic_provider,
            "rendered_context": "",
            "definitions": [],
            "callers": [],
            "files": [],
            "tests": [],
        },
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice", str(tmp_path), max_depth=2, provider="lsp"
        )
    )

    assert payload["semantic_provider"] == "lsp"
