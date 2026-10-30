"""Custom index exclusion must not hide unrelated Git changes."""

import subprocess
from pathlib import Path

import pytest

from tensor_grep.cli.codemap import build_codemap, check_codemap_freshness


@pytest.mark.parametrize("tracked", [False, True])
@pytest.mark.parametrize("shared_parent", [False, True])
def test_custom_index_does_not_hide_unrelated_dirty_files(
    tmp_path: Path, tracked: bool, shared_parent: bool
) -> None:
    repo = tmp_path / "repo"
    (repo / "custom").mkdir(parents=True)
    (repo / "one.py").write_text("x = 1\n", encoding="utf-8")
    index = repo / "custom" / "index.md"
    if tracked:
        index.write_text("old index\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    output = repo / "custom" / "maps" if shared_parent else repo / "maps"
    build_codemap(repo, out=output, index=index)
    assert check_codemap_freshness(repo, out=output, index=index)["fresh"] is True
    (repo / "custom" / "unrelated.py").write_text("y = 1\n", encoding="utf-8")
    assert check_codemap_freshness(repo, out=output, index=index)["fresh"] is False


@pytest.mark.parametrize("mutation", ["tracked", "untracked"])
def test_subdirectory_map_detects_enclosing_repository_changes(
    tmp_path: Path, mutation: str
) -> None:
    repo = tmp_path / "repo"
    project = repo / "project"
    (project / "custom").mkdir(parents=True)
    (project / "one.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "sibling.py").write_text("y = 1\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    index = project / "custom" / "index.md"
    output = project / "maps"
    build_codemap(project, out=output, index=index)
    assert check_codemap_freshness(project, out=output, index=index)["fresh"] is True
    target = repo / ("sibling.py" if mutation == "tracked" else "new_sibling.py")
    target.write_text("changed = True\n", encoding="utf-8")
    assert check_codemap_freshness(project, out=output, index=index)["fresh"] is False


@pytest.mark.parametrize("tracked", [False, True])
def test_subdirectory_index_name_does_not_hide_repo_root_file(
    tmp_path: Path, tracked: bool
) -> None:
    repo = tmp_path / "repo"
    project = repo / "project"
    project.mkdir(parents=True)
    (project / "one.py").write_text("x = 1\n", encoding="utf-8")
    sibling = repo / "index.md"
    if tracked:
        sibling.write_text("original\n", encoding="utf-8")
    for args in (
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "initial"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=30)
    index = project / "index.md"
    output = project / "maps"
    build_codemap(project, out=output, index=index)
    assert check_codemap_freshness(project, out=output, index=index)["fresh"] is True
    sibling.write_text("changed\n", encoding="utf-8")
    assert check_codemap_freshness(project, out=output, index=index)["fresh"] is False
