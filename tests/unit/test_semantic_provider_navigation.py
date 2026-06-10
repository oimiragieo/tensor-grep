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
                "lsp_provider_response": True,
                "lsp_operation": "workspace/symbol",
            }
        ],
    )

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["provider_agreement"]["agreement_status"] == "lsp-only"
    assert payload["provider_status"]["mode"] == "lsp"
    assert payload["lsp_evidence_status"] == "lsp_proof"
    assert payload["lsp_proof"] is True


def test_repo_map_defs_can_confirm_native_anchor_with_lsp_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    requests: list[str] = []

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            assert kwargs["uri"] == module_path.resolve().as_uri()

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            requests.append(method)
            if method == "workspace/symbol":
                return []
            assert method == "textDocument/definition"
            return [
                {
                    "uri": module_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 18},
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert requests == ["workspace/symbol", "textDocument/definition"]
    assert payload["lsp_proof"] is True
    assert payload["lsp_evidence_status"] == "lsp_proof"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["definitions"][0]["lsp_provider_response"] is True
    assert payload["definitions"][0]["lsp_operation"] == "textDocument/definition"
    assert payload["definitions"][0]["lsp_resolution_basis"] == "native-definition-anchor"


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
                "lsp_provider_response": True,
                "lsp_operation": "workspace/symbol",
            }
        ],
    )

    payload = repo_map.build_symbol_source("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert payload["definitions"][0]["provenance"] == "lsp-python"
    assert payload["provider_status"]["mode"] == "lsp"


def test_repo_map_source_carries_lsp_definition_anchor_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            assert kwargs["uri"] == module_path.resolve().as_uri()

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            if method == "workspace/symbol":
                return []
            assert method == "textDocument/definition"
            return [
                {
                    "uri": module_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 1, "character": 15},
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_symbol_source("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["lsp_proof"] is True
    assert payload["definitions"][0]["lsp_operation"] == "textDocument/definition"
    assert payload["sources"][0]["name"] == "create_invoice"


def test_context_render_lsp_provider_marks_primary_target_with_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            assert kwargs["uri"] == module_path.resolve().as_uri()

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            if method == "workspace/symbol":
                return []
            assert method == "textDocument/definition"
            return [
                {
                    "uri": module_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 1, "character": 15},
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = repo_map.build_context_render(
        "change create_invoice behavior",
        tmp_path,
        semantic_provider="lsp",
        max_tokens=None,
    )

    assert payload["semantic_provider"] == "lsp"
    assert payload["edit_plan_seed"]["primary_symbol"]["lsp_proof"] is True
    assert payload["edit_plan_seed"]["primary_span"] == {"start_line": 1, "end_line": 2}
    primary_target = payload["navigation_pack"]["primary_target"]
    assert primary_target["lsp_proof"] is True
    assert primary_target["lsp_operation"] == "textDocument/definition"


def test_agent_capsule_lsp_provider_carries_primary_target_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import agent_capsule

    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            assert kwargs["uri"] == module_path.resolve().as_uri()

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            if method == "workspace/symbol":
                return []
            assert method == "textDocument/definition"
            return [
                {
                    "uri": module_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 0, "character": 4},
                        "end": {"line": 1, "character": 15},
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "ready",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        semantic_provider="lsp",
        max_tokens=None,
    )

    assert payload["semantic_provider"] == "lsp"
    assert payload["primary_target"]["lsp_proof"] is True
    assert payload["primary_target"]["lsp_operation"] == "textDocument/definition"
    assert "lsp-confirmed" in payload["primary_target"]["evidence"]
    assert "--provider" in payload["raw_context_ref"]["argv"]
    assert "lsp" in payload["raw_context_ref"]["argv"]


def test_agent_context_native_provider_does_not_probe_lsp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tensor_grep.cli import agent_capsule

    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    def fail_lsp_probe(*args: object, **kwargs: object) -> list[dict[str, object]]:
        _ = args
        _ = kwargs
        raise AssertionError("native provider must not query external LSP providers")

    monkeypatch.setattr(repo_map, "_external_workspace_symbols", fail_lsp_probe)
    monkeypatch.setattr(repo_map, "_external_definitions", fail_lsp_probe)

    context_payload = repo_map.build_context_render(
        "change create_invoice behavior",
        tmp_path,
        semantic_provider="native",
        max_tokens=None,
    )
    capsule_payload = agent_capsule.build_agent_capsule(
        "change create_invoice behavior",
        tmp_path,
        semantic_provider="native",
        max_tokens=None,
    )

    assert context_payload["semantic_provider"] == "native"
    assert context_payload["navigation_pack"]["primary_target"].get("lsp_proof") is None
    assert capsule_payload["semantic_provider"] == "native"
    assert capsule_payload["primary_target"].get("lsp_proof") is None
    assert "--provider" not in capsule_payload["raw_context_ref"]["argv"]


def test_cli_context_render_accepts_provider_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_context_render_json",
        lambda query, path, semantic_provider="native", **_: json.dumps({
            "query": query,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app,
        [
            "context-render",
            str(tmp_path),
            "--query",
            "create invoice",
            "--provider",
            "lsp",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["semantic_provider"] == "lsp"


def test_cli_edit_plan_accepts_provider_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repo_map,
        "build_context_edit_plan_json",
        lambda query, path, semantic_provider="native", **_: json.dumps({
            "query": query,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app,
        ["edit-plan", str(tmp_path), "--query", "create invoice", "--provider", "hybrid", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["semantic_provider"] == "hybrid"


def test_cli_agent_accepts_provider_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tensor_grep.cli import agent_capsule

    monkeypatch.setattr(
        agent_capsule,
        "build_agent_capsule_json",
        lambda query, path, semantic_provider="native", **_: json.dumps({
            "query": query,
            "path": str(path),
            "semantic_provider": semantic_provider,
        }),
    )

    result = CliRunner().invoke(
        app,
        ["agent", str(tmp_path), "--query", "create invoice", "--provider", "lsp", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["semantic_provider"] == "lsp"


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

    _disable_external_definition_proof(monkeypatch)
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
                "lsp_provider_response": True,
                "lsp_operation": "textDocument/references",
            }
        ],
    )

    payload = repo_map.build_symbol_refs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert payload["semantic_provider"] == "hybrid"
    assert any(current["provenance"] == "lsp-python" for current in payload["references"])
    assert any(current["file"] == str(consumer_path.resolve()) for current in payload["references"])
    assert payload["provider_agreement"]["agreement_status"] in {"diverged", "agreed"}
    assert payload["provider_status"]["mode"] == "hybrid"
    assert payload["lsp_evidence_status"] == "lsp_proof"
    assert payload["lsp_proof"] is True


def test_repo_map_lsp_fallback_reports_not_lsp_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )

    _disable_external_definition_proof(monkeypatch)

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["semantic_provider"] == "lsp"
    assert payload["provider_agreement"]["agreement_status"] == "fallback-native"
    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False
    assert "native fallback" in payload["not_lsp_proof_reason"].lower()


def test_repo_map_hybrid_defs_fallback_reports_native_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n",
        encoding="utf-8",
    )

    _disable_external_definition_proof(monkeypatch)

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert payload["definitions"][0]["file"] == str(service_path.resolve())
    assert payload["provider_agreement"]["agreement_status"] == "fallback-native"
    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False


def test_repo_map_hybrid_refs_fallback_reports_native_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n",
        encoding="utf-8",
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    _disable_external_definition_proof(monkeypatch)
    monkeypatch.setattr(repo_map, "_external_references", lambda root, symbol, definitions: [])

    payload = repo_map.build_symbol_refs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert any(current["file"] == str(consumer_path.resolve()) for current in payload["references"])
    assert payload["provider_agreement"]["agreement_status"] == "fallback-native"
    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False


def test_external_lsp_workspace_symbol_queries_are_operation_budgeted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    query_timeouts: list[tuple[float, float]] = []

    class _SlowClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 15.0

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            query_timeouts.append((
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _SlowClient:
            return _SlowClient()

    monkeypatch.setenv("TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS", "0.25")
    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    repo_map._external_workspace_symbols(
        tmp_path,
        "create_invoice",
        repo_map={
            "path": str(tmp_path),
            "symbols": [
                {
                    "name": "create_invoice",
                    "file": str(service_path),
                    "line": 1,
                    "kind": "function",
                }
            ],
        },
    )

    assert query_timeouts
    request_timeout, initialize_timeout = query_timeouts[0]
    assert request_timeout <= 0.25
    assert initialize_timeout <= 0.25


def test_external_lsp_workspace_symbol_default_budget_is_agent_loop_sized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    query_timeouts: list[tuple[float, float]] = []

    class _SlowClient:
        request_timeout_seconds = 30.0
        initialize_timeout_seconds = 30.0

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            _ = method
            _ = params
            query_timeouts.append((
                self.request_timeout_seconds,
                self.initialize_timeout_seconds,
            ))
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _SlowClient:
            _ = language
            _ = workspace_root
            return _SlowClient()

    monkeypatch.delenv("TENSOR_GREP_LSP_OPERATION_BUDGET_SECONDS", raising=False)
    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    repo_map._external_workspace_symbols(
        tmp_path,
        "create_invoice",
        repo_map={
            "path": str(tmp_path),
            "symbols": [
                {
                    "name": "create_invoice",
                    "file": str(service_path),
                    "line": 1,
                    "kind": "function",
                }
            ],
        },
    )

    assert query_timeouts
    request_timeout, initialize_timeout = query_timeouts[0]
    assert request_timeout <= 2.0
    assert initialize_timeout <= 2.0


def test_external_workspace_symbols_decodes_percent_encoded_file_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "source dir"
    source_dir.mkdir()
    module_path = source_dir / "invoice module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            assert method == "workspace/symbol"
            return [
                {
                    "name": "create_invoice",
                    "kind": 12,
                    "location": {
                        "uri": module_path.resolve().as_uri(),
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 18},
                        },
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    matches = repo_map._external_workspace_symbols(
        tmp_path,
        "create_invoice",
        repo_map={
            "path": str(tmp_path),
            "files": [str(module_path)],
            "symbols": [
                {
                    "name": "create_invoice",
                    "file": str(module_path),
                    "line": 1,
                    "kind": "function",
                }
            ],
        },
    )

    assert matches[0]["file"] == str(module_path.resolve())
    assert matches[0]["lsp_provider_response"] is True
    assert matches[0]["lsp_operation"] == "workspace/symbol"


def test_external_references_decodes_percent_encoded_file_uri_before_reading_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service_path = tmp_path / "service.py"
    consumer_dir = tmp_path / "consumer dir"
    consumer_dir.mkdir()
    consumer_path = consumer_dir / "invoice user.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            return None

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            assert method == "textDocument/references"
            return [
                {
                    "uri": consumer_path.resolve().as_uri(),
                    "range": {
                        "start": {"line": 2, "character": 9},
                        "end": {"line": 2, "character": 23},
                    },
                }
            ]

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            assert language == "python"
            assert workspace_root == tmp_path.resolve()
            return _FakeClient()

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    refs = repo_map._external_references(
        tmp_path,
        "create_invoice",
        [{"file": str(service_path), "line": 1}],
    )

    assert refs[0]["file"] == str(consumer_path.resolve())
    assert refs[0]["text"] == "result = create_invoice(3)"
    assert refs[0]["lsp_provider_response"] is True
    assert refs[0]["lsp_operation"] == "textDocument/references"


def test_external_workspace_symbols_queries_repo_file_languages_when_symbols_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path = tmp_path / "payments.ts"
    source_path.write_text(
        "export function createInvoice() {\n  return 1;\n}\n",
        encoding="utf-8",
    )
    languages: list[str] = []

    class _FakeClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            assert method == "workspace/symbol"
            return []

    class _FakeManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _FakeClient:
            languages.append(language)
            return _FakeClient()

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _FakeManager())

    repo_map._external_workspace_symbols(
        tmp_path,
        "createInvoice",
        repo_map={
            "path": str(tmp_path),
            "files": [str(source_path)],
            "symbols": [],
        },
    )

    assert languages == ["typescript"]


def test_lsp_proof_requires_provider_response_marker_not_provenance_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False
    assert "native fallback" in payload["not_lsp_proof_reason"].lower()


def test_lsp_proof_requires_boolean_provider_response_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
                "lsp_provider_response": "false",
            }
        ],
    )

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="lsp")

    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False
    assert payload["definitions"][0]["provenance"] == "python-ast"
    assert "native fallback" in payload["not_lsp_proof_reason"].lower()


def test_repo_map_hybrid_defs_deduplicates_native_and_lsp_same_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    resolved = str(module_path.resolve())
    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol, **kwargs: [
            {
                "name": symbol,
                "kind": "function",
                "file": resolved,
                "line": 1,
                "end_line": 1,
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }
        ],
    )

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert len(payload["definitions"]) == 1
    assert payload["definitions"][0]["file"] == resolved
    assert payload["definitions"][0]["line"] == 1
    assert payload["definitions"][0]["lsp_proof"] is True
    assert payload["definitions"][0]["lsp_provider_response"] is True


def test_repo_map_hybrid_defs_deduplicates_same_line_even_when_lsp_span_is_narrower(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_path = tmp_path / "module.py"
    module_path.write_text(
        "class AgentRegistryGenerator:\n    def build(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    resolved = str(module_path.resolve())
    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol, **kwargs: [
            {
                "name": symbol,
                "kind": "class",
                "file": resolved,
                "line": 1,
                "end_line": 1,
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }
        ],
    )

    payload = repo_map.build_symbol_defs(
        "AgentRegistryGenerator", tmp_path, semantic_provider="hybrid"
    )

    assert [(row["file"], row["line"]) for row in payload["definitions"]] == [(resolved, 1)]
    assert payload["definitions"][0]["lsp_proof"] is True


def test_repo_map_hybrid_defs_deduplicates_lsp_file_uri_with_encoded_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module_dir = tmp_path / "module dir"
    module_dir.mkdir()
    module_path = module_dir / "module.py"
    module_path.write_text("def create_invoice() -> None:\n    return None\n", encoding="utf-8")
    resolved = str(module_path.resolve())
    monkeypatch.setattr(
        repo_map,
        "_external_workspace_symbols",
        lambda root, symbol, **kwargs: [
            {
                "name": symbol,
                "kind": "function",
                "file": module_path.resolve().as_uri(),
                "line": 1,
                "end_line": 1,
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_proof": True,
            }
        ],
    )

    payload = repo_map.build_symbol_defs("create_invoice", tmp_path, semantic_provider="hybrid")

    assert [(row["file"], row["line"]) for row in payload["definitions"]] == [(resolved, 1)]
    assert payload["definitions"][0]["lsp_proof"] is True


@pytest.mark.parametrize(
    ("command", "collection_key"),
    [
        ("defs", "definitions"),
        ("source", "sources"),
        ("refs", "references"),
        ("callers", "callers"),
        ("blast-radius", "callers"),
    ],
)
def test_cli_lsp_timeout_reports_explicit_native_fallback_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    collection_key: str,
) -> None:
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )

    class _TimeoutClient:
        request_timeout_seconds = 3.0
        initialize_timeout_seconds = 3.0

        def ensure_document(self, **kwargs: object) -> None:
            _ = kwargs
            raise repo_map.LSPTransportError("timeout waiting for LSP response: initialize")

        def request(self, method: str, params: dict[str, object]) -> list[dict[str, object]]:
            _ = method
            _ = params
            raise repo_map.LSPTransportError("timeout waiting for LSP response: initialize")

    class _TimeoutManager:
        def get_client(self, *, language: str, workspace_root: Path) -> _TimeoutClient:
            _ = language
            _ = workspace_root
            return _TimeoutClient()

        def provider_status(self, *, language: str, workspace_root: Path) -> dict[str, object]:
            return {
                "language": language,
                "workspace_root": str(workspace_root),
                "available": True,
                "health_status": "unhealthy",
                "health_check": "semantic-document-symbol",
                "health_phase": "initialize",
                "lsp_provider_response": False,
                "lsp_proof": False,
                "lsp_evidence_status": "fallback_native",
                "not_lsp_proof_reason": "Provider semantic health probe failed or timed out.",
                "last_error": "timeout waiting for LSP response: initialize",
            }

    monkeypatch.setattr(repo_map, "_EXTERNAL_LSP_PROVIDER_MANAGER", _TimeoutManager())

    result = CliRunner().invoke(
        app,
        [command, str(tmp_path), "--symbol", "create_invoice", "--provider", "lsp", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["semantic_provider"] == "lsp"
    assert payload["lsp_evidence_status"] == "fallback_native"
    assert payload["lsp_proof"] is False
    assert "native fallback" in payload["not_lsp_proof_reason"].lower()
    assert payload["provider_status"]["providers"][0]["lsp_proof"] is False
    assert "timed out" in payload["provider_status"]["providers"][0]["not_lsp_proof_reason"]
    assert collection_key in payload


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

    _disable_external_definition_proof(monkeypatch)
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
                "lsp_provider_response": True,
                "lsp_operation": "textDocument/references",
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
    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_json",
        lambda symbol, path, max_depth=3, semantic_provider="native", **_: json.dumps({
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
    def fake_build_symbol_blast_radius_render_json(
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
        return json.dumps({
            "symbol": symbol,
            "path": str(path),
            "max_depth": max_depth,
            "semantic_provider": semantic_provider,
        })

    monkeypatch.setattr(
        repo_map,
        "build_symbol_blast_radius_render_json",
        fake_build_symbol_blast_radius_render_json,
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
        lambda symbol, path, semantic_provider="native", max_repo_files=None: {
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
    assert payload["max_repo_files"] == 512


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
