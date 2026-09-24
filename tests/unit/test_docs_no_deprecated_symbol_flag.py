"""Governance gate: live teaching docs must teach positional SYMBOL, not the deprecated `--symbol`
flag, for the five symbol commands (`defs`, `source`, `refs`, `callers`, `impact`, and the
`blast-radius*` family).

`main._resolve_path_and_symbol` (src/tensor_grep/cli/main.py) prints a deprecation warning and
still accepts `--symbol` for those commands -- the flag is not going away -- but a live doc that
teaches `--symbol` as the example form is teaching a deprecated idiom to every reader (human or
agent) who copies it. This scans a fixed set of LIVE teaching docs for a `--symbol` occurrence that
sits on the same line as one of the target command names, and fails unless that line is itself
describing the deprecation/compatibility story (allowlisted by keyword) or is documenting an
unrelated `tg ledger` flag of the same name (ledger's `--symbol` has no positional alternative and
is not deprecated).

Scope is deliberately narrow to LIVE docs only -- dated plans, CHANGELOG, BACKLOG, and historical
fix-plan docs are allowed to say whatever was true when they were written and are not scanned here.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The live teaching docs in scope for this gate. Adding a new live doc that teaches `tg` usage
# should add it here; dated/historical docs must never be added.
_SCOPED_PATHS = [
    _REPO_ROOT / "SKILL.md",
    _REPO_ROOT / "AGENTS.md",
    _REPO_ROOT / "docs" / "CONTRACTS.md",
]


def _rebuild_guide_paths() -> list[Path]:
    guides_dir = _REPO_ROOT / "docs" / "rebuild-guides"
    return sorted(p for p in guides_dir.glob("*.md") if p.is_file())


def _skill_doc_paths() -> list[Path]:
    skills_dir = _REPO_ROOT / ".claude" / "skills"
    paths: list[Path] = []
    for sub in sorted(skills_dir.iterdir()):
        if not sub.is_dir() or not sub.name.startswith("tensor-grep"):
            continue
        for name in ("SKILL.md", "REFERENCE.md"):
            candidate = sub / name
            if candidate.is_file():
                paths.append(candidate)
    return paths


def _all_scoped_paths() -> list[Path]:
    return _SCOPED_PATHS + _rebuild_guide_paths() + _skill_doc_paths()


# The five symbol commands (plus the blast-radius family) whose `--symbol` usage is deprecated in
# favor of positional SYMBOL. Matched as whole words so "impact" doesn't false-match e.g.
# "high-impact".
_TARGET_COMMAND_RE = re.compile(
    r"\b(defs|source|refs|callers|impact|blast-radius[a-z-]*)\b", re.IGNORECASE
)

_SYMBOL_FLAG_RE = re.compile(r"--symbol\b")

# A line is allowed to still mention `--symbol` for a target command if it is itself describing
# the deprecation/compatibility story, or if the `--symbol` on that line actually belongs to
# `tg ledger` (a genuinely separate, non-deprecated flag with no positional form).
_ALLOWLIST_KEYWORDS = (
    "deprecated",
    "deprecation",
    "backward compat",
    "still accepted",
    "still works",
    "ledger",
)


def _is_allowlisted(line: str) -> bool:
    lowered = line.lower()
    return any(keyword in lowered for keyword in _ALLOWLIST_KEYWORDS)


def _find_violations(path: Path) -> list[str]:
    violations = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not _SYMBOL_FLAG_RE.search(line):
            continue
        if not _TARGET_COMMAND_RE.search(line):
            continue
        if _is_allowlisted(line):
            continue
        violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")
    return violations


def test_live_docs_teach_positional_symbol_not_deprecated_flag() -> None:
    all_violations: list[str] = []
    for path in _all_scoped_paths():
        all_violations.extend(_find_violations(path))

    assert not all_violations, (
        "Live teaching docs must teach positional SYMBOL (e.g. `tg defs [PATH] SYMBOL`), not "
        "the deprecated `--symbol` flag, for defs/source/refs/callers/impact/blast-radius*. "
        "Deprecation/compatibility statements and unrelated `tg ledger --symbol` usage are "
        "allowlisted (see _ALLOWLIST_KEYWORDS). Violations:\n" + "\n".join(all_violations)
    )


def test_scoped_paths_are_nonempty() -> None:
    # A silently-empty scope would make the gate above vacuously green. Guard against a future
    # refactor (e.g. renaming docs/rebuild-guides) hollowing this test out.
    assert len(_all_scoped_paths()) >= 5
