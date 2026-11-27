from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import repo_map
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

    # PR-1 (1D): the CLI stopped calling `build_agent_capsule_json` (it now builds the payload
    # once via `build_agent_capsule` and dumps it itself so both json/text branches share the
    # exit-2-on-scan-truncation gate) -- repoint this monkeypatch to the function actually called.
    monkeypatch.setattr(
        agent_capsule,
        "build_agent_capsule",
        lambda query, path, semantic_provider="native", **_: {
            "query": query,
            "path": str(path),
            "semantic_provider": semantic_provider,
        },
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
    monkeypatch.setattr(repo_map.time, "monotonic", lambda: 100.0)  # Exact deadline arithmetic.
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
    assert request_timeout <= 10.0
    assert initialize_timeout <= 10.0


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
    """H1 regression: native finds MORE callers than a partial LSP index proves (native
    finds `create_invoice()` in both consumer.py and consumer2.py; the mocked LSP index is
    PARTIAL and only proves the consumer.py call site, exactly mirroring a real under-indexed
    LSP server). Before the H1 fix, `calls = external_calls or calls` REPLACED the native call
    list with the partial LSP list, silently dropping the consumer2.py call and stamping the
    (incomplete) result "lsp-only" / authoritative. The fix must UNION both call sites and
    report the divergence honestly instead.
    """
    service_path = tmp_path / "service.py"
    consumer_path = tmp_path / "consumer.py"
    consumer2_path = tmp_path / "consumer2.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    consumer_path.write_text(
        "from service import create_invoice\n\nresult = create_invoice(3)\n",
        encoding="utf-8",
    )
    consumer2_path.write_text(
        "from service import create_invoice\n\nresult2 = create_invoice(5)\n",
        encoding="utf-8",
    )

    _disable_external_definition_proof(monkeypatch)
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        # PARTIAL LSP index: proves only the consumer.py call site, silently missing
        # consumer2.py -- the exact H1 scenario (a partial index, not an empty one).
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
    caller_files = {str(current["file"]) for current in payload["callers"]}
    # Union: BOTH the lsp-proven call site AND the native-only call site must survive --
    # neither is silently discarded.
    assert str(consumer_path.resolve()) in caller_files
    assert str(consumer2_path.resolve()) in caller_files
    assert any(current["provenance"] == "lsp-python" for current in payload["callers"])
    # native found 2 call sites, the LSP index only proved 1 -> honestly diverged, never a
    # clean "lsp-only" authoritative stamp on an incomplete result.
    assert payload["provider_agreement"]["agreement_status"] == "diverged"
    assert payload["provider_agreement"]["native_count"] == 2
    assert payload["provider_agreement"]["lsp_count"] == 1
    assert payload["provider_agreement"]["fallback_used"] is True
    # Only the row the LSP index actually proved is stamped lsp_proof -- the native-only row
    # (consumer2.py) must not be falsely credited to the LSP provider.
    consumer2_rows = [
        current
        for current in payload["callers"]
        if current["file"] == str(consumer2_path.resolve())
    ]
    assert consumer2_rows and all(row.get("lsp_proof") is not True for row in consumer2_rows)


def test_repo_map_callers_lsp_partial_index_unions_native_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standalone H1 acceptance test, phrased exactly to the reported scenario: native finds
    2 callers, a partial LSP index only proves 1 -> the merged result must contain BOTH (a
    union), never silently drop the native-only call site nor stamp the incomplete result
    authoritative ("lsp-only" / blanket lsp_proof).
    """
    a_path = tmp_path / "a.py"
    b_path = tmp_path / "b.py"
    service_path = tmp_path / "invoicing.py"
    service_path.write_text(
        "def create_invoice(total: int) -> int:\n    return total + 1\n", encoding="utf-8"
    )
    a_path.write_text(
        "from invoicing import create_invoice\n\ndef handle_a():\n    return create_invoice(1)\n",
        encoding="utf-8",
    )
    b_path.write_text(
        "from invoicing import create_invoice\n\ndef handle_b():\n    return create_invoice(2)\n",
        encoding="utf-8",
    )

    _disable_external_definition_proof(monkeypatch)
    monkeypatch.setattr(
        repo_map,
        "_external_references",
        # The partial LSP index proves only a.py, exactly like the H1 dogfood report.
        lambda root, symbol, definitions: [
            {
                "name": symbol,
                "kind": "reference",
                "file": str(a_path.resolve()),
                "line": 4,
                "end_line": 4,
                "text": "return create_invoice(1)",
                "provenance": "lsp-python",
                "lsp_provider_response": True,
                "lsp_operation": "textDocument/references",
            }
        ],
    )

    payload = repo_map.build_symbol_callers("create_invoice", tmp_path, semantic_provider="lsp")

    caller_files = {str(current["file"]) for current in payload["callers"]}
    assert caller_files == {str(a_path.resolve()), str(b_path.resolve())}
    assert payload["provider_agreement"]["agreement_status"] != "lsp-only"
    assert payload["provider_agreement"]["agreement_status"] == "diverged"
    assert payload["provider_agreement"]["fallback_used"] is True


def test_repo_map_callers_lsp_agrees_with_native_reports_clean_lsp_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full-agreement regression guard: when native and the LSP index see the exact same
    single call site, the H1 union fix must NOT turn honest agreement into a false
    "diverged" signal -- it should still report a clean lsp-only proof.
    """
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
    assert payload["provider_agreement"]["native_count"] == 1
    assert payload["provider_agreement"]["lsp_count"] == 1
    assert payload["provider_agreement"]["fallback_used"] is False
