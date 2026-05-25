from __future__ import annotations

import os
import queue
import time
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pytest

import tensor_grep.cli.lsp_external_provider as provider_module
from tensor_grep.cli.lsp_external_provider import (
    ExternalLSPClient,
    ExternalLSPProviderManager,
    LSPTransportError,
)


def test_provider_status_reports_missing_binary(tmp_path: Path) -> None:
    manager = ExternalLSPProviderManager()

    status = manager.provider_status(language="definitely-not-a-language", workspace_root=tmp_path)

    assert status["available"] is False
    assert status["health_status"] == "missing"
    assert status["running"] is False
    assert status["capabilities"] == {}
    assert status["lsp_provider_response"] is False
    assert status["last_error"]


def test_lsp_message_framing_uses_binary_content_length() -> None:
    stream = BytesIO()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"probe": "tg_lsp_health_probe_é"},
    }

    provider_module._write_message(stream, payload)

    raw = stream.getvalue()
    assert b"\r\n\r\n" in raw
    assert b"\r\r\n" not in raw

    stream.seek(0)
    assert provider_module._read_message(stream) == payload


def test_provider_status_reports_cached_client_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)
    client.capabilities = {"definitionProvider": True}
    client.last_error = "timeout waiting for LSP response: textDocument/definition"

    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["available"] is True
    assert status["health_status"] == "unhealthy"
    assert status["language"] == "python"
    assert status["capabilities"]["definitionProvider"] is True
    assert status["last_error"] == "timeout waiting for LSP response: textDocument/definition"
    assert status["request_timeout_seconds"] == provider_module._DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS
    assert (
        status["initialize_timeout_seconds"]
        == provider_module._DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS
    )
    assert provider_module._DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS == 15.0


def test_external_lsp_client_drains_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    class _FakeProcess:
        stderr = StringIO("starting provider\nindexed workspace\n")

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.process = _FakeProcess()  # type: ignore[assignment]
    client.enable_debug_trace()

    client._stderr_loop()

    assert client.stderr_tail() == ["starting provider", "indexed workspace"]
    assert [event["event"] for event in client.debug_trace()] == ["stderr", "stderr"]


def test_provider_debug_trace_reports_probe_status_and_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    def _fake_start(self: ExternalLSPClient) -> None:
        self._record_debug_trace(event="process_start", detail={"command": self.command})
        self._record_debug_trace(event="send_request", method="initialize", request_id=1)
        self._record_debug_trace(event="receive_response", method="initialize", request_id=1)
        self.capabilities = {"documentSymbolProvider": True}

    def _fake_ensure_document(self: ExternalLSPClient, **_kwargs: object) -> None:
        self._record_debug_trace(event="send_notification", method="textDocument/didOpen")

    def _fake_request(self: ExternalLSPClient, method: str, _params: dict[str, Any]) -> object:
        self._record_debug_trace(event="send_request", method=method, request_id=2)
        self._record_debug_trace(event="receive_response", method=method, request_id=2)
        return [{"name": "tg_lsp_health_probe", "kind": 12}]

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", _fake_ensure_document)
    monkeypatch.setattr(ExternalLSPClient, "request", _fake_request)
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    payload = ExternalLSPProviderManager().provider_debug_trace(
        language="python",
        workspace_root=tmp_path,
        probe_timeout_seconds=0.5,
    )

    assert payload["schema_version"] == 1
    assert payload["probe_timeout_seconds"] == 0.5
    assert payload["status"]["health_status"] == "ready"
    assert payload["status"]["lsp_proof"] is True
    trace_methods = [event.get("method") for event in payload["trace"]]
    assert "initialize" in trace_methods
    assert "textDocument/documentSymbol" in trace_methods


def test_provider_status_cached_initialized_client_is_not_lsp_proof_without_provider_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    class _FakeProcess:
        def poll(self) -> None:
            return None

    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)
    client.process = _FakeProcess()  # type: ignore[assignment]
    client.initialized = True
    client.capabilities = {"documentSymbolProvider": True}

    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["health_status"] == "ready"
    assert status["health_check"] == "cached-client"
    assert status["lsp_provider_response"] is False
    assert status["lsp_proof"] is False
    assert "not been verified" in status["not_lsp_proof_reason"]


