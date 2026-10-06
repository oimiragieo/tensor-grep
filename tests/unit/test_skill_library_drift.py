"""Public skill publication boundary and documentation integrity checks."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SKILLS = {
    "tensor-grep",
    "tensor-grep-build-and-env",
    "tensor-grep-architecture-contract",
    "tensor-grep-add-language",
    "tensor-grep-validation-and-qa",
    "tensor-grep-docs-and-writing",
}
PUBLIC_FILES = {f".claude/skills/{name}/SKILL.md" for name in PUBLIC_SKILLS} | {
    ".claude/skills/tensor-grep/REFERENCE.md",
}


def _unexpected_paths(paths: set[str]) -> set[str]:
    return {path for path in paths if path.startswith(".claude/")} - PUBLIC_FILES


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        check=False,
    )


def test_only_reviewed_claude_files_are_tracked() -> None:
    result = _git(ROOT, "ls-files", "-z", "--", ".claude")
    assert result.returncode == 0, result.stderr
    tracked = set(filter(None, result.stdout.split("\0")))
    assert tracked == PUBLIC_FILES
    assert not _unexpected_paths(tracked)


@pytest.mark.parametrize(
    "path",
    [
        ".claude/skills/private-research/SKILL.md",
        ".claude/skills/tensor-grep/internal-notes.md",
        ".claude/skills/tensor-grep/references/private.md",
        ".claude/workflows/internal.js",
        ".claude/settings.local.json",
        ".claude/skill_rules.json",
        ".claude/private.txt",
    ],
)
def test_private_files_are_ignored_and_rejected_if_force_added(tmp_path: Path, path: str) -> None:
    assert _git(tmp_path, "init", "--quiet").returncode == 0
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("private fixture", encoding="utf-8")
    ignored = _git(tmp_path, "check-ignore", "--no-index", "--", path)
    assert ignored.returncode == 0, ignored.stderr
    assert _git(tmp_path, "add", "--", path).returncode != 0
    assert _git(tmp_path, "add", "--force", "--", path).returncode == 0
    staged = _git(tmp_path, "ls-files", "-z")
    assert staged.returncode == 0
    assert _unexpected_paths(set(filter(None, staged.stdout.split("\0")))) == {path}


def test_public_files_can_be_added(tmp_path: Path) -> None:
    assert _git(tmp_path, "init", "--quiet").returncode == 0
    shutil.copyfile(ROOT / ".gitignore", tmp_path / ".gitignore")
    for path in PUBLIC_FILES:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("public fixture", encoding="utf-8")
        assert _git(tmp_path, "check-ignore", "--no-index", "--", path).returncode == 1
        assert _git(tmp_path, "add", "--", path).returncode == 0
    staged = _git(tmp_path, "ls-files", "-z")
    assert staged.returncode == 0
    assert set(filter(None, staged.stdout.split("\0"))) == PUBLIC_FILES


def test_public_skills_have_frontmatter_and_no_internal_workflow_dependencies() -> None:
    forbidden = re.compile(r"Fable|Sonnet|Opus|thinktank|CEO|model-router|C:\\Users\\", re.I)
    for path in sorted(PUBLIC_FILES):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert text.strip(), path
        assert not forbidden.search(text), path
        if path.endswith("SKILL.md"):
            assert text.startswith("---\n"), path
            assert f"name: {Path(path).parent.name}\n" in text, path
            assert "description:" in text, path


def test_public_skill_links_resolve() -> None:
    checked = 0
    for path in sorted(PUBLIC_FILES):
        source = ROOT / path
        text = source.read_text(encoding="utf-8")
        for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            target = (source.parent / link.split("#", 1)[0]).resolve()
            assert target.is_relative_to(ROOT), (path, link)
            assert target.is_file(), (path, link)
            checked += 1
    assert checked >= len(PUBLIC_SKILLS), (
        "Each public skill should link to source or public documentation"
    )
