"""GPU-P0-1 (#171): the agent capsule's GPU evidence probe is the twin of the doctor's WSL
path-domain bridging bug -- `_agent_gpu_tg_command()` can resolve to a Windows-target binary that
cannot open a Linux TemporaryDirectory path. These tests exercise the AGENT-SIDE wiring by
monkeypatching the shared runtime_paths collaborators directly; the collaborators' own detection/
translation/timeout logic is unit-tested exhaustively in tests/unit/test_runtime_paths.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule
from tensor_grep.cli.runtime_paths import CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S


def _fake_native_gpu_payload() -> dict[str, Any]:
    return {
        "routing_backend": "NativeGpuBackend",
        "routing_reason": "gpu-device-ids-explicit",
        "sidecar_used": False,
        "routing_gpu_device_ids": [0],
        "matches": [],
        "total_matches": 0,
    }


def test_agent_gpu_evidence_cross_domain_translates_probe_path(monkeypatch, tmp_path):
    translated_path = "C:\\Users\\x\\AppData\\Local\\Temp\\tg-agent-gpu-probe-abc"

    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        agent_capsule, "translate_path_for_windows_binary", lambda _path: translated_path
    )

    captured_calls: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        captured_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    result = agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert captured_calls, "expected the probe to shell out at least once"
    # The LAST argv element of the probe call is the sentinel path -- it must be the TRANSLATED
    # Windows path, not the raw Linux TemporaryDirectory path the sentinel was written under.
    assert captured_calls[0][-1] == translated_path
    assert result["status"] != "path_domain_mismatch"
    assert result["status"] != "failed"


def test_agent_gpu_evidence_cross_domain_translates_evidence_path(monkeypatch, tmp_path):
    """NIT-B (#172): the FIRST (probe) command translates `path` when cross_domain, but the
    SECOND (evidence) command used to append the RAW user `path` -- a Windows-target binary
    cannot resolve a raw WSL/Linux path any more for the evidence scan than it can for the
    probe scan. Both commands must translate the same way."""
    translated_probe_path = "C:\\Users\\x\\AppData\\Local\\Temp\\tg-agent-gpu-probe-abc"
    translated_evidence_path = "C:\\Users\\x\\repo"

    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)

    def _fake_translate(path):
        # The probe path is a fresh TemporaryDirectory under the "tg-agent-gpu-probe-" prefix;
        # the evidence path is the caller's own `path` argument -- assert each is translated
        # independently rather than the probe's cached translation leaking into the evidence
        # command's argv.
        return (
            translated_probe_path
            if "tg-agent-gpu-probe-" in str(path)
            else translated_evidence_path
        )

    monkeypatch.setattr(agent_capsule, "translate_path_for_windows_binary", _fake_translate)

    captured_calls: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        captured_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    result = agent_capsule._agent_gpu_evidence(
        query="needle_query", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert len(captured_calls) == 2, "expected a probe call and an evidence call"
    # The evidence call's LAST argv element is the search path -- it must be the TRANSLATED
    # Windows path, not the raw Linux/WSL `path` argument the capsule was invoked with.
    assert captured_calls[1][-1] == translated_evidence_path
    assert str(tmp_path) not in captured_calls[1]
    assert result["status"] != "path_domain_mismatch"
    assert result["status"] != "failed"


def test_agent_gpu_evidence_path_domain_mismatch_when_evidence_translation_unavailable(
    monkeypatch, tmp_path
):
    """NIT-B (#172): when the probe path translates fine but the EVIDENCE path's translation
    fails, the function must fail closed with the same honest path_domain_mismatch status
    instead of shelling out with an unresolvable raw path."""
    translated_probe_path = "C:\\Users\\x\\AppData\\Local\\Temp\\tg-agent-gpu-probe-abc"

    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)

    def _fake_translate(path):
        return translated_probe_path if "tg-agent-gpu-probe-" in str(path) else None

    monkeypatch.setattr(agent_capsule, "translate_path_for_windows_binary", _fake_translate)

    captured_calls: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        captured_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    result = agent_capsule._agent_gpu_evidence(
        query="needle_query", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert len(captured_calls) == 1, "must not shell out for evidence once translation fails"
    assert result["status"] == "path_domain_mismatch"
    assert result["used_for_evidence"] is False
    assert result["promotion_claim"] is False
    assert "wslpath" in result["reason"]


def test_agent_gpu_evidence_path_domain_mismatch_when_translation_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(agent_capsule, "translate_path_for_windows_binary", lambda _path: None)

    def _fake_run(command, **_kwargs):
        raise AssertionError(
            "must not shell out to the native binary when wslpath translation is unavailable -- "
            "the path would be unresolvable and misreport as a generic GPU failure"
        )

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    result = agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert result["status"] == "path_domain_mismatch"
    assert result["used_for_evidence"] is False
    assert result["promotion_claim"] is False
    assert "wslpath" in result["reason"]


def test_agent_gpu_evidence_same_domain_is_unaffected(monkeypatch, tmp_path):
    """Cross-domain detection false (the common case) leaves argv/behavior exactly as before."""
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/usr/local/bin/tg")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: False)

    def _fail_translate(_path):
        raise AssertionError("must not be called when cross_domain is False")

    monkeypatch.setattr(agent_capsule, "translate_path_for_windows_binary", _fail_translate)

    captured_calls: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        captured_calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    result = agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert result["status"] != "path_domain_mismatch"
    assert "tg-agent-gpu-probe-" in captured_calls[0][-1]


def test_agent_gpu_evidence_cross_domain_raises_timeout_floor(monkeypatch, tmp_path):
    monkeypatch.delenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        agent_capsule, "translate_path_for_windows_binary", lambda _path: "C:\\translated"
    )

    captured_kwargs: list[dict[str, Any]] = []

    def _fake_run(command, **kwargs):
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    # The CLI's own --gpu-timeout-s default (5.0) is smaller than the cross-domain floor -- the
    # shared helper must raise it, not silently time out on a WSL -> Windows interop exec.
    agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert captured_kwargs[0]["timeout"] == pytest.approx(CROSS_DOMAIN_GPU_PROBE_TIMEOUT_S)


def test_agent_gpu_evidence_cross_domain_keeps_larger_explicit_timeout(monkeypatch, tmp_path):
    monkeypatch.delenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: True)
    monkeypatch.setattr(
        agent_capsule, "translate_path_for_windows_binary", lambda _path: "C:\\translated"
    )

    captured_kwargs: list[dict[str, Any]] = []

    def _fake_run(command, **kwargs):
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    # An explicit --gpu-timeout-s already above the cross-domain floor must never be LOWERED.
    agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=30.0
    )

    assert captured_kwargs[0]["timeout"] == pytest.approx(30.0)


def test_agent_gpu_evidence_timeout_env_override_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("TENSOR_GREP_GPU_PROBE_TIMEOUT_S", "12.5")
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/usr/local/bin/tg")
    )
    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", lambda _binary: False)

    captured_kwargs: list[dict[str, Any]] = []

    def _fake_run(command, **kwargs):
        captured_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0, json.dumps(_fake_native_gpu_payload()), "")

    monkeypatch.setattr(agent_capsule.subprocess, "run", _fake_run)

    agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert captured_kwargs[0]["timeout"] == pytest.approx(12.5)


# #704 gate CRUX-4: `_agent_gpu_tg_command()`'s bare-"tg" fallback (when `resolve_native_tg_binary()`
# finds nothing) used to hand `subprocess.run` an un-checked PATH lookup, invisible to
# `is_cross_domain_native_binary()`'s sibling-`.exe`/metadata classification (a relative name has no
# directory component for those checks to inspect). These tests exercise `_agent_gpu_tg_command()`
# directly rather than the full `_agent_gpu_evidence()` flow, mirroring the direct-unit-test style
# already used for the collaborator helpers in tests/unit/test_runtime_paths.py.


def test_agent_gpu_tg_command_uses_resolved_binary_when_available(monkeypatch):
    """Baseline (unaffected by this fix): a resolved native binary is returned as-is."""
    monkeypatch.setattr(
        agent_capsule, "resolve_native_tg_binary", lambda: Path("/mnt/c/fake/tg.exe")
    )
    assert agent_capsule._agent_gpu_tg_command() == str(Path("/mnt/c/fake/tg.exe"))


def test_agent_gpu_tg_command_resolves_via_shutil_which_when_unresolved(monkeypatch):
    """When `resolve_native_tg_binary()` finds nothing but a `tg` IS on PATH, the fallback must
    return the ABSOLUTE resolved path (so the existing `is_cross_domain_native_binary()` gate at
    the call site can classify the real location), not the bare, un-checked string "tg"."""
    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.shutil, "which", lambda name: "/usr/local/bin/tg")

    assert agent_capsule._agent_gpu_tg_command() == "/usr/local/bin/tg"


def test_agent_gpu_tg_command_degrades_honestly_when_nothing_resolves(monkeypatch):
    """When neither `resolve_native_tg_binary()` nor `shutil.which("tg")` find anything, the
    function must still return a plain string rather than raise, so the probe falls through to
    its existing honest failure path: `_run_agent_gpu_json_command`'s `OSError` handler turns the
    resulting spawn failure into a `status: "failed"` result instead of crashing the capsule."""
    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.shutil, "which", lambda name: None)

    assert agent_capsule._agent_gpu_tg_command() == "tg"


def test_agent_gpu_evidence_classifies_shutil_which_result_cross_domain(monkeypatch, tmp_path):
    """End-to-end (through `_agent_gpu_evidence`, not just the helper): a `shutil.which`-resolved
    fallback binary must reach the SAME `is_cross_domain_native_binary()` gate a resolved native
    binary would, proving the call site needs no separate handling for this path."""
    monkeypatch.setattr(agent_capsule, "resolve_native_tg_binary", lambda: None)
    monkeypatch.setattr(agent_capsule.shutil, "which", lambda name: "/mnt/c/fake/tg")

    seen_binaries: list[str] = []

    def _fake_is_cross_domain(binary):
        seen_binaries.append(str(binary))
        return True

    monkeypatch.setattr(agent_capsule, "is_cross_domain_native_binary", _fake_is_cross_domain)
    monkeypatch.setattr(agent_capsule, "translate_path_for_windows_binary", lambda _path: None)

    result = agent_capsule._agent_gpu_evidence(
        query="", path=str(tmp_path), gpu_device_ids=[0], max_files=5, timeout_s=5.0
    )

    assert seen_binaries == ["/mnt/c/fake/tg"]
    assert result["status"] == "path_domain_mismatch"