def test_provider_manager_uses_configured_timeouts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS", "18")
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)

    assert client.request_timeout_seconds == 7.5
    assert client.initialize_timeout_seconds == 18.0


def test_provider_manager_evicts_lru_clients_and_stops_evicted_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    stopped_roots: list[Path] = []

    def _fake_stop(self: ExternalLSPClient) -> None:
        stopped_roots.append(self.workspace_root)

    monkeypatch.setattr(ExternalLSPClient, "stop", _fake_stop)
    roots = []
    for index in range(3):
        root = tmp_path / f"repo_{index}"
        root.mkdir()
        roots.append(root.resolve())

    manager = ExternalLSPProviderManager(max_clients=2)
    for root in roots:
        manager.get_client(language="python", workspace_root=root)

    assert len(manager._clients) == 2
    assert stopped_roots == [roots[0]]
    assert ("python", str(roots[0])) not in manager._clients
    assert ("python", str(roots[1])) in manager._clients
    assert ("python", str(roots[2])) in manager._clients


def test_lsp_cache_cap_invalid_env_values_use_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    monkeypatch.setenv("TENSOR_GREP_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES", "invalid")
    monkeypatch.setenv("TENSOR_GREP_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES", "invalid")

    manager = ExternalLSPProviderManager()
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)

    assert manager._max_clients == provider_module._DEFAULT_LSP_PROVIDER_CLIENT_CACHE_MAX_ENTRIES
    assert (
        client.status()["max_open_documents"]
        == provider_module._DEFAULT_LSP_PROVIDER_OPEN_DOCUMENT_MAX_ENTRIES
    )


def test_lsp_client_closes_oldest_document_when_open_document_cap_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    client = ExternalLSPClient(language="python", workspace_root=tmp_path, max_open_documents=2)
    notifications: list[tuple[str, dict[str, Any]]] = []

    def _fake_notify(method: str, params: dict[str, Any]) -> None:
        notifications.append((method, params))

    monkeypatch.setattr(client, "notify", _fake_notify)
    uris = [f"file:///{index}.py" for index in range(3)]
    for uri in uris:
        client.ensure_document(uri=uri, text="def f():\n    pass\n", language_id="python")

    did_close = [
        params["textDocument"]["uri"]
        for method, params in notifications
        if method == "textDocument/didClose"
    ]
    assert did_close == [uris[0]]
    assert len(client._opened_documents) == 2
    assert uris[0] not in client._opened_documents
    assert uris[1] in client._opened_documents
    assert uris[2] in client._opened_documents


def test_lsp_client_preserves_old_document_when_new_open_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    client = ExternalLSPClient(language="python", workspace_root=tmp_path, max_open_documents=1)
    old_uri = "file:///old.py"
    new_uri = "file:///new.py"
    client._opened_documents[old_uri] = None
    notifications: list[str] = []

    def _fake_notify(method: str, _params: dict[str, Any]) -> None:
        notifications.append(method)
        if method == "textDocument/didOpen":
            raise LSPTransportError("open failed")

    monkeypatch.setattr(client, "notify", _fake_notify)

    with pytest.raises(LSPTransportError):
        client.ensure_document(uri=new_uri, text="def f():\n    pass\n", language_id="python")

    assert old_uri in client._opened_documents
    assert new_uri not in client._opened_documents
    assert "textDocument/didClose" not in notifications


def test_verified_provider_status_stops_probe_client_on_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    manager = ExternalLSPProviderManager()
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    stopped = False

    def _raise_runtime_error() -> None:
        raise RuntimeError("unexpected probe failure")

    def _fake_stop() -> None:
        nonlocal stopped
        stopped = True

    monkeypatch.setattr(client, "start", _raise_runtime_error)
    monkeypatch.setattr(client, "stop", _fake_stop)

    with pytest.raises(RuntimeError, match="unexpected probe failure"):
        manager._verified_provider_status(
            client=client,
            language="python",
            workspace_root=tmp_path,
            probe_timeout_seconds=0.1,
            stop_after_probe=True,
        )

    assert stopped is True


