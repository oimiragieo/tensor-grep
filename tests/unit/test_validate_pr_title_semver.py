from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_validator(
    *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "validate_pr_title_semver.py"
    command = [sys.executable, str(script), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, env=env)


def test_validate_pr_title_semver_should_accept_minor_title() -> None:
    result = _run_validator("--title", "feat: add AI release-intent gate")

    assert result.returncode == 0
    assert "release_intent=minor" in result.stdout


def test_validate_pr_title_semver_should_accept_major_title() -> None:
    result = _run_validator("--title", "feat!: remove legacy updater flow")

    assert result.returncode == 0
    assert "release_intent=major" in result.stdout


def test_validate_pr_title_semver_should_accept_patch_title() -> None:
    result = _run_validator("--title", "fix: report latest PyPI version accurately")

    assert result.returncode == 0
    assert "release_intent=patch" in result.stdout


def test_validate_pr_title_semver_should_reject_non_conventional_title() -> None:
    result = _run_validator("--title", "update release flow")

    assert result.returncode == 1
    assert "Invalid PR title for semantic release." in result.stderr


def test_validate_pr_title_semver_should_read_github_event_payload(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"pull_request": {"title": "fix: stabilize release automation"}}),
        encoding="utf-8",
    )

    result = _run_validator("--event-path", str(event_path))

    assert result.returncode == 0
    assert "release_intent=patch" in result.stdout
