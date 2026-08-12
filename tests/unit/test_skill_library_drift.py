"""Governance gate: the `.claude/skills/` library must not drift away from the repo it documents.

Sibling of `test_skill_index_sync.py`, which pins the skill *roster* (the folder set vs the two
docs' indices). This file pins two DIFFERENT properties that the roster gate is blind to, both of
which have gone wrong in practice:

1. **Citations resolve.** The library carries ~770 `file:line` citations. Nothing checked that the
   cited files still exist or that the line numbers are inside them, so a renamed or deleted module
   left dangling anchors that read as authoritative.
2. **The stated library size matches the folders it names.** The roster gate compares NAME SETS, so
   the prose count beside the list is entirely unguarded.

WHAT THIS GATE DOES *NOT* CHECK -- read this before trusting a green run:

    It verifies that a cited file EXISTS and that the cited line is IN RANGE.
    It does NOT verify that the cited line still CONTAINS what the skill claims.

That distinction is the whole point of the naming below. Code churns constantly; a citation can
resolve, sit well inside the file, and point at completely unrelated code. Catching that needs a
human or agent read, and no green result here is evidence it happened. (This is also why house
style prefers citing a SYMBOL over a line number.)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# `__file__`-anchored, not cwd-relative -- see test_public_docs_governance.py's flake #37 note.
_REPO_ROOT = Path(__file__).resolve().parents[2]

SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"
AGENTS_DOC_PATH = _REPO_ROOT / "AGENTS.md"
CLAUDE_DOC_PATH = _REPO_ROOT / "CLAUDE.md"

# Matches `some/path/file.py:123` and `file.rs:12-40`. The leading guard keeps it from biting into
# the middle of a longer path fragment.
_CITATION_RE = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9_][A-Za-z0-9_/.-]*\.(?:py|rs|toml|yml|yaml|json|md)):(\d+)(?:-(\d+))?"
)

# Deliberately illustrative paths inside instructional prose, not claims about this repo.
_PLACEHOLDER_HINTS = ("path/to/", "path/file", "<", "example.com", "your-", "foo.", "bar.")

# The prose beside the index reads "... (`.claude/skills/tensor-grep-*` + `code-search-and-
# retrieval-reference`, **N skills**)". That sentence DEFINES which folders it counts, and it
# deliberately excludes the bare `tensor-grep` usage skill, which is listed on its own line above
# it. Deriving N from the raw folder count instead of from this definition produces a permanent
# off-by-one that fires on a CORRECT repo -- verified against three historical revisions
# (20/20, 26/26, 27/27 all correct under this rule; all three "wrong" under a raw folder count).
# The regex is anchored to the DEFINING sentence (the index sentence that names
# `code-search-and-retrieval-reference` immediately before the count), not to any `**N skills**`
# anywhere in the file -- AGENTS.md carries dated historical narratives that mention an old skill
# count (e.g. a 2026-08-01 audit that read "`**28 skills**`" when that was true), and the never-
# rewrite-a-dated-receipt law forbids editing them, so a whole-file leftmost match would read the
# historical number instead of the index's.
_STATED_COUNT_RE = re.compile(r"code-search-and-retrieval-reference`, \*\*(\d+) skills\*\*")


def _library_skill_folders() -> set[str]:
    """The folders the docs' count sentence actually names."""
    return {
        skill_doc.parent.name
        for skill_doc in SKILLS_DIR.glob("*/SKILL.md")
        if skill_doc.parent.name.startswith("tensor-grep-")
        or skill_doc.parent.name == "code-search-and-retrieval-reference"
    }


