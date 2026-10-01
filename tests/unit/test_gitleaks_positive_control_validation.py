"""Positive-control validation for gitleaks.

Proves that NON-allowlisted AWS credentials are detected by the scanner.

Codex Sol CRITICAL finding #69: the Wave 1 positive control used EXAMPLE-suffix
credentials, which gitleaks v8.30.1 allowlists, so it proved nothing about
detection. This test plants credentials without the EXAMPLE marker, requires
exit code 1 plus the named rule, and then proves reversibility (credentials
removed from the scanned history -> exit code 0).

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

import pytest

# The aws-access-token rule (v8.30.1) matches exactly 4-char prefix + 16 chars of
# [A-Z2-7] bounded by \b. The originally planned 34-char key
# (AKIAIOSFODNN7THISISAFAKEKEYFORTEST) can never match, so it would make this
# control fail for the wrong reason. This 20-char key has no EXAMPLE marker.
ACCESS_KEY = "AKIAIOSFODNN7ZXCVBNM"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYFAKETESTKEY"
EXPECTED_RULE = "aws-access-token"


def _gitleaks_bin() -> str:
    """Locate a gitleaks v8 binary (GITLEAKS_BIN override, else PATH)."""
    exe = os.environ.get("GITLEAKS_BIN") or shutil.which("gitleaks")
    if not exe:
        pytest.skip("gitleaks not on PATH (set GITLEAKS_BIN)")
    ver = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=60)
    if ver.returncode != 0 or not ver.stdout.strip().startswith("8."):
        pytest.skip(f"gitleaks v8 required (rc={ver.returncode}, out={ver.stdout.strip()!r})")
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

        (repo / "secrets.txt").write_text(
            f"AWS_ACCESS_KEY_ID={ACCESS_KEY}\nAWS_SECRET_ACCESS_KEY={SECRET_KEY}\n"
        )
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
