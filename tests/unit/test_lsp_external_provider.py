from __future__ import annotations

import os
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
