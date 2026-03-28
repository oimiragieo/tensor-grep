from __future__ import annotations

import time
from pathlib import Path

import pytest

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


def test_external_provider_client_respects_retry_cooldown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    client.last_error = "timeout waiting for LSP response: initialize"
    client.disabled_until_monotonic = time.monotonic() + 30.0
    monkeypatch.setattr("tensor_grep.cli.lsp_external_provider.subprocess.Popen", lambda *args, **kwargs: None)

    with pytest.raises(Exception, match="timeout waiting for LSP response: initialize"):
        client.start()
