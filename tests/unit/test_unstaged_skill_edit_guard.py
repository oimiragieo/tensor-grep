"""Guard: catch live-dogfood edits to `.claude/skills/*/SKILL.md` that are sitting MODIFIED
but UNSTAGED in the working tree.

Three times during this campaign a real skill correction (in one case a reversal telling agents
a previously-broken thing was still broken) sat unstaged in the main checkout -- one `git checkout
-b` or `git stash` away from being silently lost. This is a papercut guard against that specific
loss, not a general dirty-tree linter: it must fire ONLY on the unstaged half of a change (Part C
below), stay silent on a clean tree (Part B), and report clearly when it does fire (Part A).

Uses a real temporary git repository (not a mocked `git diff` string) because the property under
test -- "staged and committed edits are invisible to this guard, only unstaged ones trip it" -- is
exactly what `git diff` (no `--cached`) computes; mocking that computation would just be re-asserting
the mock.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[2]
    script_path = root / "scripts" / "check_unstaged_skill_edits.py"
    spec = importlib.util.spec_from_file_location("check_unstaged_skill_edits", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo_with_committed_skill(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")

    skill_dir = repo / ".claude" / "skills" / "dummy-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Dummy skill\n\nSlice 2 is broken.\n", encoding="utf-8")

    other_file = repo / "README.md"
    other_file.write_text("hello\n", encoding="utf-8")

    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "init")
    return repo


# ---------------------------------------------------------------------------
# A: a deliberate unstaged edit under .claude/skills/ is reported clearly.
# ---------------------------------------------------------------------------


def test_reports_deliberate_unstaged_skill_edit(tmp_path: Path) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    skill_md = repo / ".claude" / "skills" / "dummy-skill" / "SKILL.md"
    skill_md.write_text("# Dummy skill\n\nSlice 2 is fixed now.\n", encoding="utf-8")

    found = module.find_unstaged_skill_edits(repo)

    assert found == [".claude/skills/dummy-skill/SKILL.md"]

    report = module.format_report(found)
    assert ".claude/skills/dummy-skill/SKILL.md" in report
    assert "unstaged" in report.lower()


def test_main_exits_nonzero_and_prints_report_on_unstaged_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    skill_md = repo / ".claude" / "skills" / "dummy-skill" / "SKILL.md"
    skill_md.write_text("# Dummy skill\n\nSlice 2 is fixed now.\n", encoding="utf-8")

    exit_code = module.main([], repo_root=repo)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert ".claude/skills/dummy-skill/SKILL.md" in captured.err


# ---------------------------------------------------------------------------
# B: CONTROL ARM -- a clean .claude/skills/ tree passes silently, blocks nothing.
# ---------------------------------------------------------------------------


def test_clean_tree_passes_silently(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    found = module.find_unstaged_skill_edits(repo)
    assert found == []

    exit_code = module.main([], repo_root=repo)
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""


# ---------------------------------------------------------------------------
# C: must NOT fire on staged or committed skill edits -- only unstaged ones.
# ---------------------------------------------------------------------------


def test_does_not_fire_on_staged_skill_edit(tmp_path: Path) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    skill_md = repo / ".claude" / "skills" / "dummy-skill" / "SKILL.md"
    skill_md.write_text("# Dummy skill\n\nSlice 2 is fixed now.\n", encoding="utf-8")
    _run_git(repo, "add", str(skill_md))

    found = module.find_unstaged_skill_edits(repo)

    assert found == []


def test_does_not_fire_on_committed_skill_edit(tmp_path: Path) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    skill_md = repo / ".claude" / "skills" / "dummy-skill" / "SKILL.md"
    skill_md.write_text("# Dummy skill\n\nSlice 2 is fixed now.\n", encoding="utf-8")
    _run_git(repo, "add", str(skill_md))
    _run_git(repo, "commit", "-q", "-m", "fix skill")

    found = module.find_unstaged_skill_edits(repo)

    assert found == []


def test_does_not_fire_on_unstaged_edit_outside_skills_dir(tmp_path: Path) -> None:
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    (repo / "README.md").write_text("hello, edited\n", encoding="utf-8")

    found = module.find_unstaged_skill_edits(repo)

    assert found == []


def test_fires_only_for_the_partially_staged_remainder(tmp_path: Path) -> None:
    """A file that is staged AND then further edited still has a real unstaged component --
    the guard should still catch that remainder rather than treating "staged at all" as clean."""
    module = _load_module()
    repo = _init_repo_with_committed_skill(tmp_path)

    skill_md = repo / ".claude" / "skills" / "dummy-skill" / "SKILL.md"
    skill_md.write_text("# Dummy skill\n\nfirst edit\n", encoding="utf-8")
    _run_git(repo, "add", str(skill_md))
    skill_md.write_text("# Dummy skill\n\nfirst edit\n\nsecond edit, unstaged\n", encoding="utf-8")

    found = module.find_unstaged_skill_edits(repo)

    assert found == [".claude/skills/dummy-skill/SKILL.md"]
