"""Codemap freshness checks suppress Git's optional index refresh."""

import os
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli import evidence_receipt
from tensor_grep.cli.codemap import build_codemap, check_codemap_freshness


@pytest.mark.parametrize("custom_index", [False, True])
def test_freshness_does_not_refresh_git_index(
    tmp_path: Path, monkeypatch, custom_index: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "one.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    for args in (
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    calls = []
    original_run = evidence_receipt.run_subprocess

    def record(args, **kwargs):
        calls.append(args)
        return original_run(args, **kwargs)

    monkeypatch.setattr(evidence_receipt, "run_subprocess", record)
    opts = {"out": repo / "maps", "index": repo / "custom.md"} if custom_index else {}
    build_codemap(repo, **opts)
    assert all("--no-optional-locks" not in args for args in calls)
    calls.clear()
    assert check_codemap_freshness(repo, **opts)["fresh"] is True
    assert any("--no-optional-locks" in args and "status" in args for args in calls)
    index = repo / ".git" / "index"
    original = index.read_bytes()
    current = source.stat()
    os.utime(source, ns=(current.st_atime_ns, current.st_mtime_ns + 5_000_000_000))
    assert check_codemap_freshness(repo, **opts)["fresh"] is True
    assert index.read_bytes() == original

    subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, timeout=30
    )
    assert index.read_bytes() != original
    source.write_text("x = 2\n", encoding="utf-8")
    assert check_codemap_freshness(repo, **opts)["fresh"] is False


@pytest.mark.parametrize("custom_index", [False, True])
def test_revision_identity_optional_lock_control(
    tmp_path: Path, monkeypatch, custom_index: bool
) -> None:
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "one.py"
    source.write_text("x = 1\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    opts = {"exclude_files": ["custom.md"]} if custom_index else {}
    if custom_index:
        (repo / "custom.md").write_text("generated", encoding="utf-8")
    index = repo / ".git" / "index"
    original = index.read_bytes()
    current = source.stat()
    os.utime(source, ns=(current.st_atime_ns, current.st_mtime_ns + 5_000_000_000))
    without_refresh = evidence_receipt._repo_revision_identity(repo, no_optional_locks=True, **opts)
    assert without_refresh["status"] == "present" and not without_refresh["dirty"]
    assert index.read_bytes() == original
    with_refresh = evidence_receipt._repo_revision_identity(repo, **opts)
    assert with_refresh == without_refresh
    assert index.read_bytes() != original
    source.write_text("x = 2\n", encoding="utf-8")
    dirty_no_refresh = evidence_receipt._repo_revision_identity(
        repo, no_optional_locks=True, **opts
    )
    dirty_default = evidence_receipt._repo_revision_identity(repo, **opts)
    assert dirty_no_refresh == dirty_default
    assert dirty_no_refresh["dirty"] is True