def test_provider_status_reports_configured_timeouts_without_cached_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TENSOR_GREP_LSP_REQUEST_TIMEOUT_SECONDS", "6")
    monkeypatch.setenv("TENSOR_GREP_LSP_INITIALIZE_TIMEOUT_SECONDS", "21")
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    manager = ExternalLSPProviderManager()
    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["available"] is True
    assert status["health_status"] == "available_unverified"
    assert status["health_check"] == "not_run"
    assert status["lsp_provider_response"] is False
    assert status["lsp_proof"] is False
    assert "not verified" in status["not_lsp_proof_reason"]
    assert status["request_timeout_seconds"] == 6.0
    assert status["initialize_timeout_seconds"] == 21.0


def test_provider_status_available_binary_is_unverified_without_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
    )

    assert status["available"] is True
    assert status["health_status"] == "available_unverified"
    assert status["health_check"] == "not_run"
    assert status["lsp_provider_response"] is False
    assert status["lsp_proof"] is False
    assert status["not_lsp_proof_reason"]


def test_provider_status_verify_health_success_reports_lsp_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    seen_timeouts: list[tuple[float, float]] = []
    opened_documents: list[dict[str, object]] = []
    requests: list[str] = []

    def _fake_start(self: ExternalLSPClient) -> None:
        seen_timeouts.append((self.request_timeout_seconds, self.initialize_timeout_seconds))
        self.capabilities = {"definitionProvider": True, "documentSymbolProvider": True}

    def _fake_ensure_document(self: ExternalLSPClient, **kwargs: object) -> None:
        opened_documents.append(dict(kwargs))

    def _fake_request(self: ExternalLSPClient, method: str, params: dict[str, Any]) -> object:
        requests.append(method)
        return [{"name": "tg_lsp_health_probe", "kind": 12}]

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", _fake_ensure_document)
    monkeypatch.setattr(ExternalLSPClient, "request", _fake_request)
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.25,
    )

    assert seen_timeouts == [(0.25, 0.25)]
    assert opened_documents
    assert requests == ["textDocument/documentSymbol"]
    assert status["available"] is True
    assert status["health_status"] == "ready"
    assert status["health_check"] == "semantic-document-symbol"
    assert status["health_phase"] == "document_symbol"
    assert status["lsp_provider_response"] is True
    assert status["lsp_proof"] is True
    assert "not_lsp_proof_reason" not in status


def test_provider_status_verify_health_success_preserves_sre_stderr_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    def _fake_start(self: ExternalLSPClient) -> None:
        self.capabilities = {"documentSymbolProvider": True}
        self._stderr_tail = ["SRE module mismatch traceback"]

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", lambda self, **_kwargs: None)
    monkeypatch.setattr(
        ExternalLSPClient,
        "request",
        lambda self, method, params: [{"name": "tg_lsp_health_probe", "kind": 12}],
    )
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.25,
    )

    assert status["health_status"] == "ready"
    assert status["lsp_proof"] is True
    assert status["last_error"] is None
    assert status["stderr_tail"] == ["SRE module mismatch traceback"]
    assert status["provider_warnings"] == ["SRE module mismatch traceback"]
    assert status["stderr_tail_suppressed"] is False


def test_provider_status_verify_health_applies_probe_budget_to_initialize(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    seen_timeouts: list[tuple[float, float]] = []

    def _fake_start(self: ExternalLSPClient) -> None:
        seen_timeouts.append((self.request_timeout_seconds, self.initialize_timeout_seconds))
        self.capabilities = {"documentSymbolProvider": True}

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", lambda self, **_kwargs: None)
    monkeypatch.setattr(
        ExternalLSPClient,
        "request",
        lambda self, method, params: [{"name": "tg_lsp_health_probe", "kind": 12}],
    )
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.25,
    )

    assert seen_timeouts == [(0.25, 0.25)]
    assert status["probe_timeout_seconds"] == 0.25
    assert (
        status["initialize_timeout_seconds"]
        == provider_module._DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS
    )


