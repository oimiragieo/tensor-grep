"""Tests for DF2-07: MCP tg_explore(action='doctor') fast=True default and LSP deduplication."""

from __future__ import annotations

import json
import time
from pathlib import Path

from tensor_grep.cli import mcp_server


def _patch_fast_doctor_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_MCP_ROOT", str(tmp_path))
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.142.0")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr("tensor_grep.cli.main._doctor_rust_binary_version", lambda _b: None)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_core_extension_available", lambda: True
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda: None
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )


def test_tg_explore_doctor_defaults_to_fast_and_skips_slow_probes(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_fast_doctor_env(monkeypatch, tmp_path)

    def _fail_lsp(_root: str):
        raise AssertionError("LSP probe must be skipped when fast=True (default)")

    def _fail_pypi():
        raise AssertionError("PyPI probe must be skipped when fast=True (default)")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", _fail_lsp)
    monkeypatch.setattr("tensor_grep.cli.main._latest_pypi_tensor_grep_version", _fail_pypi)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda _b: {"status": "missing_native_binary"},
    )

    t0 = time.perf_counter()
    raw = mcp_server.tg_explore(action="doctor", path=str(tmp_path), compact=True)
    elapsed = time.perf_counter() - t0

    assert len(raw.encode("utf-8")) <= 4096
    assert elapsed < 1.0
    payload = json.loads(raw)
    assert payload["lsp"]["enabled"] is False
    assert "lsp_providers" not in payload
    assert "lsp_provider_items" not in payload


def test_tg_explore_doctor_deduplicates_lsp_fields_even_when_fast_false(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_fast_doctor_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_lsp_provider_statuses",
        lambda _root: [
            {
                "language": "python",
                "command": "pyright-langserver",
                "available": False,
                "health_status": "missing",
            }
        ],
    )
    monkeypatch.setattr("tensor_grep.cli.main._latest_pypi_tensor_grep_version", lambda: "1.142.0")
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_gpu_search_runtime_probe",
        lambda _b: {"status": "missing_native_binary"},
    )

    raw = mcp_server.tg_explore(
        action="doctor", path=str(tmp_path), with_lsp=True, fast=False, compact=False
    )
    payload = json.loads(raw)
    assert payload["lsp"]["enabled"] is True
    assert len(payload["lsp"]["providers"]) == 1
    assert "python" in payload["lsp"]["providers_by_language"]
    assert "lsp_providers" not in payload
    assert "lsp_provider_items" not in payload
