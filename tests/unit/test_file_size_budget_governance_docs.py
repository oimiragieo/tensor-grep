"""Report-only governance-doc size visibility (AGENTS.md / CLAUDE.md / ...).

`scripts/file_size_budget.py::governance_doc_census` exists because AGENTS.md and
CLAUDE.md need size visibility without prescribing a minimum document length. This suite proves the census is real (bidirectional
control: a populated result differs observably from an empty one) rather than
proving the docs are small, and that --docs-report never touches the exit code.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = REPO_ROOT / "scripts" / "file_size_budget.py"

_spec = importlib.util.spec_from_file_location("file_size_budget_docs", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
budget = importlib.util.module_from_spec(_spec)
sys.modules["file_size_budget_docs"] = budget
_spec.loader.exec_module(budget)


def test_census_finds_agents_and_claude_with_plausible_counts() -> None:
    rows = {path: (lines, size) for path, lines, size in budget.governance_doc_census()}
    assert "AGENTS.md" in rows, "AGENTS.md missing from governance doc census"
    assert "CLAUDE.md" in rows, "CLAUDE.md missing from governance doc census"

    for relative in ("AGENTS.md", "CLAUDE.md"):
        raw = (REPO_ROOT / relative).read_bytes()
        assert raw, f"{relative} is unexpectedly empty"
        assert rows[relative] == (len(raw.splitlines()), len(raw))


def test_path_not_in_the_tuple_is_absent_from_result() -> None:
    rows = {path for path, _, _ in budget.governance_doc_census()}
    assert "README.md" not in rows, "census leaked a path outside GOVERNANCE_DOC_PATHS"


def test_untracked_path_in_the_tuple_is_skipped() -> None:
    """Force the untracked case via an explicit files= list -- never the real tree.

    A worktree artifact sharing a governance doc's filename must never be
    reported as if it were the real doc; passing files=[] simulates "not
    git-tracked" for every governance path without depending on repo state.
    """
    rows = budget.governance_doc_census(files=[])
    assert rows == []


def test_renderer_empty_census_is_explicit_not_silent() -> None:
    """The bidirectional control: empty must read differently from populated."""
    empty_text = budget._render_governance_doc_report([])
    assert "NO TRACKED DOCS MATCHED" in empty_text

    populated_text = budget._render_governance_doc_report([("AGENTS.md", 3893, 377239)])
    assert "NO TRACKED DOCS MATCHED" not in populated_text
    assert "AGENTS.md" in populated_text
    assert empty_text != populated_text


def test_docs_report_flag_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(_MODULE_PATH), "--docs-report"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AGENTS.md" in result.stdout
