"""Papercut guard: stop losing live-dogfood `.claude/skills/*/SKILL.md` corrections to an
unstaged working tree.

Three times during this campaign a real skill correction -- in one case a REVERSAL telling
agents that a previously-broken thing was fixed -- sat MODIFIED but UNSTAGED in the main
checkout, one `git checkout -b` / `git stash` / branch-switch away from being silently lost.

This script reports (does not auto-fix) any file under `.claude/skills/` that has an unstaged
change in the working tree. It intentionally does NOT fire on staged or committed edits -- only
on the unstaged half of a change, exactly the state that is invisible to `git log`/`git diff
--cached` and therefore easy for a human or agent to forget about mid-session.

Prefer this over a git hook: a hook mutates the operator's local git config (`core.hooksPath` or
`.git/hooks/*`), which needs explicit consent per this repo's house rules. A plain script any
agent or human can run (and that `pytest` also exercises for regression coverage) needs none.

Usage:
    python scripts/check_unstaged_skill_edits.py

Exit codes:
    0  clean -- no unstaged edits under .claude/skills/ (silent, prints nothing)
    1  unstaged edits found under .claude/skills/ (reported to stderr)
    2  could not inspect the working tree (not a git repo, git missing, etc.)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_PATHSPEC = ".claude/skills/"


def find_unstaged_skill_edits(repo_root: Path, pathspec: str = SKILLS_PATHSPEC) -> list[str]:
    """Return repo-relative paths under `pathspec` that have an UNSTAGED change.

    `git diff --name-only` (no `--cached`) compares the working tree against the index, which by
    construction reports only the unstaged half of a change: a fully-staged edit does not appear
    here (it differs from HEAD but not from the index), and a committed edit does not appear here
    either (working tree and index both already match it). A file that is staged and then further
    edited DOES appear here, for its real remaining unstaged delta.
    """
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--", pathspec],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        line.strip().replace("\\", "/") for line in completed.stdout.splitlines() if line.strip()
    ]


def format_report(paths: list[str]) -> str:
    listed = "\n".join(f"  - {path}" for path in paths)
    return (
        "Unstaged edit(s) detected under .claude/skills/ -- these are live dogfood corrections "
        "and are one `git checkout -b` / `git stash` / branch-switch away from being lost:\n"
        f"{listed}\n"
        "Stage and commit them (or note them explicitly before switching branches)."
    )


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    _ = argv
    root = repo_root if repo_root is not None else ROOT
    try:
        paths = find_unstaged_skill_edits(root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(
            f"unstaged-skill-edit guard failed to inspect the working tree: {exc}",
            file=sys.stderr,
        )
        return 2

    if paths:
        print(format_report(paths), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
