from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule
from tensor_grep.cli import main as cli_main

_MISSING = object()
_UNPROVEN_SIDECAR_VALUES = (_MISSING, None, 0, "", "yes", [], True)


def _native_payload(sidecar_value: object = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"routing_backend": "NativeGpuBackend"}
    if sidecar_value is not _MISSING:
        payload["sidecar_used"] = sidecar_value
    return payload


@pytest.mark.parametrize("sidecar_value", _UNPROVEN_SIDECAR_VALUES)
def test_doctor_requires_raw_false_for_native_gpu_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sidecar_value: object,
) -> None:
    binary = tmp_path / "tg.exe"
    binary.write_text("native", encoding="utf-8")
    monkeypatch.setattr(cli_main, "is_cross_domain_native_binary", lambda _binary: False)
    payload = _native_payload(sidecar_value)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(cli_main.subprocess, "run", run)

    result = cli_main._doctor_gpu_search_runtime_probe(binary)

    assert result["status"] == "unsupported"
    assert result["sidecar_used"] is (True if sidecar_value is True else None)


def test_doctor_accepts_explicit_native_false_and_keeps_sidecar_true_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binary = tmp_path / "tg.exe"
    binary.write_text("native", encoding="utf-8")
    monkeypatch.setattr(cli_main, "is_cross_domain_native_binary", lambda _binary: False)
    payloads = [
        {"routing_backend": "NativeGpuBackend", "sidecar_used": False},
        {"routing_backend": "GpuSidecar", "sidecar_used": True},
    ]

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps(payloads.pop(0)), "")

    monkeypatch.setattr(cli_main.subprocess, "run", run)

    native = cli_main._doctor_gpu_search_runtime_probe(binary)
    sidecar = cli_main._doctor_gpu_search_runtime_probe(binary)

    assert native["status"] == "supported"
    assert native["sidecar_used"] is False
    assert sidecar["status"] == "unsupported"
    assert sidecar["sidecar_used"] is True


@pytest.mark.parametrize("sidecar_value", _UNPROVEN_SIDECAR_VALUES)
def test_agent_gpu_route_helpers_require_raw_false_and_preserve_unknown(
    sidecar_value: object,
) -> None:
    payload = _native_payload(sidecar_value)

    rejection = agent_capsule._native_gpu_route_rejection(payload)
    fields = agent_capsule._gpu_route_fields(payload)

    assert rejection is not None
    assert fields["sidecar_used"] is (True if sidecar_value is True else None)


def test_agent_gpu_route_helpers_keep_explicit_false_and_sidecar_true_control() -> None:
    native = {"routing_backend": "NativeGpuBackend", "sidecar_used": False}
    sidecar = {"routing_backend": "GpuSidecar", "sidecar_used": True}

    assert agent_capsule._native_gpu_route_rejection(native) is None
    assert agent_capsule._gpu_route_fields(native)["sidecar_used"] is False
    assert "sidecar-routed" in agent_capsule._native_gpu_route_rejection(sidecar)
    assert agent_capsule._gpu_route_fields(sidecar)["sidecar_used"] is True


@pytest.mark.parametrize("stage", ["probe", "evidence"])
@pytest.mark.parametrize("sidecar_value", _UNPROVEN_SIDECAR_VALUES)
def test_agent_gpu_evidence_rejects_unproven_native_route_at_each_door(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: str,
    sidecar_value: object,
) -> None:
    monkeypatch.setattr(agent_capsule, "_agent_gpu_tg_command", lambda: "tg-native")
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: False)
    bad_payload = _native_payload(sidecar_value)
    valid_payload = {"routing_backend": "NativeGpuBackend", "sidecar_used": False}
    replies = [
        bad_payload if stage == "probe" else valid_payload,
        bad_payload if stage == "evidence" else valid_payload,
    ]
    commands: list[list[object]] = []

    def run_json(argv: list[object], **_kwargs: object) -> dict[str, Any]:
        commands.append(argv)
        return {"status": "ok", "payload": replies.pop(0)}

    monkeypatch.setattr(agent_capsule, "_run_agent_gpu_json_command", run_json)

    result = agent_capsule._agent_gpu_evidence(
        query="needle" if stage == "evidence" else "",
        path=str(tmp_path),
        gpu_device_ids=[0],
        max_files=5,
        timeout_s=5.0,
    )

    assert result["status"] == "unsupported"
    assert result["used_for_evidence"] is False
    assert result["promotion_claim"] is False
    assert result["sidecar_used"] is (True if sidecar_value is True else None)
    assert len(commands) == (2 if stage == "evidence" else 1)