def test_provider_status_verify_health_persists_semantic_provider_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    class _FakeProcess:
        def poll(self) -> None:
            return None

    def _fake_start(self: ExternalLSPClient) -> None:
        self.process = _FakeProcess()  # type: ignore[assignment]
        self.initialized = True
        self.capabilities = {"documentSymbolProvider": True}

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", lambda self, **_kwargs: None)
    monkeypatch.setattr(
        ExternalLSPClient,
        "request",
        lambda self, method, params: [{"name": "tg_lsp_health_probe", "kind": 12}],
    )

    manager = ExternalLSPProviderManager()
    manager.get_client(language="python", workspace_root=tmp_path)
    probed = manager.provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.25,
    )
    cached = manager.provider_status(language="python", workspace_root=tmp_path)

    assert probed["lsp_provider_response"] is True
    assert probed["lsp_proof"] is True
    assert cached["health_status"] == "ready"
    assert cached["health_check"] == "cached-client"
    assert cached["lsp_provider_response"] is True
    assert cached["lsp_proof"] is True


def test_provider_status_verify_health_bounds_cached_probe_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    seen_timeouts: list[tuple[float, float]] = []

    class _FakeProcess:
        def poll(self) -> None:
            return None

    def _fake_start(self: ExternalLSPClient) -> None:
        seen_timeouts.append((self.request_timeout_seconds, self.initialize_timeout_seconds))
        self.process = _FakeProcess()  # type: ignore[assignment]
        self.initialized = True
        self.capabilities = {"documentSymbolProvider": True}

    def _fake_request(self: ExternalLSPClient, method: str, params: dict[str, Any]) -> object:
        seen_timeouts.append((self.request_timeout_seconds, self.initialize_timeout_seconds))
        return [{"name": "tg_lsp_health_probe", "kind": 12}]

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", lambda self, **_kwargs: None)
    monkeypatch.setattr(ExternalLSPClient, "request", _fake_request)

    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)
    client.request_timeout_seconds = 3.0
    client.initialize_timeout_seconds = 15.0

    status = manager.provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.2,
    )

    assert seen_timeouts == [(0.2, 0.2), (0.2, 0.2)]
    assert status["lsp_proof"] is True
    assert status["probe_timeout_seconds"] == 0.2
    assert client.request_timeout_seconds == 3.0
    assert client.initialize_timeout_seconds == 15.0


def test_provider_status_verify_health_requires_semantic_provider_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    requests: list[str] = []

    def _fake_start(self: ExternalLSPClient) -> None:
        self.capabilities = {"documentSymbolProvider": True}

    def _fake_request(self: ExternalLSPClient, method: str, params: dict[str, Any]) -> object:
        requests.append(method)
        return []

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "ensure_document", lambda self, **_kwargs: None)
    monkeypatch.setattr(ExternalLSPClient, "request", _fake_request)
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.2,
    )

    assert requests == ["textDocument/documentSymbol"]
    assert status["available"] is True
    assert status["health_status"] == "unhealthy"
    assert status["health_check"] == "semantic-document-symbol"
    assert status["health_phase"] == "document_symbol"
    assert status["lsp_provider_response"] is False
    assert status["lsp_proof"] is False
    assert "semantic" in status["not_lsp_proof_reason"].lower()


def test_provider_status_verify_health_failure_reports_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )

    def _fake_start(self: ExternalLSPClient) -> None:
        self.disabled_until_monotonic = time.monotonic() + 30.0
        raise LSPTransportError("timeout waiting for LSP response: initialize")

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.2,
    )

    assert status["available"] is True
    assert status["health_status"] == "unhealthy"
    assert status["health_check"] == "semantic-document-symbol"
    assert status["health_phase"] == "initialize"
    assert status["lsp_provider_response"] is False
    assert status["lsp_proof"] is False
    assert status["last_error"] == "timeout waiting for LSP response: initialize"
    assert status["probe_timeout_seconds"] == 0.2
    assert status["cooldown_remaining_s"] > 0.0


def test_provider_status_prefers_managed_provider_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_root = tmp_path / "providers"
    suffix = ".cmd" if os.name == "nt" else ""
    binary = (
        provider_root / "node-packages" / "node_modules" / ".bin" / f"pyright-langserver{suffix}"
    )
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("TENSOR_GREP_LSP_PROVIDER_HOME", str(provider_root))

    manager = ExternalLSPProviderManager()
    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["available"] is True
    assert status["command"] == [str(binary.resolve()), "--stdio"]
    assert status["command_source"] == "managed"
    assert status["managed_provider_root"] == str(provider_root.resolve())


