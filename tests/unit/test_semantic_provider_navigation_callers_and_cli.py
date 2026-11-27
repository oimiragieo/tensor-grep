from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import mcp_server, repo_map
from tensor_grep.cli.main import app


def _disable_external_definition_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repo_map, "_external_workspace_symbols", lambda root, symbol, **kwargs: [])
    monkeypatch.setattr(
        repo_map,
        "_external_definitions",
        lambda root, symbol, native_definitions, **kwargs: [],
    )


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

    _disable_external_definition_proof(monkeypatch)
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

    _disable_external_definition_proof(monkeypatch)
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

    _disable_external_definition_proof(monkeypatch)
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


def test_repo_map_callers_lsp_fallback_alias_rows_are_not_lsp_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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

    _disable_external_definition_proof(monkeypatch)
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    payload = repo_map.build_symbol_callers(
        "createInvoiceAliasWrapper",
        tmp_path,
        semantic_provider="lsp",
    )

    assert payload["semantic_provider"] == "lsp"
    assert any(current["file"] == str(service_path.resolve()) for current in payload["callers"])
    assert all("-fallback" in str(current.get("provenance", "")) for current in payload["callers"])
    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False
    assert "native fallback" in payload["not_lsp_proof_reason"].lower()


def test_repo_map_callers_hybrid_deduplicates_external_and_native_same_call_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    consumer_path.write_text(
        "from service import create_invoice\n\ncreate_invoice()\n",
        encoding="utf-8",
    )
    resolved_consumer = str(consumer_path.resolve())
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": resolved_consumer,
                "line": 3,
                "end_line": 3,
                "text": "create_invoice()",
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_operation": "textDocument/references",
            }
        ],
    )

    payload = repo_map.build_symbol_callers("create_invoice", tmp_path, semantic_provider="hybrid")

    assert [(row["file"], row["line"], row["text"]) for row in payload["callers"]] == [
        (resolved_consumer, 3, "create_invoice()")
    ]
    assert payload["callers"][0]["lsp_provider_response"] is True
    assert payload["callers"][0]["lsp_proof"] is True
    assert payload["lsp_proof"] is True
    assert payload["lsp_evidence_status"] == "lsp_proof"


