from __future__ import annotations

import subprocess

from tensor_grep.cli import subprocess_policy


def test_ripgrep_timeout_defaults_to_60s(monkeypatch) -> None:
    # Fail-fast default: ripgrep does GB/s, so a >60s search is pathological; an agent must never
    # hang ~10 minutes (the old 600s default) before getting an actionable error.
    monkeypatch.delenv("TG_RG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 60.0


def test_ripgrep_timeout_env_override(monkeypatch) -> None:
    monkeypatch.setenv("TG_RG_TIMEOUT_SECONDS", "120")
    monkeypatch.delenv("TG_SIDECAR_TIMEOUT_MS", raising=False)
    assert subprocess_policy.configured_ripgrep_timeout_seconds() == 120.0


def test_run_subprocess_honors_timeout(monkeypatch) -> None:
    monkeypatch.setenv("TG_SUBPROCESS_TIMEOUT_SECONDS", "1")

    try:
        subprocess_policy.run_subprocess(
            ["python", "-c", "import time; time.sleep(5)"],
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return

    raise AssertionError("expected subprocess timeout")