def test_external_provider_client_starts_managed_provider_with_managed_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider_root = tmp_path / "providers"
    suffix = ".cmd" if os.name == "nt" else ""
    binary = (
        provider_root / "node-packages" / "node_modules" / ".bin" / f"pyright-langserver{suffix}"
    )
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    monkeypatch.setenv("TENSOR_GREP_LSP_PROVIDER_HOME", str(provider_root))
    captured: dict[str, object] = {}

    class _FakeProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self) -> None:
            return None

    def _fake_popen(*args: object, **kwargs: object) -> _FakeProcess:
        captured["env"] = kwargs["env"]
        return _FakeProcess()

    monkeypatch.setattr(provider_module.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        ExternalLSPClient,
        "request",
        lambda self, method, params: {"capabilities": {}},
    )
    monkeypatch.setattr(ExternalLSPClient, "notify", lambda self, method, params: None)

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.start()

    env = captured["env"]
    assert isinstance(env, dict)
    expected_node_path = (
        provider_root / "node-runtime"
        if os.name == "nt"
        else provider_root / "node-runtime" / "bin"
    )
    assert str(expected_node_path) in str(env["PATH"]).split(os.pathsep)


def test_external_provider_client_start_orders_initialize_initialized_and_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    written_messages: list[dict[str, Any]] = []
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
    responses.put({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"capabilities": {"definitionProvider": True}},
    })
    responses.put(None)

    class _FakeStream:
        def close(self) -> None:
            return None

    class _FakeProcess:
        stdin = _FakeStream()
        stdout = _FakeStream()
        stderr = _FakeStream()

        def poll(self) -> None:
            return None

    monkeypatch.setattr(provider_module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(
        provider_module,
        "_write_message",
        lambda _stream, payload: written_messages.append(dict(payload)),
    )
    monkeypatch.setattr(provider_module, "_read_message", lambda _stream: responses.get())

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.start()

    assert [message["method"] for message in written_messages] == [
        "initialize",
        "initialized",
        "workspace/didChangeConfiguration",
    ]
    initialize_message = written_messages[0]
    assert "id" in initialize_message
    assert initialize_message["params"]["capabilities"]["workspace"] == {
        "configuration": True,
        "workspaceFolders": True,
    }
    assert (
        initialize_message["params"]["initializationOptions"]["python"]["analysis"][
            "diagnosticMode"
        ]
        == "openFilesOnly"
    )
    settings = written_messages[2]["params"]["settings"]
    analysis_settings = settings["python"]["analysis"]
    assert analysis_settings["diagnosticMode"] == "openFilesOnly"
    assert "node_modules" in analysis_settings["exclude"]
    assert "__pycache__" in analysis_settings["exclude"]
    assert ".venv" in analysis_settings["exclude"]


def test_external_provider_client_answers_server_configuration_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    written_messages: list[dict[str, Any]] = []
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
    responses.put({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "workspace/configuration",
        "params": {
            "items": [
                {"section": "python.analysis"},
                {"section": "python"},
                {},
            ]
        },
    })
    responses.put({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"capabilities": {"definitionProvider": True}},
    })
    responses.put(None)

    class _FakeStream:
        def close(self) -> None:
            return None

    class _FakeProcess:
        stdin = _FakeStream()
        stdout = _FakeStream()
        stderr = _FakeStream()

        def poll(self) -> None:
            return None

    monkeypatch.setattr(provider_module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(
        provider_module,
        "_write_message",
        lambda _stream, payload: written_messages.append(dict(payload)),
    )
    monkeypatch.setattr(provider_module, "_read_message", lambda _stream: responses.get())

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.start()

    server_response = next(
        message for message in written_messages if message.get("id") == 1 and "result" in message
    )
    assert "error" not in server_response
    analysis_settings, python_settings, full_settings = server_response["result"]
    assert analysis_settings["diagnosticMode"] == "openFilesOnly"
    assert "artifacts" in analysis_settings["exclude"]
    assert python_settings["analysis"]["diagnosticMode"] == "openFilesOnly"
    assert full_settings["python"]["analysis"]["diagnosticMode"] == "openFilesOnly"
    assert [message["method"] for message in written_messages if "method" in message] == [
        "initialize",
        "initialized",
        "workspace/didChangeConfiguration",
    ]


def test_external_provider_client_answers_workspace_folder_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    written_messages: list[dict[str, Any]] = []
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
    responses.put({
        "jsonrpc": "2.0",
        "id": 42,
        "method": "workspace/workspaceFolders",
    })
    responses.put({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"capabilities": {"definitionProvider": True}},
    })
    responses.put(None)

    class _FakeStream:
        def close(self) -> None:
            return None

    class _FakeProcess:
        stdin = _FakeStream()
        stdout = _FakeStream()
        stderr = _FakeStream()

        def poll(self) -> None:
            return None

    monkeypatch.setattr(provider_module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(
        provider_module,
        "_write_message",
        lambda _stream, payload: written_messages.append(dict(payload)),
    )
    monkeypatch.setattr(provider_module, "_read_message", lambda _stream: responses.get())

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.start()

    server_response = next(message for message in written_messages if message.get("id") == 42)
    assert server_response["result"] == [
        {"uri": tmp_path.resolve().as_uri(), "name": tmp_path.name}
    ]


def test_external_provider_client_rejects_unknown_server_requests_without_poisoning_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    written_messages: list[dict[str, Any]] = []
    responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
    responses.put({
        "jsonrpc": "2.0",
        "id": "server-unknown",
        "method": "experimental/unknown",
    })
    responses.put({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"capabilities": {"definitionProvider": True}},
    })
    responses.put(None)

    class _FakeStream:
        def close(self) -> None:
            return None

    class _FakeProcess:
        stdin = _FakeStream()
        stdout = _FakeStream()
        stderr = _FakeStream()

        def poll(self) -> None:
            return None

    monkeypatch.setattr(provider_module.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess())
    monkeypatch.setattr(
        provider_module,
        "_write_message",
        lambda _stream, payload: written_messages.append(dict(payload)),
    )
    monkeypatch.setattr(provider_module, "_read_message", lambda _stream: responses.get())

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.start()

    server_response = next(
        message for message in written_messages if message.get("id") == "server-unknown"
    )
    assert server_response["error"]["code"] == -32601
    assert "Unsupported LSP server request" in server_response["error"]["message"]
    assert client.capabilities == {"definitionProvider": True}


