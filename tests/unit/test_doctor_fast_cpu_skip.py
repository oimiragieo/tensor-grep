from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tensor_grep.cli import doctor_payload
from tensor_grep.cli.main import app


def _patch_doctor_baseline(
    monkeypatch,
    *,
    native_bin: Path | None,
    flavor: str | None,
) -> None:
    monkeypatch.setattr("tensor_grep.cli.main._doctor_installed_version", lambda: "1.141.0")
    monkeypatch.setattr("tensor_grep.cli.main.resolve_native_tg_binary", lambda: native_bin)
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_rust_binary_version",
        lambda _b: "tg 1.141.0" if native_bin is not None else None,
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main.inspect_native_tg_binary",
        lambda _b, **_kw: {"native_frontdoor_flavor": flavor} if flavor is not None else {},
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_rust_core_extension_available", lambda: True)
    monkeypatch.setattr("tensor_grep.cli.main._doctor_path_tg_candidates", lambda: [])
    monkeypatch.setattr("tensor_grep.cli.main._doctor_fresh_shell_path_tg_candidates", lambda: [])
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_python_subprocess_path_tg_candidate", lambda: None
    )
    monkeypatch.setattr(
        "tensor_grep.cli.main._doctor_session_daemon_status", lambda _p: {"running": False}
    )
    monkeypatch.setattr("tensor_grep.cli.main._doctor_lsp_provider_statuses", lambda _p: [])
    monkeypatch.setattr("tensor_grep.cli.main._latest_pypi_tensor_grep_version", lambda: None)


def test_doctor_fast_skips_gpu_search_runtime_probe_on_cpu_flavor(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "tg.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    _patch_doctor_baseline(monkeypatch, native_bin=fake_bin, flavor="cpu")

    def _fail_probe(_binary: Path | None):
        raise AssertionError("GPU search runtime probe must be skipped under --fast on cpu flavor")

    monkeypatch.setattr("tensor_grep.cli.main._doctor_gpu_search_runtime_probe", _fail_probe)

    result = CliRunner().invoke(app, ["doctor", str(tmp_path), "--fast", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    probe = payload["gpu"]["search_runtime_probe"]
    assert probe["status"] == "skipped_fast_cpu_flavor"
    assert payload["gpu"]["search_ready"] is False
    assert payload["gpu"]["tier"]["promotion_proof"] is False


def test_doctor_fast_runs_gpu_search_runtime_probe_when_flavor_unknown(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "tg.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    _patch_doctor_baseline(monkeypatch, native_bin=fake_bin, flavor=None)

    called = {"count": 0}

    def _fake_probe(_binary: Path | None):
        called["count"] += 1
        return {"status": "failed_gpu_unavailable"}

    monkeypatch.setattr("tensor_grep.cli.main._doctor_gpu_search_runtime_probe", _fake_probe)

    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    assert called["count"] == 1
    assert payload["gpu"]["search_runtime_probe"]["status"] == "failed_gpu_unavailable"


def test_doctor_fast_runs_gpu_search_runtime_probe_when_no_binary(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_doctor_baseline(monkeypatch, native_bin=None, flavor=None)

    called = {"count": 0}

    def _fake_probe(_binary: Path | None):
        called["count"] += 1
        return {"status": "missing_native_binary"}

    monkeypatch.setattr("tensor_grep.cli.main._doctor_gpu_search_runtime_probe", _fake_probe)

    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    assert called["count"] == 1
    assert payload["gpu"]["search_runtime_probe"]["status"] == "missing_native_binary"


def test_doctor_fast_runs_gpu_search_runtime_probe_on_nvidia_flavor(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "tg.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    _patch_doctor_baseline(monkeypatch, native_bin=fake_bin, flavor="nvidia")

    called = {"count": 0}

    def _fake_probe(_binary: Path | None):
        called["count"] += 1
        return {
            "status": "supported",
            "requested_gpu_device_ids": [0],
            "command": "tg search",
            "exit_code": 0,
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "native_gpu",
            "sidecar_used": False,
            "routing_gpu_device_ids": [0],
            "native_error_kind": None,
            "error": None,
        }

    monkeypatch.setattr("tensor_grep.cli.main._doctor_gpu_search_runtime_probe", _fake_probe)

    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False, fast=True)
    assert called["count"] == 1
    assert payload["gpu"]["search_runtime_probe"]["status"] == "supported"
    assert payload["gpu"]["search_ready"] is True


def test_doctor_non_fast_still_runs_gpu_search_runtime_probe_on_cpu_flavor(
    monkeypatch, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "tg.exe"
    fake_bin.write_text("exe", encoding="utf-8")
    _patch_doctor_baseline(monkeypatch, native_bin=fake_bin, flavor="cpu")

    called = {"count": 0}

    def _fake_probe(_binary: Path | None):
        called["count"] += 1
        return {"status": "failed_gpu_unavailable"}

    monkeypatch.setattr("tensor_grep.cli.main._doctor_gpu_search_runtime_probe", _fake_probe)

    payload = doctor_payload._build_doctor_payload(str(tmp_path), with_lsp=False, fast=False)
    assert called["count"] == 1
    assert payload["gpu"]["search_runtime_probe"]["status"] == "failed_gpu_unavailable"
