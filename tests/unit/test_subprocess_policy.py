from __future__ import annotations

import subprocess

from tensor_grep.cli import subprocess_policy


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
