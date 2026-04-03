from __future__ import annotations

import sys
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
    assert status["managed_provider_root"] == str(provider_module._managed_provider_root())


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
    assert status["language"] == "python"
    assert status["capabilities"]["definitionProvider"] is True
    assert status["last_error"] == "timeout waiting for LSP response: textDocument/definition"
    assert status["request_timeout_seconds"] == provider_module._DEFAULT_LSP_REQUEST_TIMEOUT_SECONDS
    assert (
        status["initialize_timeout_seconds"]
        == provider_module._DEFAULT_LSP_INITIALIZE_TIMEOUT_SECONDS
    )


def test_provider_command_prefers_managed_binary_over_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "providers"
    suffix = ".cmd" if sys.platform.startswith("win") else ""
    managed_binary = (
        managed_root / "node-packages" / "node_modules" / ".bin" / f"pyright-langserver{suffix}"
    )
    managed_binary.parent.mkdir(parents=True)
    managed_binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider_module, "_managed_provider_root", lambda: managed_root)
    monkeypatch.setattr(
        "tensor_grep.cli.lsp_external_provider.resolved_provider_command",
        lambda language, managed_root=None: (
            [str(managed_binary), "--stdio"] if language == "python" else None
        ),
    )

    command = provider_module._provider_command("python")

    assert command == [str(managed_binary), "--stdio"]


def test_provider_status_reports_managed_command_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "providers"
    suffix = ".cmd" if sys.platform.startswith("win") else ""
    managed_binary = (
        managed_root
        / "node-packages"
        / "node_modules"
        / ".bin"
        / f"typescript-language-server{suffix}"
    )
    managed_binary.parent.mkdir(parents=True)
    managed_binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(provider_module, "_managed_provider_root", lambda: managed_root)
    monkeypatch.setattr(
        "tensor_grep.cli.lsp_external_provider.resolved_provider_command",
        lambda language, managed_root=None: (
            [str(managed_binary), "--stdio"] if language == "typescript" else None
        ),
    )

    manager = ExternalLSPProviderManager()
    status = manager.provider_status(language="typescript", workspace_root=tmp_path)

    assert status["available"] is True
    assert status["command"] == [str(managed_binary), "--stdio"]
    assert status["command_source"] == "managed"


def test_provider_command_supports_java_from_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_module, "_managed_provider_root", lambda: Path("/tmp/providers"))
    monkeypatch.setattr(
        "tensor_grep.cli.lsp_external_provider.resolved_provider_command",
        lambda language, managed_root=None: ["/usr/bin/jdtls"] if language == "java" else None,
    )

    command = provider_module._provider_command("java")

    assert command == ["/usr/bin/jdtls"]


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
    assert status["request_timeout_seconds"] == 6.0
    assert status["initialize_timeout_seconds"] == 21.0


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