def test_external_provider_client_stop_sends_shutdown_request_then_exit_notification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    written_messages: list[dict[str, Any]] = []

    class _FakeStream:
        def close(self) -> None:
            return None

    class _FakeProcess:
        stdin = _FakeStream()
        stdout = _FakeStream()
        stderr = _FakeStream()

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

    monkeypatch.setattr(
        provider_module,
        "_write_message",
        lambda _stream, payload: written_messages.append(dict(payload)),
    )

    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.process = _FakeProcess()  # type: ignore[assignment]
    client._message_queue.put({"jsonrpc": "2.0", "id": 1, "result": None})

    client.stop()

    assert [message["method"] for message in written_messages] == ["shutdown", "exit"]
    assert "id" in written_messages[0]
    assert "id" not in written_messages[1]


def test_external_provider_client_respects_retry_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.last_error = "timeout waiting for LSP response: initialize"
    client.disabled_until_monotonic = time.monotonic() + 30.0
    monkeypatch.setattr(
        "tensor_grep.cli.lsp_external_provider.subprocess.Popen", lambda *args, **kwargs: None
    )

    with pytest.raises(Exception, match="timeout waiting for LSP response: initialize"):
        client.start()


def test_provider_manager_stop_all_stops_and_forgets_cached_clients(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        provider_module,
        "_provider_command",
        lambda _language: ["fake-lsp", "--stdio"],
    )
    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)
    stopped: list[bool] = []
    monkeypatch.setattr(client, "stop", lambda: stopped.append(True))

    manager.stop_all()

    assert stopped == [True]
    assert manager.get_client(language="python", workspace_root=tmp_path) is not client
