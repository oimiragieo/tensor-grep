from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import frontdoor_hops


def _load_agent_readiness_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "agent_readiness.py"
    spec = importlib.util.spec_from_file_location("agent_readiness_script", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_probe_env_strips_frontdoor_hops_and_preserves_pythonutf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_agent_readiness_module()
    monkeypatch.setenv("TG_FRONTDOOR_HOPS", "2")
    monkeypatch.setenv("TG_UNRELATED_PROBE_VAR", "keep-me")
    monkeypatch.delenv("PYTHONUTF8", raising=False)

    cleaned = module._clean_probe_env()

    assert "TG_FRONTDOOR_HOPS" not in cleaned
    assert cleaned["PYTHONUTF8"] == "1"
    assert cleaned["TG_UNRELATED_PROBE_VAR"] == "keep-me"


def test_run_check_passes_cleaned_env_without_frontdoor_hops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load_agent_readiness_module()
    monkeypatch.setenv("TG_FRONTDOOR_HOPS", "3")
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _cmd: "cmd")

    seen_envs: list[dict[str, str] | None] = []

    def fake_run(command, **kwargs):
        seen_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    check = module.Check(
        name="public-doctor-cmd",
        command=["cmd", "/c", "tg doctor --json --no-lsp"],
        description="Verify cmd.exe public tg can run sidecar-backed doctor.",
    )
    result = module.run_check(check, repo_root=tmp_path, expected_version="1.0.0")

    assert result["status"] == "passed"
    assert len(seen_envs) == 1
    assert seen_envs[0] is not None
    assert "TG_FRONTDOOR_HOPS" not in seen_envs[0]
    assert seen_envs[0]["PYTHONUTF8"] == "1"


def test_frontdoor_hops_still_enforces_limit_4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        frontdoor_hops.FRONTDOOR_HOPS_ENV,
        str(frontdoor_hops.MAX_FRONTDOOR_HOPS),
    )

    with pytest.raises(frontdoor_hops.FrontdoorHopLimitError, match="reached the limit of 4"):
        frontdoor_hops.next_hop_env()

    env, exit_code = frontdoor_hops.child_env_or_refusal()
    assert env is None
    assert exit_code == 2