def test_repo_map_refs_hybrid_deduplicates_external_and_native_same_reference_with_lsp_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    consumer_path.write_text(
        "from service import create_invoice\n\ncreate_invoice()\n",
        encoding="utf-8",
    )
    resolved_consumer = str(consumer_path.resolve())
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": resolved_consumer,
                "line": 3,
                "end_line": 3,
                "text": "create_invoice()",
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_proof": True,
                "lsp_operation": "textDocument/references",
            }
        ],
    )

    payload = repo_map.build_symbol_refs("create_invoice", tmp_path, semantic_provider="hybrid")
    matching_refs = [
        row
        for row in payload["references"]
        if row["file"] == resolved_consumer and row["line"] == 3
    ]

    assert len(matching_refs) == 1
    assert matching_refs[0]["lsp_provider_response"] is True
    assert matching_refs[0]["lsp_proof"] is True
    assert payload["lsp_proof"] is True
    assert payload["lsp_evidence_status"] == "lsp_proof"


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

    _disable_external_definition_proof(monkeypatch)
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

    _disable_external_definition_proof(monkeypatch)
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

    _disable_external_definition_proof(monkeypatch)
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
        # **_kwargs swallows additive params real callers may pass (e.g. audit C1/C2's
        # deadline_monotonic thread-through) -- mirrors the sibling blast-radius test below
        # (lambda root, symbol, **kwargs: ...), which already tolerates this.
        lambda repo_map_payload, symbol, semantic_provider="native", **_kwargs: {
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
                "lsp_provider_response": True,
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
        lambda symbol, path, semantic_provider="native", **_: json.dumps({
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
    # The impact command uses an inline implementation and does not delegate to
    # build_symbol_impact_json, so no monkeypatch is needed.  The command exits 1
    # on no-match (mirroring rg's exit convention) while still emitting a valid JSON
    # payload — assert that the provider option is accepted and threaded through.
    result = CliRunner().invoke(
        app, ["impact", str(tmp_path), "--symbol", "create_invoice", "--provider", "lsp", "--json"]
    )

    # Exit 1 is the correct no-match exit code (symbol not found in empty dir).
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "lsp"


def test_cli_source_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    # The source command uses an inline implementation and does not delegate to
    # build_symbol_source_json, so no monkeypatch is needed.  The command exits 1
    # on no-match (mirroring rg's exit convention) while still emitting a valid JSON
    # payload — assert that the provider option is accepted and threaded through.
    result = CliRunner().invoke(
        app,
        ["source", str(tmp_path), "--symbol", "create_invoice", "--provider", "hybrid", "--json"],
    )

    # Exit 1 is the correct no-match exit code (symbol not found in empty dir).
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"


def test_cli_blast_radius_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    # The `blast-radius` CLI command builds its own JSON via `build_symbol_blast_radius` +
    # `json.dumps` (main.py) and never delegates to `build_symbol_blast_radius_json`, so a
    # monkeypatch on the latter was always dead here -- this test already exercised the REAL
    # (unmocked) payload against an empty `tmp_path`. audit #12 gave `blast-radius` the same
    # no-match exit-1 convention `impact`/`source` already have (see
    # test_cli_impact_accepts_provider_option / test_cli_source_accepts_provider_option above),
    # so "create_invoice" resolving to nothing in an empty dir now correctly exits 1.
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

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"
    assert payload["not_found"] is True


def test_cli_blast_radius_plan_accepts_provider_option(tmp_path: Path, monkeypatch) -> None:
    def fake_build_symbol_blast_radius_plan_json(
        symbol,
        path,
        max_depth=3,
        max_files=3,
        max_symbols=5,
        semantic_provider="native",
        **_,
    ):
        return json.dumps({
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "max_files": max_files,
            "max_symbols": max_symbols,
            "semantic_provider": semantic_provider,
        })

    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_plan_json",
        fake_build_symbol_blast_radius_plan_json,
    )

    # B-05: blast-radius-plan now uses the shared 0/1/2 emitter, so a plan for a symbol that does
    # not exist exits 1. The fixture defines the symbol so the provider option is exercised on a
    # FOUND plan (exit 0), which is what this test is about.
    (tmp_path / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n", encoding="utf-8"
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

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "hybrid"
    assert payload["not_found"] is False


def test_cli_blast_radius_render_accepts_provider_option(tmp_path: Path) -> None:
    """`--provider` must reach the emitted payload.

    THIS TEST USED TO CARRY AN INERT MONKEYPATCH. It stubbed
    `repo_map.build_symbol_blast_radius_render_json` and the CLI never called it -- the command
    calls `build_symbol_blast_radius_render` directly. Proven, not inferred: replacing the stub
    body with `raise AssertionError("STUB WAS CALLED")` left this test PASSING, so the fake was
    never invoked once.

    The assertion below was therefore always being satisfied by the REAL code path, which is the
    stronger thing to test -- so the fake and the `monkeypatch` fixture are gone rather than
    repointed. The wrapper they referenced is deleted too: it had zero real references in
    `main.py` (AST-checked; the only grep hit is a comment calling it "the old ... helper").
    """
    # B-05: blast-radius-render now uses the shared 0/1/2 emitter, so a symbol that does not
    # exist exits 1. The fixture defines it so the provider option is exercised on a FOUND result.
    (tmp_path / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n", encoding="utf-8"
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

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "lsp"
    assert payload["not_found"] is False


def test_mcp_defs_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    monkeypatch.setattr(
        mcp_server,
        "build_symbol_defs",
        lambda symbol, path, semantic_provider="native", **_: {
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
            "definitions": [],
        },
    )

    payload = json.loads(mcp_server.tg_symbol_defs("create_invoice", str(tmp_path), provider="lsp"))

    assert payload["semantic_provider"] == "lsp"


def test_mcp_impact_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_impact",
        lambda symbol, path, semantic_provider="native", max_repo_files=None, deadline_seconds=None: {
            "symbol": symbol,
            "path": str(path),
            "semantic_provider": semantic_provider,
            "max_repo_files": max_repo_files,
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
    assert payload["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT


def test_mcp_source_accepts_provider_parameter(tmp_path: Path, monkeypatch) -> None:
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_source",
        lambda symbol, path, semantic_provider="native", **_: {
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
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        mcp_server,
        "build_symbol_blast_radius",
        lambda symbol, path, max_depth=3, semantic_provider="native", **_: {
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
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_plan",
        lambda symbol, path, max_depth=3, max_files=3, max_symbols=5, semantic_provider="native", **_: {
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
    # round-8 (audit #95): path is now confined to the MCP root (cwd); chdir so tmp_path
    # is in-root.
    monkeypatch.chdir(tmp_path)

    def fake_build_symbol_blast_radius_render(
        symbol,
        path,
        max_depth=3,
        max_files=3,
        max_sources=5,
        max_symbols_per_file=6,
        max_render_chars=None,
        optimize_context=False,
        render_profile="full",
        profile=False,
        semantic_provider="native",
        **_,
    ):
        return {
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "semantic_provider": semantic_provider,
            "rendered_context": "",
            "definitions": [],
            "callers": [],
            "files": [],
            "tests": [],
        }

    monkeypatch.setattr(
        mcp_server,
        "build_symbol_blast_radius_render",
        fake_build_symbol_blast_radius_render,
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice", str(tmp_path), max_depth=2, provider="lsp"
        )
    )

    assert payload["semantic_provider"] == "lsp"


# ---------------------------------------------------------------------------
# Regression : `tg defs/source/refs/callers/impact/
# blast-radius <FILE> <SYMBOL> --provider lsp/hybrid` used to crash with
# NotADirectoryError. Root cause: when a repo map is scoped to a single FILE
# (not a directory), `repo_map["path"]` IS that file. Several `_external_*`
# call sites derived `repo_root`/`workspace_root` directly from it via
# `Path(str(repo_map["path"])).resolve()` with no directory guard. That value
# eventually reaches `ExternalLSPClient._start_locked`'s
# `subprocess.Popen(cwd=str(self.workspace_root), ...)`
# (lsp_external_provider.py:500), which raises `NotADirectoryError`
# (WinError 267 on Windows, ENOTDIR on POSIX) when `cwd` is a file rather
# than a directory. `--provider native` (the CLI default) never reaches this
# code, which is why the bug shipped silently -- it only reproduces under
# `--provider lsp`/`hybrid`.
#
# The fix threads every such site through `_repo_map_root_dir()`, mirroring
# the pre-existing `root if root.is_dir() else root.parent` pattern already
# used by `build_repo_map` (`context_root`) and
# `_repo_map_file_and_test_universe` (`base`) -- so external tooling always
# receives a real directory, while the user-visible `path`/`definitions[].file`
# fields are unaffected (they still report the exact file, per `_envelope`).
# ---------------------------------------------------------------------------


def test_repo_map_defs_file_path_with_lsp_provider_scopes_workspace_to_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg defs <file.py> <symbol> --provider lsp` must never crash with
    NotADirectoryError. The LSP `workspace_root` passed to `get_client` must be
    the file's PARENT directory (a real directory `subprocess.Popen(cwd=...)`
    can chdir into), never the file itself."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            pass

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            # The load-bearing assertion: workspace_root must be a real directory (the
            # file's parent), never the file itself -- a file here is exactly what crashed
            # subprocess.Popen(cwd=...) with NotADirectoryError.
            assert workspace_root == tmp_path.resolve()
            assert workspace_root.is_dir()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            assert workspace_root == tmp_path.resolve()
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_symbol_defs("create_invoice", module_path, semantic_provider="lsp")

    assert payload["definitions"][0]["name"] == "create_invoice"
    # The user-visible `path` field must still report the exact FILE, unaffected by the
    # internal workspace-root directory fix.
    assert payload["path"] == str(module_path.resolve())


def test_repo_map_defs_file_path_symbol_not_found_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same crash is reachable even when the symbol is absent from the file:
    `_external_workspace_symbols` runs unconditionally before any native-match
    check, so a bad `workspace_root` crashes regardless of whether the symbol
    resolves."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            pass

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert workspace_root.is_dir()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            assert workspace_root.is_dir()
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_symbol_defs(
        "ThisSymbolDoesNotExist", module_path, semantic_provider="lsp"
    )

    assert payload["definitions"] == []


def test_repo_map_defs_directory_path_with_lsp_provider_workspace_root_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a DIRECTORY path must keep using itself (not its parent) as the
    LSP workspace_root -- unchanged from before this fix."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            pass

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            assert workspace_root == tmp_path.resolve()
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["definitions"][0]["name"] == "create_invoice"


def test_repo_map_source_file_path_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg source <file> <symbol> --provider lsp` inherits defs' internal
    `_external_definitions` call and used to crash identically."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    captured_roots: list[Path] = []

    def _fake_external_workspace_symbols(root, symbol, **kwargs):
        captured_roots.append(root)
        return []

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", _fake_external_workspace_symbols)

    payload = repo_map.build_symbol_source("create_invoice", module_path, semantic_provider="lsp")

    assert captured_roots, "expected _external_workspace_symbols to be invoked"
    assert all(root.is_dir() for root in captured_roots)
    assert payload["sources"][0]["name"] == "create_invoice"


def test_repo_map_impact_file_path_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg impact <file> <symbol> --provider lsp` inherits defs' internal
    `_external_definitions` call and used to crash identically."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    captured_roots: list[Path] = []

    def _fake_external_workspace_symbols(root, symbol, **kwargs):
        captured_roots.append(root)
        return []

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", _fake_external_workspace_symbols)

    payload = repo_map.build_symbol_impact("create_invoice", module_path, semantic_provider="lsp")

    assert captured_roots, "expected _external_workspace_symbols to be invoked"
    assert all(root.is_dir() for root in captured_roots)
    assert payload["definitions"][0]["name"] == "create_invoice"


def test_repo_map_blast_radius_file_path_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg blast-radius <file> <symbol> --provider lsp` inherits defs' internal
    `_external_definitions` call and used to crash identically."""
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    captured_roots: list[Path] = []

    def _fake_external_workspace_symbols(root, symbol, **kwargs):
        captured_roots.append(root)
        return []

    def _fake_external_references(root, symbol, definitions):
        captured_roots.append(root)
        return []

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", _fake_external_workspace_symbols)
    monkeypatch.setattr(repo_map, "_external_references", _fake_external_references)

    payload = repo_map.build_symbol_blast_radius(
        "create_invoice", module_path, semantic_provider="lsp"
    )

    assert captured_roots, "expected external LSP helpers to be invoked"
    assert all(root.is_dir() for root in captured_roots)
    assert payload["definitions"][0]["name"] == "create_invoice"


def test_repo_map_refs_file_path_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg refs <file> <symbol> --provider lsp` has its OWN independent repo_root
    computation (distinct from defs' internal one) feeding `_external_references` --
    must be fixed at its own site too, or this crashes even after defs itself is fixed."""
    module_path = tmp_path / "module.py"
    module_path.write_text(
        "def create_invoice() -> None:\n    return None\n\n\ncreate_invoice()\n", encoding="utf-8"
    )

    captured_roots: list[Path] = []

    def _fake_external_workspace_symbols(root, symbol, **kwargs):
        captured_roots.append(root)
        return []

    def _fake_external_references(root, symbol, definitions):
        captured_roots.append(root)
        return []

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", _fake_external_workspace_symbols)
    monkeypatch.setattr(repo_map, "_external_references", _fake_external_references)

    payload = repo_map.build_symbol_refs("create_invoice", module_path, semantic_provider="lsp")

    assert captured_roots, "expected external LSP helpers to be invoked"
    assert all(root.is_dir() for root in captured_roots)
    assert payload["definitions"][0]["name"] == "create_invoice"


def test_repo_map_callers_file_path_with_lsp_provider_no_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tg callers <file> <symbol> --provider lsp` has its OWN independent repo_root
    computation (distinct from defs' internal one) feeding `_external_references` --
    must be fixed at its own site too, or this crashes even after defs itself is fixed."""
    module_path = tmp_path / "module.py"
    module_path.write_text(
        "def create_invoice() -> None:\n    return None\n\n\ncreate_invoice()\n", encoding="utf-8"
    )

    captured_roots: list[Path] = []

    def _fake_external_workspace_symbols(root, symbol, **kwargs):
        captured_roots.append(root)
        return []

    def _fake_external_references(root, symbol, definitions):
        captured_roots.append(root)
        return []

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", _fake_external_workspace_symbols)
    monkeypatch.setattr(repo_map, "_external_references", _fake_external_references)

    payload = repo_map.build_symbol_callers("create_invoice", module_path, semantic_provider="lsp")

    assert captured_roots, "expected external LSP helpers to be invoked"
    assert all(root.is_dir() for root in captured_roots)
    assert payload["definitions"][0]["name"] == "create_invoice"
