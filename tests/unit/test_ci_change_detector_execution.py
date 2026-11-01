"""Run the actual workflow detectors against disposable Git histories."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
GIT = shutil.which("git")
BASH = (
    next(
        (str(p / "bin/bash.exe") for p in Path(GIT).parents if (p / "bin/bash.exe").is_file()), None
    )
    if sys.platform == "win32" and GIT
    else shutil.which("bash")
)
pytestmark = pytest.mark.skipif(not GIT or not BASH, reason="Git and Bash required")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        [GIT, "-c", "user.name=CI detector test", "-c", "user.email=ci@example.invalid", *args],
        cwd=root,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def _history(root: Path, changed: str) -> str:
    _git(root, "init", "-q")
    for name in ("src/example.py", "docs/example.md", "LICENSE"):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("before\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / changed).write_text("after\n")
    _git(root, "commit", "-qam", "change")
    return base


def _detect(root: Path, detector: str, base: str, event: str):
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["changes"]["steps"]
    script = next(s["run"] for s in steps if s.get("id") == detector)
    script = script.replace("${{ github.event_name }}", event).replace(
        "${{ github.event.pull_request.base.sha }}", base
    )
    output = root / "github-output"
    result = subprocess.run(
        [BASH, "-c", script],
        cwd=root,
        env={**os.environ, "GITHUB_OUTPUT": output.as_posix()},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result, output.read_text().strip() if output.exists() else ""


@pytest.mark.parametrize("detector", ["code", "docs"])
@pytest.mark.parametrize("changed", ["src/example.py", "docs/example.md", "LICENSE"])
def test_detector_valid_comparison(tmp_path, detector, changed):
    base = _history(tmp_path, changed)
    result, output = _detect(tmp_path, detector, base, "pull_request")
    expected = changed.startswith("src/" if detector == "code" else "docs/")
    assert result.returncode == 0, result.stderr
    assert output == f"{detector}={str(expected).lower()}"


@pytest.mark.parametrize("detector", ["code", "docs"])
def test_detector_refuses_missing_base(tmp_path, detector):
    _history(tmp_path, "src/example.py")
    result, output = _detect(tmp_path, detector, "f" * 40, "pull_request")
    assert result.returncode != 0, "detector swallowed diff failure"
    assert not output, "failed comparison emitted a changes decision"


@pytest.mark.parametrize("detector", ["code", "docs"])
def test_non_pr_detector_keeps_full_checks(tmp_path, detector):
    result, output = _detect(tmp_path, detector, "unavailable", "push")
    assert result.returncode == 0, result.stderr
    assert output == f"{detector}=true"
