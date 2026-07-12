"""Bidirectional-oracle freshness tests for `tg codemap --check` (Section 4 of the build spec): a
check that can only ever pass, or only ever fail, is not a check. Every "fresh" assertion here has
a matching "this exact mutation flips it to stale" sibling.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli.codemap import build_codemap, check_codemap_freshness

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "codemap_repo"


def _copy_fixture(dest_parent: Path) -> Path:
    dest = dest_parent / "repo"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


# ---------------------------------------------------------------------------
# (9) fresh map exits 0 (manifest, no git) / (10) edit/add/delete each flips to stale
# ---------------------------------------------------------------------------


def test_fresh_map_check_passes_with_manifest_oracle_no_git(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    build_codemap(repo)  # tmp_path is not inside any git repo -> git oracle is "unavailable"

    result = check_codemap_freshness(repo)
    assert result["fresh"] is True, result["reason"]


@pytest.mark.parametrize("mutation", ["edit", "add", "delete"])
def test_any_tree_mutation_flips_check_to_stale(tmp_path: Path, mutation: str) -> None:
    repo = _copy_fixture(tmp_path)
    build_codemap(repo)

    fresh_before = check_codemap_freshness(repo)
    assert fresh_before["fresh"] is True, fresh_before["reason"]

    target = repo / "pkg" / "core.py"
    if mutation == "edit":
        target.write_text(target.read_text(encoding="utf-8") + "\n# mutated\n", encoding="utf-8")
    elif mutation == "add":
        (repo / "pkg" / "new_module.py").write_text(
            "def new_fn():\n    return 1\n", encoding="utf-8"
        )
    else:
        target.unlink()

    result = check_codemap_freshness(repo)
    assert result["fresh"] is False, f"expected stale after {mutation!r}, got fresh"
    assert result["reason"]


def test_repeated_fresh_checks_are_stable(tmp_path: Path) -> None:
    """A check that never mutates state must return the same verdict every time it is called."""
    repo = _copy_fixture(tmp_path)
    build_codemap(repo)

    first = check_codemap_freshness(repo)
    second = check_codemap_freshness(repo)
    assert first == second


# ---------------------------------------------------------------------------
# (11) missing/corrupt coverage exits 1
# ---------------------------------------------------------------------------


def test_missing_coverage_json_is_stale(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    # No build_codemap call -> _coverage.json never existed.
    result = check_codemap_freshness(repo)
    assert result["fresh"] is False
    assert "missing" in result["reason"].lower()


def test_corrupt_coverage_json_is_stale(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = build_codemap(repo)

    coverage_path = Path(payload["out"]) / "_coverage.json"
    coverage_path.write_text("{not valid json", encoding="utf-8")

    result = check_codemap_freshness(repo)
    assert result["fresh"] is False
    assert "json" in result["reason"].lower()


def test_non_object_coverage_json_is_stale(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = build_codemap(repo)

    coverage_path = Path(payload["out"]) / "_coverage.json"
    coverage_path.write_text("[1, 2, 3]", encoding="utf-8")

    result = check_codemap_freshness(repo)
    assert result["fresh"] is False


# ---------------------------------------------------------------------------
# (12) git oracle commit+dirty transitions (tmp git repo)
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def git_fixture_repo(tmp_path: Path) -> Path:
    repo = _copy_fixture(tmp_path)
    # The generated map's own output is untracked immediately after `build_codemap` writes it;
    # `_repo_revision_identity`'s git-dirty signal is repo-wide (it has no --out-aware pathspec,
    # and this test suite must not modify that shared, multi-consumer helper to add one -- see
    # the "additive, read-only over shared infra" rule). Gitignoring the output directory keeps
    # THIS test isolated to the git oracle itself instead of confounding it with "did you commit
    # the regenerated pages" (a real, separate product question the final report calls out).
    (repo / ".gitignore").write_text("docs/code-map/\n", encoding="utf-8")
    _run_git(["init", "-b", "main", "."], cwd=repo)
    _run_git(["config", "user.email", "test@example.com"], cwd=repo)
    _run_git(["config", "user.name", "Test User"], cwd=repo)
    _run_git(["add", "-A"], cwd=repo)
    _run_git(["commit", "-m", "initial commit"], cwd=repo)
    return repo


def test_git_oracle_fresh_on_clean_repo(git_fixture_repo: Path) -> None:
    build_codemap(git_fixture_repo)
    result = check_codemap_freshness(git_fixture_repo)
    assert result["fresh"] is True, result["reason"]


def test_git_oracle_detects_new_commit(git_fixture_repo: Path) -> None:
    build_codemap(git_fixture_repo)

    core_py = git_fixture_repo / "pkg" / "core.py"
    core_py.write_text(core_py.read_text(encoding="utf-8") + "\n# more\n", encoding="utf-8")
    _run_git(["add", "-A"], cwd=git_fixture_repo)
    _run_git(["commit", "-m", "second commit"], cwd=git_fixture_repo)

    result = check_codemap_freshness(git_fixture_repo)
    assert result["fresh"] is False
    assert "git" in result["reason"].lower()


def test_git_oracle_detects_dirty_worktree(git_fixture_repo: Path) -> None:
    build_codemap(git_fixture_repo)

    core_py = git_fixture_repo / "pkg" / "core.py"
    core_py.write_text(
        core_py.read_text(encoding="utf-8") + "\n# dirty, uncommitted\n", encoding="utf-8"
    )

    result = check_codemap_freshness(git_fixture_repo)
    assert result["fresh"] is False
    assert "git" in result["reason"].lower()


def test_git_oracle_recovers_fresh_after_committing_the_drift(git_fixture_repo: Path) -> None:
    """Bidirectional: a dirty worktree reads stale, and regenerating + re-checking against that
    SAME (now committed) state reads fresh again -- proves the oracle isn't just permanently
    tripped, it tracks the CURRENT revision identity."""
    core_py = git_fixture_repo / "pkg" / "core.py"
    core_py.write_text(
        core_py.read_text(encoding="utf-8") + "\n# now committed\n", encoding="utf-8"
    )
    _run_git(["add", "-A"], cwd=git_fixture_repo)
    _run_git(["commit", "-m", "second commit"], cwd=git_fixture_repo)

    build_codemap(git_fixture_repo)
    result = check_codemap_freshness(git_fixture_repo)
    assert result["fresh"] is True, result["reason"]


# ---------------------------------------------------------------------------
# (13) truncated scan writes partial+exit-2-equivalent AND --check fails partial
# ---------------------------------------------------------------------------


def test_truncated_scan_writes_partial_and_check_fails(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    payload = build_codemap(repo, max_repo_files=2)  # fixture has far more than 2 files

    assert payload["partial"] is True
    assert payload["partial_reason"] == "scan_limit"
    assert payload["remediation"]

    coverage = json.loads((Path(payload["out"]) / "_coverage.json").read_text(encoding="utf-8"))
    assert coverage["partial"] is True

    result = check_codemap_freshness(repo)
    assert result["fresh"] is False
    assert "partial" in result["reason"].lower()


# ---------------------------------------------------------------------------
# PATH contract on the check path too.
# ---------------------------------------------------------------------------


def test_check_on_file_path_raises_not_a_directory(tmp_path: Path) -> None:
    repo = _copy_fixture(tmp_path)
    with pytest.raises(NotADirectoryError):
        check_codemap_freshness(repo / "README.md")


def test_check_on_missing_path_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        check_codemap_freshness(tmp_path / "does-not-exist")