def _tracked_path_index() -> dict[str, list[str]]:
    """Index every path SUFFIX of every git-tracked file.

    Two decisions here are load-bearing, and both were found by measurement rather than design:

    * **Suffix indexing**, because skills cite in three equally legitimate shapes -- bare basename
      (`ripgrep_backend.py:123`), partial suffix (`cli/main.py:6737`), and full repo-relative path.
      Resolving only the third marks ~60% of a correct library "missing", which is precisely how an
      earlier iteration of this idea drowned its own signal in false positives.
    * **`git ls-files`, not a filesystem walk**, because this repo routinely holds stale agent
      worktrees under `.claude/worktrees/` and snapshot copies under `src/.tensor-grep/checkpoints/`
      -- each a full source tree. A walk made EVERY citation match 7-21 paths, so everything went
      ambiguous and the scan checked nothing while reporting success. Tracked files are also
      exactly what CI sees.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - environment guard
        raise AssertionError(
            "COULD NOT LOOK: `git ls-files` failed, so this gate could not enumerate the tracked "
            f"files it resolves citations against. This is NOT a clean result -- it is an "
            f"un-run check. Underlying error: {exc}"
        ) from exc

    index: dict[str, list[str]] = {}
    for rel in filter(None, completed.stdout.split("\0")):
        parts = rel.split("/")
        for i in range(len(parts)):
            index.setdefault("/".join(parts[i:]), []).append(rel)
    return index


def _line_count(rel_path: str) -> int:
    with (_REPO_ROOT / rel_path).open("rb") as handle:
        return sum(1 for _ in handle)


def _scan_citations() -> tuple[list[str], list[str], int, int]:
    """Return (missing, out_of_range, resolved_count, ambiguous_count)."""
    index = _tracked_path_index()
    missing: list[str] = []
    out_of_range: list[str] = []
    resolved = 0
    ambiguous = 0

    for doc in sorted(SKILLS_DIR.rglob("*.md")):
        text = doc.read_text(encoding="utf-8", errors="replace")
        for doc_lineno, line in enumerate(text.splitlines(), 1):
            for match in _CITATION_RE.finditer(line):
                cited, start, end = match.group(1), int(match.group(2)), match.group(3)
                if any(hint in cited for hint in _PLACEHOLDER_HINTS):
                    continue

                targets = index.get(cited.replace("\\", "/"), [])
                where = f"{doc.relative_to(_REPO_ROOT).as_posix()}:{doc_lineno}"

                if not targets:
                    missing.append(f"{where} cites `{cited}:{start}` -- no such tracked file")
                    continue
                if len(targets) > 1:
                    # The gate cannot tell which file is meant, so it makes no claim. Reported as
                    # coverage, never as a violation.
                    ambiguous += 1
                    continue

                top = int(end) if end else start
                total = _line_count(targets[0])
                if top > total:
                    out_of_range.append(
                        f"{where} cites `{cited}:{top}` but {targets[0]} has only {total} lines"
                    )
                else:
                    resolved += 1

    return missing, out_of_range, resolved, ambiguous


def test_citation_scan_actually_examined_citations() -> None:
    # Bidirectional-oracle guard, mirroring test_skill_index_sync.py's folder-count floor. If the
    # citation regex stopped matching (a reformat, a changed convention) or SKILLS_DIR moved, every
    # assertion below would compare empty-to-empty and pass vacuously. A gate that examined nothing
    # must fail loudly rather than report the silence as a clean library.
    _, _, resolved, ambiguous = _scan_citations()
    assert resolved + ambiguous >= 400, (
        f"Only {resolved + ambiguous} citations were examined across `.claude/skills/`. The scan "
        "found far fewer than this library carries, so the regex or SKILLS_DIR is probably broken "
        "-- do not read this run as a clean result."
    )


def test_every_skill_citation_resolves_to_a_tracked_file() -> None:
    missing, _, _, _ = _scan_citations()
    assert not missing, (
        "Skill docs cite files that are not tracked in git. Either the path is stale, or the file "
        "is new and was never `git add`ed (CI only ever sees committed state):\n  "
        + "\n  ".join(missing)
    )


def test_no_skill_citation_points_past_the_end_of_its_file() -> None:
    _, out_of_range, _, _ = _scan_citations()
    assert not out_of_range, (
        "Skill docs cite line numbers beyond the end of the file. The file shrank underneath the "
        "citation:\n  " + "\n  ".join(out_of_range)
    )


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

# `**Form 7 — the MEASUREMENT that cannot discriminate ...`. These headings are the ONLY authority
# on how many forms exist; both docs' prose counts are derived from them, never from each other.
_ORACLE_FORM_RE = re.compile(r"^\*\*Form (\d+) [-—]", re.MULTILINE)
_AGENTS_FORM_COUNT_RE = re.compile(r"Verification-Oracle Family [-—] (\w+) forms")
_SKILL_FORM_COUNT_RE = re.compile(r"(\w+) distinct forms")
_VALIDATION_SKILL = SKILLS_DIR / "tensor-grep-validation-and-qa" / "SKILL.md"


def _word_or_digit(token: str) -> int | None:
    lowered = token.lower()
    if lowered in _NUMBER_WORDS:
        return _NUMBER_WORDS[lowered]
    return int(token) if token.isdigit() else None


def test_stated_oracle_form_count_matches_the_forms_actually_enumerated() -> None:
    """Same CLASS as the skill-count gate: a prose number sitting beside an enumeration.

    Modelled as a class rather than a second one-off case (AGENTS.md, "Model The Class, Don't
    Enumerate The Cases"). Receipt: AGENTS.md's header read "nine forms" while ten were present
    for four days, and the sibling skill -- which had the count right and even documented the
    miscount -- misdated two of them. Each doc was half correct, so reading either one alone
    confirmed it. Adding a form is a TWO-FILE edit and both halves drifted independently.
    """
    agents_text = AGENTS_DOC_PATH.read_text(encoding="utf-8")
    enumerated = {int(n) for n in _ORACLE_FORM_RE.findall(agents_text)}
    assert enumerated, (
        "No `**Form N —**` headings found in AGENTS.md -- the heading style changed, so this gate "
        "examined nothing. Do not read this as a clean result."
    )

    # The forms must be a contiguous 1..N run; a gap means one was deleted or misnumbered.
    expected = set(range(1, max(enumerated) + 1))
    assert enumerated == expected, (
        f"AGENTS.md oracle forms are not contiguous: found {sorted(enumerated)}, "
        f"expected 1..{max(enumerated)}. A missing number means a form was dropped or misnumbered."
    )
    real = len(enumerated)

    for doc_path, pattern in (
        (AGENTS_DOC_PATH, _AGENTS_FORM_COUNT_RE),
        (_VALIDATION_SKILL, _SKILL_FORM_COUNT_RE),
    ):
        match = pattern.search(doc_path.read_text(encoding="utf-8"))
        assert match is not None, (
            f"{doc_path.name} no longer states how many verification-oracle forms exist. The count "
            "is part of the contract with its enumeration; restore it rather than deleting it."
        )
        stated = _word_or_digit(match.group(1))
        assert stated == real, (
            f"{doc_path.name} says {match.group(1)!r} verification-oracle forms, but AGENTS.md "
            f"enumerates {real} (`**Form 1**`..`**Form {real}**`). Adding a form is a two-file "
            "edit -- update both prose counts, and derive the number from the headings."
        )


def test_stated_library_skill_count_matches_the_folders_it_names() -> None:
    real = len(_library_skill_folders())
    for doc_path in (AGENTS_DOC_PATH, CLAUDE_DOC_PATH):
        text = doc_path.read_text(encoding="utf-8")
        match = _STATED_COUNT_RE.search(text)
        # An absent count must fail rather than silently skip -- otherwise deleting the number is
        # the cheapest way to green this gate.
        assert match is not None, (
            f"{doc_path.name} no longer states a `**N skills**` count beside its skill index. "
            "The count is part of the index contract; restore it rather than removing it."
        )
        stated = int(match.group(1))
        assert stated == real, (
            f"{doc_path.name} says `**{stated} skills**` but `.claude/skills/` holds {real} "
            "folders matching the sentence's own definition (`tensor-grep-*` plus "
            "`code-search-and-retrieval-reference`; the bare `tensor-grep` usage skill is listed "
            "separately and deliberately not counted)."
        )
