"""Positive-control validation for gitleaks.

Proves that NON-allowlisted AWS credentials are detected by the scanner.

The fixture uses a synthetic non-EXAMPLE key so the scanner must detect it.
It then removes the fixture and requires a clean scan.

Requires a gitleaks v8 binary: set GITLEAKS_BIN or put it on PATH. The test
skips (does not pass) when none is available.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NoReturn

import pytest

# The aws-access-token rule (v8.30.1) matches exactly 4-char prefix + 16 chars of
# [A-Z2-7] bounded by \b. The originally planned 34-char key
# (AKIAIOSFODNN7THISISAFAKEKEYFORTEST) can never match, so it would make this
# control fail for the wrong reason. This 20-char key has no EXAMPLE marker.
ACCESS_KEY = "AKIA" + "IOSFODNN7ZXCVBNM"
EXPECTED_RULE = "aws-access-token"


def _unavailable(reason: str) -> NoReturn:
    """Skip when no usable gitleaks exists -- unless ``TG_REQUIRE_GITLEAKS=1``, in which case a
    missing binary is a FAILURE. A lane that is supposed to run the control must set it, because
    a skipped control is indistinguishable from a passing one in a green summary.
    """
    if os.environ.get("TG_REQUIRE_GITLEAKS") == "1":
        pytest.fail(f"TG_REQUIRE_GITLEAKS=1 but {reason}")
    pytest.skip(reason)


def _gitleaks_bin() -> str:
    """Locate a gitleaks v8 binary (GITLEAKS_BIN override, else PATH)."""
    exe = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
    if not exe:
        _unavailable("gitleaks not on PATH (set GITLEAKS_BIN)")
    ver = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60)
    if ver.returncode != 0 or not ver.stdout.strip().startswith("8."):
        _unavailable(f"gitleaks v8 required (rc={ver.returncode}, out={ver.stdout.strip()!r})")
    return exe


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _scan(repo: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _gitleaks_bin(),
            "git",
            "--no-banner",
            "--log-opts=--all --no-merges",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            str(repo),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_gitleaks_detects_non_allowlisted_aws_credentials() -> None:
    """Plant non-EXAMPLE AWS credentials, require detection, then reversibility."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir) / "repo"
        repo.mkdir()
        report = Path(tmpdir) / "report.json"

        _git(repo, "init")
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        _git(repo, "config", "commit.gpgsign", "false")

        (repo / "file.txt").write_text("initial\n")
        _git(repo, "add", "--", "file.txt")
        _git(repo, "commit", "-m", "initial")

        # Control 0: a clean history must scan clean (baseline).
        baseline = _scan(repo, report)
        assert baseline.returncode == 0, (
            f"Baseline scan not clean: rc={baseline.returncode} {baseline.stdout}{baseline.stderr}"
        )

        # This fixed synthetic access key is the exact positive control for the named rule.
        (repo / "secrets.txt").write_text(f"AWS_ACCESS_KEY_ID={ACCESS_KEY}\n")
        _git(repo, "add", "--", "secrets.txt")
        _git(repo, "commit", "-m", "add credentials")

        detected = _scan(repo, report)
        assert detected.returncode == 1, (
            "Positive control FAILED: gitleaks did not detect planted credentials. "
            f"rc={detected.returncode} (expected 1). {detected.stdout}{detected.stderr}"
        )
        rules = {f["RuleID"] for f in json.loads(report.read_text())}
        assert EXPECTED_RULE in rules, f"Expected rule {EXPECTED_RULE!r}, got {sorted(rules)}"

        # Reversibility: drop the credential commit from history (a plain removal
        # commit would leave the secret in history and still be detected).
        _git(repo, "reset", "--hard", "HEAD~1")
        assert not (repo / "secrets.txt").exists()
        _git(repo, "reflog", "expire", "--expire=now", "--all")
        _git(repo, "gc", "--prune=now")

        clean = _scan(repo, report)
        assert clean.returncode == 0, (
            "Reversibility FAILED: findings remain after credentials removed from history. "
            f"rc={clean.returncode} (expected 0). {clean.stdout}{clean.stderr}"
        )


def test_a_missing_gitleaks_skips_by_default_and_fails_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Needs no gitleaks binary, so it runs everywhere: it proves the switch that makes the
    positive control enforceable. A skipped control looks green; a lane that must run it sets
    ``TG_REQUIRE_GITLEAKS=1`` and a missing binary then FAILS instead of skipping.
    """
    monkeypatch.delenv("GITLEAKS_BIN", raising=False)
    monkeypatch.setenv("PATH", "")  # no gitleaks can be found on PATH

    monkeypatch.delenv("TG_REQUIRE_GITLEAKS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _gitleaks_bin()

    monkeypatch.setenv("TG_REQUIRE_GITLEAKS", "1")
    with pytest.raises(pytest.fail.Exception, match="TG_REQUIRE_GITLEAKS=1"):
        _gitleaks_bin()
