from __future__ import annotations

import time
from pathlib import Path

import pytest

import tensor_grep.cli.lsp_external_provider as provider_module
from tensor_grep.cli.lsp_external_provider import ExternalLSPClient, ExternalLSPProviderManager


def test_provider_status_reports_missing_binary(tmp_path: Path) -> None:
    manager = ExternalLSPProviderManager()

    status = manager.provider_status(language="definitely-not-a-language", workspace_root=tmp_path)

    assert status["available"] is False
    assert status["running"] is False
    assert status["capabilities"] == {}
    assert status["last_error"]


def test_provider_status_reports_cached_client_state(tmp_path: Path) -> None:
    manager = ExternalLSPProviderManager()
    client = manager.get_client(language="python", workspace_root=tmp_path)
    client.capabilities = {"definitionProvider": True}
    client.last_error = "timeout waiting for LSP response: textDocument/definition"

    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["available"] is True
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

    manager = ExternalLSPProviderManager()
    status = manager.provider_status(language="python", workspace_root=tmp_path)

    assert status["available"] is True
    assert status["request_timeout_seconds"] == 6.0
    assert status["initialize_timeout_seconds"] == 21.0


def test_external_provider_client_respects_retry_cooldown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.last_error = "timeout waiting for LSP response: initialize"
    client.disabled_until_monotonic = time.monotonic() + 30.0
    monkeypatch.setattr(
        "tensor_grep.cli.lsp_external_provider.subprocess.Popen", lambda *args, **kwargs: None
    )

    with pytest.raises(Exception, match="timeout waiting for LSP response: initialize"):
        client.start()
