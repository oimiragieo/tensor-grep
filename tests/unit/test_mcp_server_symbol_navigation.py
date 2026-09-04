"""MCP symbol navigation / imports / impact contracts."""

import json
from unittest.mock import patch

from tensor_grep.cli import repo_map
from tests.unit.test_mcp_server_shared import (
    _assert_enriched_edit_plan_seed,
    _without_profiling,
)


def test_tg_symbol_blast_radius_render_returns_prompt_ready_radius_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )

    assert payload["routing_reason"] == "symbol-blast-radius-render"
    assert payload["symbol"] == "create_invoice"
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["sources"][0]["name"] == "create_invoice"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )
    assert "create_invoice" in payload["rendered_context"]


def test_tg_symbol_blast_radius_render_profile_includes_profiling_without_changing_output(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "def create_invoice(total):\n    return total + 1\n",
        encoding="utf-8",
    )
    (src_dir / "service.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    (tests_dir / "test_service.py").write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    baseline = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
        )
    )
    profiled = json.loads(
        mcp_server.tg_symbol_blast_radius_render(
            "create_invoice",
            str(project),
            max_depth=1,
            max_render_chars=400,
            profile=True,
        )
    )

    assert "_profiling" not in baseline
    assert profiled["_profiling"]["phases"]
    assert _without_profiling(profiled) == baseline


