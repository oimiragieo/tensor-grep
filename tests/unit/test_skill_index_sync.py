"""Governance gate: the `.claude/skills/` folder set and the AGENTS.md / CLAUDE.md skill indices
must never drift apart.

Mirrors the content-pinning style of `tests/unit/test_public_docs_governance.py` (anchor every doc
path to the repo root via `__file__`, read the real doc text, assert on extracted facts) but instead
of pinning specific prose fragments, this file cross-checks two docs against a third source of
truth: the real folder listing on disk. A skill folder added without a doc update, or a doc mention
of a skill folder that does not (or no longer) exists, fails loudly here instead of silently
drifting -- exactly the class of bug this gate exists to catch (see AGENTS.md's CLAUDE.md skill-index
fix and the "24 vs 21" reconciliation note in the PR that introduced this test).
"""

from __future__ import annotations

import re
from pathlib import Path

# __file__-anchored, not cwd-relative -- see test_public_docs_governance.py's flake #37 note.
_REPO_ROOT = Path(__file__).resolve().parents[2]

SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"
AGENTS_DOC_PATH = _REPO_ROOT / "AGENTS.md"
CLAUDE_DOC_PATH = _REPO_ROOT / "CLAUDE.md"

AGENTS_SECTION_START = "## Skills\n"
AGENTS_SECTION_END = "\n## Dogfood follow-up workflow"
CLAUDE_SECTION_START = "## Skills that apply here"
CLAUDE_SECTION_END = None  # runs to end of file

# A skill name is mentioned in a doc in one of three shapes, and this gate must recognize all
# three so a future reformat (e.g. switching backtick names to hyperlinks) cannot silently defeat
# it:
#   1. a standalone backtick-quoted identifier: `tensor-grep-change-control`
#   2. a markdown link, optionally with backtick-quoted link text:
#      [tensor-grep-change-control](...) or [`tensor-grep-change-control`](...)
#   3. a `.claude/skills/<name>/...` path reference (how the single non-bucketed `tensor-grep`
#      skill is named, e.g. "`.claude/skills/tensor-grep/SKILL.md`" -- the folder name there is
#      not independently backtick-quoted, so patterns 1/2 alone would miss it)
_BACKTICK_TOKEN_RE = re.compile(r"`([a-z][a-z0-9-]*)`")
_LINK_TOKEN_RE = re.compile(r"\[`?([a-z][a-z0-9-]*)`?\]\([^)]*\)")
_SKILLS_PATH_RE = re.compile(r"\.claude[/\\]skills[/\\]([a-z][a-z0-9_-]*)[/\\]")


def _looks_like_skill_name(token: str) -> bool:
    # Real onboarding-library folders are always `tensor-grep` (bare) or `tensor-grep-*`, plus the
    # one non-prefixed skill `code-search-and-retrieval-reference`. Filtering on this shape before
    # comparing against the real folder set drops unrelated backtick-quoted tokens (flag names,
    # env vars, other doc paths) that would otherwise register as false "phantom skills".
    return (
        token == "tensor-grep"
        or token == "code-search-and-retrieval-reference"
        or token.startswith("tensor-grep-")
    )


def _extract_section(text: str, start_marker: str, end_marker: str | None) -> str:
    start = text.index(start_marker)
    remainder = text[start:]
    if end_marker is None:
        return remainder
    end = remainder.index(end_marker)
    return remainder[:end]


def _skill_tokens_in(section_text: str) -> set[str]:
    tokens = {m.group(1) for m in _BACKTICK_TOKEN_RE.finditer(section_text)}
    tokens |= {m.group(1) for m in _LINK_TOKEN_RE.finditer(section_text)}
    tokens |= {m.group(1) for m in _SKILLS_PATH_RE.finditer(section_text)}
    return {token for token in tokens if _looks_like_skill_name(token)}


def _real_skill_folders() -> set[str]:
    return {skill_doc.parent.name for skill_doc in SKILLS_DIR.glob("*/SKILL.md")}


def _agents_skills_section() -> str:
    agents = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    return _extract_section(agents, AGENTS_SECTION_START, AGENTS_SECTION_END)


def _claude_skills_section() -> str:
    claude = CLAUDE_DOC_PATH.read_text(encoding="utf-8")
    return _extract_section(claude, CLAUDE_SECTION_START, CLAUDE_SECTION_END)


def test_skills_directory_has_a_plausible_folder_count() -> None:
    # Bidirectional-oracle guard: if `.claude/skills/` went missing or the glob pattern broke, the
    # comparison tests below would compare empty-set-to-empty-set and vacuously pass. Fail loudly
    # on an implausibly small real-folder count instead of trusting a silent zero.
    real = _real_skill_folders()
    assert len(real) >= 15, (
        f"Only found {len(real)} `.claude/skills/*/SKILL.md` folders -- the glob pattern or "
        "SKILLS_DIR path is probably broken, not the repo."
    )


def test_every_real_skill_folder_is_named_in_agents_md() -> None:
    real = _real_skill_folders()
    mentioned = _skill_tokens_in(_agents_skills_section())

    missing = real - mentioned
    phantom = mentioned - real
    assert not missing, (
        f"AGENTS.md Skills section is missing these real skill folders: {sorted(missing)}"
    )
    assert not phantom, (
        f"AGENTS.md Skills section names these phantom skills (no matching .claude/skills/ folder): "
        f"{sorted(phantom)}"
    )


def test_every_real_skill_folder_is_named_in_claude_md() -> None:
    real = _real_skill_folders()
    mentioned = _skill_tokens_in(_claude_skills_section())

    missing = real - mentioned
    phantom = mentioned - real
    assert not missing, (
        f"CLAUDE.md Skills section is missing these real skill folders: {sorted(missing)}"
    )
    assert not phantom, (
        f"CLAUDE.md Skills section names these phantom skills (no matching .claude/skills/ folder): "
        f"{sorted(phantom)}"
    )


def test_agents_and_claude_skill_indices_name_the_same_skills() -> None:
    # AGENTS.md and CLAUDE.md are meant to be kept byte-identical for this specific enumeration
    # (see both docs' "kept byte-identical" cross-reference sentence). This is the set-level half
    # of that contract: even if the surrounding prose or bucket formatting diverges, the two docs
    # must never claim a different skill roster.
    agents_mentioned = _skill_tokens_in(_agents_skills_section())
    claude_mentioned = _skill_tokens_in(_claude_skills_section())

    assert agents_mentioned == claude_mentioned, (
        "AGENTS.md and CLAUDE.md skill indices have drifted apart -- "
        f"only in AGENTS.md: {sorted(agents_mentioned - claude_mentioned)}; "
        f"only in CLAUDE.md: {sorted(claude_mentioned - agents_mentioned)}"
    )
