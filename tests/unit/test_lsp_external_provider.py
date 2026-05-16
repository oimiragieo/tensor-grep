from __future__ import annotations

import os
import queue
import time
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
    assert status["last_error"]


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

    def _fake_start(self: ExternalLSPClient) -> None:
        seen_timeouts.append((self.request_timeout_seconds, self.initialize_timeout_seconds))
        self.capabilities = {"definitionProvider": True}

    monkeypatch.setattr(ExternalLSPClient, "start", _fake_start)
    monkeypatch.setattr(ExternalLSPClient, "stop", lambda self: None)

    status = ExternalLSPProviderManager().provider_status(
        language="python",
        workspace_root=tmp_path,
        verify_health=True,
        probe_timeout_seconds=0.25,
    )

    assert seen_timeouts == [(0.25, 0.25)]
    assert status["available"] is True
    assert status["health_status"] == "ready"
    assert status["health_check"] == "probe"
    assert status["lsp_proof"] is True
    assert "not_lsp_proof_reason" not in status


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
    assert status["health_check"] == "probe"
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
    settings = written_messages[2]["params"]["settings"]
    analysis_settings = settings["python"]["analysis"]
    assert analysis_settings["diagnosticMode"] == "openFilesOnly"
    assert "node_modules" in analysis_settings["exclude"]
    assert "__pycache__" in analysis_settings["exclude"]
    assert ".venv" in analysis_settings["exclude"]


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