def test_tg_symbol_blast_radius_plan_returns_machine_readable_bundle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_service.py"
    test_path.write_text(
        "from src.service import build_invoice\n\n"
        "def test_build_invoice():\n"
        "    assert build_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius_plan("create_invoice", str(project), max_depth=1)
    )

    assert payload["routing_reason"] == "symbol-blast-radius-plan"
    assert "rendered_context" not in payload
    assert "sources" not in payload
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["edit_plan_seed"]["primary_test"] == str(test_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["file"] == str(module_path.resolve())
    assert payload["candidate_edit_targets"]["spans"][0]["symbol"] == "create_invoice"
    _assert_enriched_edit_plan_seed(
        payload["edit_plan_seed"],
        primary_file=module_path,
        primary_symbol_name="create_invoice",
    )


def test_tg_symbol_defs_returns_exact_definition_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_defs("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-defs"
    assert (
        payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert payload["symbol"] == "create_invoice"
    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert payload["graph_completeness"] == "strong"


def test_tg_symbol_defs_can_find_rust_and_typescript_symbols(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_defs("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_defs("issue_invoice", str(project)))

    assert (
        ts_payload["coverage"]["language_scope"]
        == "c-cpp-csharp-go-java-javascript-php-python-rust-typescript"
    )
    assert ts_payload["definitions"][0]["file"] == str(ts_path.resolve())
    assert ts_payload["definitions"][0]["kind"] == "function"
    assert rust_payload["definitions"][0]["file"] == str(rust_path.resolve())
    assert rust_payload["definitions"][0]["kind"] == "function"


def test_tg_symbol_source_returns_exact_python_function_body(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    subtotal = total + tax\n    return subtotal\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_source("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-source"
    assert payload["symbol"] == "create_invoice"
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["sources"][0]["start_line"] == 1
    assert payload["sources"][0]["end_line"] == 3
    assert "subtotal = total + tax" in payload["sources"][0]["source"]


def test_tg_symbol_source_can_extract_typescript_and_rust_blocks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  const subtotal = total + 1;\n"
        "  return subtotal;\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n    let subtotal = 1;\n    subtotal\n}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_source("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_source("issue_invoice", str(project)))

    assert ts_payload["sources"][0]["file"] == str(ts_path.resolve())
    assert "const subtotal = total + 1;" in ts_payload["sources"][0]["source"]
    assert rust_payload["sources"][0]["file"] == str(rust_path.resolve())
    assert "let subtotal = 1;" in rust_payload["sources"][0]["source"]


def test_tg_symbol_impact_returns_related_files_and_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-impact"
    assert payload["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert payload["symbol"] == "create_invoice"
    assert payload["files"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        entry["file"] == str(other_path.resolve()) and entry["provenance"] == "python-ast"
        for entry in payload["imports"]
    )


def test_tg_symbol_impact_uses_bounded_repo_scan_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    seen: dict[str, object] = {}

    def _fake_build_symbol_impact(
        symbol,
        path=".",
        *,
        semantic_provider="native",
        max_repo_files=None,
        deadline_seconds=None,
    ):
        seen["symbol"] = symbol
        seen["path"] = path
        seen["semantic_provider"] = semantic_provider
        seen["max_repo_files"] = max_repo_files
        seen["deadline_seconds"] = deadline_seconds
        return {
            "version": 1,
            "routing_backend": "RepoMap",
            "routing_reason": "symbol-impact",
            "sidecar_used": False,
            "symbol": symbol,
            "path": str(path),
            "files": [],
            "tests": [],
            "scan_limit": {
                "max_repo_files": max_repo_files,
                "scanned_files": 0,
                "possibly_truncated": False,
            },
        }

    monkeypatch.setattr(mcp_server, "build_symbol_impact", _fake_build_symbol_impact)

    payload = json.loads(mcp_server.tg_symbol_impact("safeParseJSON", str(tmp_path)))

    # Cluster A cap-value decision (Fable completeness review): the MCP default was raised
    # 512 -> 2000 to match the CLI's routing-accuracy default; tg_symbol_impact's *behavior*
    # (forwarding the shared default) is unchanged, so assert against the constant rather
    # than a value that would silently go stale on the next cap-value change.
    assert payload["scan_limit"]["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT
    assert seen["max_repo_files"] == mcp_server._DEFAULT_MCP_REPO_SCAN_LIMIT


def test_tg_symbol_impact_prefers_import_linked_typescript_and_rust_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    ts_test_path = tests_dir / "invoice_flow.spec.ts"
    ts_test_path.write_text(
        'import { createInvoice } from "../src/payments";\n'
        "test('invoice', () => expect(createInvoice(1)).toBe(1));\n",
        encoding="utf-8",
    )
    rust_test_path = tests_dir / "integration_checks.rs"
    rust_test_path.write_text(
        "use crate::billing::issue_invoice;\n\n"
        "#[test]\n"
        "fn invoice_smoke() {\n"
        "    assert_eq!(issue_invoice(), 1);\n"
        "}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_impact("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_impact("issue_invoice", str(project)))

    assert ts_payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert ts_payload["tests"][0] == str(ts_test_path.resolve())
    assert rust_payload["tests"][0] == str(rust_test_path.resolve())


def test_tg_symbol_impact_prefers_import_linked_source_files_over_name_only_matches(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    notes_dir = project / "notes"
    src_dir.mkdir(parents=True)
    notes_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    importer_path = src_dir / "billing.py"
    importer_path.write_text(
        "from src.payments import create_invoice\n\ndef bill():\n    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    noisy_path = notes_dir / "invoice_notes.py"
    noisy_path.write_text("def placeholder():\n    return 'invoice'\n", encoding="utf-8")

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    assert payload["files"][0] == str(module_path.resolve())
    assert payload["files"][1] == str(importer_path.resolve())
    assert str(noisy_path.resolve()) not in payload["files"][:2]


def test_tg_symbol_refs_returns_python_reference_sites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\nresult = create_invoice(10, 2)\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_refs("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-refs"
    assert payload["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert payload["graph_completeness"] == "moderate"
    assert any(ref["provenance"] == "python-ast" for ref in payload["references"])
    assert any(ref["file"] == str(other_path.resolve()) for ref in payload["references"])


def test_tg_symbol_refs_and_callers_include_typescript_and_rust_heuristics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(10);\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "pub fn issue_invoice() -> usize {\n"
        "    1\n"
        "}\n\n"
        "pub fn settle_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    ts_refs = json.loads(mcp_server.tg_symbol_refs("createInvoice", str(project)))
    ts_callers = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))
    rust_refs = json.loads(mcp_server.tg_symbol_refs("issue_invoice", str(project)))
    rust_callers = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert ts_refs["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert any(ref["file"] == str(ts_path.resolve()) for ref in ts_refs["references"])
    assert any(
        ref["provenance"] in {"tree-sitter", "regex-heuristic"} for ref in ts_refs["references"]
    )
    assert any(caller["file"] == str(ts_path.resolve()) for caller in ts_callers["callers"])
    assert any(ref["file"] == str(rust_path.resolve()) for ref in rust_refs["references"])
    assert any(
        ref["provenance"] in {"tree-sitter", "regex-heuristic"} for ref in rust_refs["references"]
    )
    assert any(caller["file"] == str(rust_path.resolve()) for caller in rust_callers["callers"])


def test_tg_symbol_callers_returns_python_call_sites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    other_path = src_dir / "billing.py"
    other_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(10, 2)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_payments.py"
    test_path.write_text(
        "from src.payments import create_invoice\n\nassert create_invoice(1, 2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("create_invoice", str(project)))

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-callers"
    assert payload["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert any(caller["file"] == str(other_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(
        symbol["name"] == "create_invoice" and symbol["score"] > 0 for symbol in payload["symbols"]
    )
    assert payload["related_paths"][0] == str(module_path.resolve())
    assert str(other_path.resolve()) not in payload["related_paths"][:1]


def test_tg_symbol_callers_prefers_import_linked_typescript_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(10);\n"
        "}\n",
        encoding="utf-8",
    )
    ts_test_path = tests_dir / "invoice_flow.spec.ts"
    ts_test_path.write_text(
        'import { createInvoice } from "../src/payments";\n'
        "test('invoice', () => expect(createInvoice(1)).toBe(1));\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert payload["coverage"]["test_matching"] == "filename+import+graph-heuristic"
    assert any(caller["file"] == str(ts_path.resolve()) for caller in payload["callers"])
    assert payload["tests"][0] == str(ts_test_path.resolve())


def test_tg_symbol_blast_radius_returns_transitive_call_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.py"
    module_path.write_text("def create_invoice(total):\n    return total + 1\n", encoding="utf-8")
    service_path = src_dir / "service.py"
    service_path.write_text(
        "from src.payments import create_invoice\n\n"
        "def build_invoice(total):\n"
        "    return create_invoice(total)\n",
        encoding="utf-8",
    )
    api_path = src_dir / "api.py"
    api_path.write_text(
        "from src.service import build_invoice\n\n"
        "def post_invoice(total):\n"
        "    return build_invoice(total)\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "test_api.py"
    test_path.write_text(
        "from src.api import post_invoice\n\n"
        "def test_post_invoice():\n"
        "    assert post_invoice(2) == 3\n",
        encoding="utf-8",
    )

    payload = json.loads(
        mcp_server.tg_symbol_blast_radius("create_invoice", str(project), max_depth=2)
    )

    assert payload["routing_backend"] == "RepoMap"
    assert payload["routing_reason"] == "symbol-blast-radius"
    assert payload["coverage"]["symbol_navigation"] == repo_map._symbol_navigation_descriptor()
    assert payload["symbol"] == "create_invoice"
    assert payload["max_depth"] == 2
    assert payload["definitions"][0]["file"] == str(module_path.resolve())
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert any(caller["file"] == str(service_path.resolve()) for caller in payload["callers"])
    assert any(caller["provenance"] == "python-ast" for caller in payload["callers"])
    assert payload["files"][0] == str(module_path.resolve())
    assert str(service_path.resolve()) in payload["files"]
    assert str(api_path.resolve()) in payload["files"]
    assert payload["tests"][0] == str(test_path.resolve())
    assert any(level["depth"] == 0 for level in payload["caller_tree"])
    assert any(level["depth"] == 1 for level in payload["caller_tree"])
    assert all("graph-derived" in level["provenance"] for level in payload["caller_tree"])
    assert all(level["graph_completeness"] == "moderate" for level in payload["caller_tree"])
    assert all(
        level["edge_summary"]["edge_kind"] == "reverse-import" for level in payload["caller_tree"]
    )
    assert all("confidence" in level["edge_summary"] for level in payload["caller_tree"])
    assert payload["graph_trust_summary"]["edge_kind"] == "reverse-import"
    assert payload["graph_trust_summary"]["depth_count"] >= 1
    assert "graph-derived" in payload["graph_trust_summary"]["provenance"]
    assert "Depth 0:" in payload["rendered_caller_tree"]


# --- M11: uniform exception sanitization -- a KeyError/AttributeError must return a
# structured error, not propagate a raw traceback out of the MCP call. ---


def test_tg_symbol_blast_radius_returns_structured_error_on_unexpected_exception(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_blast_radius",
        side_effect=KeyError("boom"),
    ):
        out = mcp_server.tg_symbol_blast_radius("create_invoice", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False
    assert "KeyError" in payload["error"]["message"]


def test_tg_symbol_blast_radius_render_returns_structured_error_on_unexpected_exception(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    with patch(
        "tensor_grep.cli.mcp_server.build_symbol_blast_radius_render",
        side_effect=AttributeError("boom"),
    ):
        out = mcp_server.tg_symbol_blast_radius_render("create_invoice", str(tmp_path))

    payload = json.loads(out)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["retryable"] is False


def test_tg_symbol_impact_can_rank_tests_through_transitive_import_chain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    payments_path = src_dir / "payments.ts"
    payments_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    workflow_path = src_dir / "workflow.ts"
    workflow_path.write_text(
        'import { createInvoice } from "./payments";\n\n'
        "export function runWorkflow() {\n"
        "  return createInvoice(1);\n"
        "}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.ts"
    ui_path.write_text(
        'import { runWorkflow } from "./workflow";\n\n'
        "export function renderInvoice() {\n"
        "  return runWorkflow();\n"
        "}\n",
        encoding="utf-8",
    )
    test_path = tests_dir / "ui_flow.spec.ts"
    test_path.write_text(
        'import { renderInvoice } from "../src/ui";\n'
        "test('invoice', () => expect(renderInvoice()).toBe(1));\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("createInvoice", str(project)))

    assert payload["tests"][0] == str(test_path.resolve())
    assert payload["test_matches"][0]["path"] == str(test_path.resolve())
    assert "test-graph" in payload["test_matches"][0]["reasons"]
    assert payload["test_matches"][0]["association"]["edge_kind"] in {"import-graph", "hybrid"}
    assert payload["test_matches"][0]["association"]["confidence"] in {"strong", "moderate"}


def test_tg_symbol_impact_orders_tests_by_graph_score(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    (src_dir / "payments.py").write_text(
        "def create_invoice(total, tax):\n    return total + tax\n",
        encoding="utf-8",
    )
    (src_dir / "z_billing.py").write_text(
        "from src.payments import create_invoice\n\n"
        "def invoice_total():\n"
        "    return create_invoice(1, 2)\n",
        encoding="utf-8",
    )
    (src_dir / "a_cli.py").write_text(
        "from src.payments import create_invoice\n\ndef run():\n    return create_invoice(2, 3)\n",
        encoding="utf-8",
    )
    (src_dir / "ui.py").write_text(
        "from src.z_billing import invoice_total\n\ndef render():\n    return invoice_total()\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "test_ui_flow.py"
    ui_test.write_text(
        "from src.ui import render\n\ndef test_render():\n    assert render() == 3\n",
        encoding="utf-8",
    )
    cli_test = tests_dir / "test_cli_flow.py"
    cli_test.write_text(
        "from src.a_cli import run\n\ndef test_run():\n    assert run() == 5\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_impact("create_invoice", str(project)))

    ui_match = next(
        item for item in payload["test_matches"] if item["path"] == str(ui_test.resolve())
    )
    cli_match = next(
        item for item in payload["test_matches"] if item["path"] == str(cli_test.resolve())
    )
    ordered_by_score = [
        item["path"]
        for item in sorted(
            payload["test_matches"],
            key=lambda item: (-float(item["graph_score"]), str(item["path"])),
        )
    ]
    assert payload["tests"] == ordered_by_score
    assert cli_match["graph_score"] > ui_match["graph_score"]
    assert "graph-derived" in cli_match["association"]["provenance"]
    assert cli_match["association"]["confidence"] in {"strong", "moderate"}


def test_tg_symbol_callers_uses_parser_backed_javascript_calls_not_string_noise(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.js"
    api_path.write_text(
        "export function createInvoice(total) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.js"
    consumer_path.write_text(
        'import { createInvoice } from "./payments";\n'
        'const note = "createInvoice(1)";\n'
        "// createInvoice(2)\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 5


def test_tg_symbol_callers_uses_parser_backed_typescript_calls_not_string_noise(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.ts"
    api_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.ts"
    consumer_path.write_text(
        'import { createInvoice } from "./payments";\n'
        'const note: string = "createInvoice(1)";\n'
        "// createInvoice(2)\n"
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 5


def test_tg_symbol_callers_uses_parser_backed_rust_calls_not_string_noise(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "billing.rs"
    api_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        'const NOTE: &str = "issue_invoice()";\n'
        "// issue_invoice();\n"
        "pub fn render_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 4


def test_tg_symbol_callers_resolves_javascript_namespace_import_aliases(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "payments.js"
    api_path.write_text(
        "export function createInvoice(total) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.js"
    consumer_path.write_text(
        'import * as paymentsApi from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return paymentsApi.createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 3


def test_tg_symbol_callers_resolves_rust_module_alias_use_chains(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    api_path = src_dir / "billing.rs"
    api_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        "use crate::billing as billing_api;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    billing_api::issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    consumer_calls = [
        caller for caller in payload["callers"] if caller["file"] == str(consumer_path.resolve())
    ]
    assert len(consumer_calls) == 1
    assert consumer_calls[0]["line"] == 4


def test_tg_symbol_callers_prefers_typescript_definition_selected_by_namespace_import(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    admin_dir = src_dir / "admin"
    src_dir.mkdir(parents=True)
    admin_dir.mkdir()

    preferred_path = src_dir / "payments.ts"
    preferred_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    other_path = admin_dir / "payments.ts"
    other_path.write_text(
        "export function createInvoice(total: number) {\n  return total * 2;\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.ts"
    consumer_path.write_text(
        'import * as paymentsApi from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return paymentsApi.createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "createInvoice"
    assert payload["definitions"][0]["kind"] == "function"
    assert payload["definitions"][0]["file"] == str(preferred_path.resolve())
    assert payload["definitions"][0]["line"] == 1
    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])
    assert all(
        definition["file"] != str(other_path.resolve()) for definition in payload["definitions"]
    )


def test_tg_symbol_callers_prefers_rust_definition_selected_by_module_alias_use_chain(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    other_dir = src_dir / "other"
    src_dir.mkdir(parents=True)
    other_dir.mkdir()

    preferred_path = src_dir / "billing.rs"
    preferred_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    other_path = other_dir / "billing.rs"
    other_path.write_text(
        "pub fn issue_invoice() -> usize {\n    2\n}\n",
        encoding="utf-8",
    )
    consumer_path = src_dir / "consumer.rs"
    consumer_path.write_text(
        "use crate::billing as billing_api;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    billing_api::issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["name"] == "issue_invoice"
    assert payload["definitions"][0]["kind"] == "function"
    assert payload["definitions"][0]["file"] == str(preferred_path.resolve())
    assert payload["definitions"][0]["line"] == 1
    assert any(caller["file"] == str(consumer_path.resolve()) for caller in payload["callers"])
    assert all(
        definition["file"] != str(other_path.resolve()) for definition in payload["definitions"]
    )


def test_tg_symbol_callers_prefers_typescript_tests_importing_direct_callers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "payments.ts"
    module_path.write_text(
        "export function createInvoice(total: number) {\n  return total;\n}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.ts"
    ui_path.write_text(
        'import { createInvoice } from "./payments";\n'
        "export function renderInvoice() {\n"
        "  return createInvoice(3);\n"
        "}\n",
        encoding="utf-8",
    )
    cli_path = src_dir / "cli.ts"
    cli_path.write_text(
        'import { renderInvoice } from "./ui";\n'
        "export function runCli() {\n"
        "  return renderInvoice();\n"
        "}\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "ui_flow.spec.ts"
    ui_test.write_text(
        'import { renderInvoice } from "../src/ui";\n'
        'test("invoice", () => expect(renderInvoice()).toBe(3));\n',
        encoding="utf-8",
    )
    cli_test = tests_dir / "cli_flow.spec.ts"
    cli_test.write_text(
        'import { runCli } from "../src/cli";\n'
        'test("invoice cli", () => expect(runCli()).toBe(3));\n',
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("createInvoice", str(project)))

    assert payload["tests"].index(str(ui_test.resolve())) < payload["tests"].index(
        str(cli_test.resolve())
    )


def test_tg_symbol_callers_prefers_rust_tests_importing_direct_callers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    tests_dir = project / "tests"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir()

    module_path = src_dir / "billing.rs"
    module_path.write_text(
        "pub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )
    ui_path = src_dir / "ui.rs"
    ui_path.write_text(
        "use crate::billing::issue_invoice;\n\n"
        "pub fn render_invoice() -> usize {\n"
        "    issue_invoice()\n"
        "}\n",
        encoding="utf-8",
    )
    cli_path = src_dir / "cli.rs"
    cli_path.write_text(
        "use crate::ui::render_invoice;\n\npub fn run_cli() -> usize {\n    render_invoice()\n}\n",
        encoding="utf-8",
    )
    ui_test = tests_dir / "ui_flow.rs"
    ui_test.write_text(
        "use crate::ui::render_invoice;\n\n"
        "#[test]\n"
        "fn renders_invoice() {\n"
        "    assert_eq!(render_invoice(), 1);\n"
        "}\n",
        encoding="utf-8",
    )
    cli_test = tests_dir / "cli_flow.rs"
    cli_test.write_text(
        "use crate::cli::run_cli;\n\n"
        "#[test]\n"
        "fn runs_invoice_cli() {\n"
        "    assert_eq!(run_cli(), 1);\n"
        "}\n",
        encoding="utf-8",
    )

    payload = json.loads(mcp_server.tg_symbol_callers("issue_invoice", str(project)))

    assert payload["tests"].index(str(ui_test.resolve())) < payload["tests"].index(
        str(cli_test.resolve())
    )


def test_tg_symbol_source_ignores_comment_noise_for_typescript_and_rust(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from tensor_grep.cli import mcp_server

    project = tmp_path / "project"
    src_dir = project / "src"
    src_dir.mkdir(parents=True)

    ts_path = src_dir / "payments.ts"
    ts_path.write_text(
        "// export function createInvoice() {}\n"
        "export function createInvoice(total: number) {\n"
        "  return total;\n"
        "}\n",
        encoding="utf-8",
    )
    rust_path = src_dir / "billing.rs"
    rust_path.write_text(
        "// pub fn issue_invoice() -> usize { 0 }\npub fn issue_invoice() -> usize {\n    1\n}\n",
        encoding="utf-8",
    )

    ts_payload = json.loads(mcp_server.tg_symbol_source("createInvoice", str(project)))
    rust_payload = json.loads(mcp_server.tg_symbol_source("issue_invoice", str(project)))

    assert ts_payload["sources"][0]["file"] == str(ts_path.resolve())
    assert ts_payload["sources"][0]["start_line"] == 2
    assert "return total;" in ts_payload["sources"][0]["source"]

    assert rust_payload["sources"][0]["file"] == str(rust_path.resolve())
    assert rust_payload["sources"][0]["start_line"] == 2
    assert "1" in rust_payload["sources"][0]["source"]
