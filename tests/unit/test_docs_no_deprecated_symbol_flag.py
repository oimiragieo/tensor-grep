"""Governance gate: live teaching docs must teach positional SYMBOL, not the deprecated `--symbol`
flag, for the five symbol commands (`defs`, `source`, `refs`, `callers`, `impact`, and the
`blast-radius*` family).

`main._resolve_path_and_symbol` (src/tensor_grep/cli/main.py) prints a deprecation warning and
still accepts `--symbol` for those commands -- the flag is not going away -- but a live doc that
teaches `--symbol` as the example form is teaching a deprecated idiom to every reader (human or
agent) who copies it.

## Scanning model (hardened per Sol R1 review of PR #1174)

A naive per-line, whole-file-keyword scan misses two real shapes and over-trusts a third:

1. **Multiline invocations.** A fenced shell example can backslash-continue a `tg` command across
   lines (`tg impact \\` / `  --symbol X`), and a per-line scan never sees the two halves together.
   This scanner joins backslash-continued lines *inside fenced code blocks* into one logical unit
   before matching, so a split invocation is caught exactly like an unsplit one.
2. **Ledger is a different flag, not a different spelling of the same exemption.** `tg ledger`'s
   `--symbol` is a real, required, non-deprecated flag with no positional form (confirmed via
   `tg ledger claim --help`). The exemption is scoped to an actual `tg ledger ...` invocation
   (the command token immediately following `tg` is parsed, not just the substring "ledger"
   appearing somewhere on the line) -- a prose paragraph that merely *mentions* ledger while also
   showing a `defs`/`impact`/etc. example must not borrow ledger's exemption.
3. **"Deprecated" is not a magic word that launders a bad code example.** The deprecation/
   compatibility exemption applies only to PROSE describing the deprecation -- a line that is
   neither inside a fenced code block nor itself a `tg <command> ...` invocation. A fenced `tg
   impact --symbol X` example is a violation regardless of whether the word "deprecated" appears
   in a comment on the same line; only the *positional* form is a correct example to copy.

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

# A real `tg <command>` invocation: the command token immediately following the `tg` word. Used to
# (a) find genuine `tg ledger ...` invocations for the ledger exemption, and (b) tell an actual
# code example apart from prose that merely names a command in backticks.
_TG_INVOCATION_RE = re.compile(r"\btg\s+([a-z][a-z0-9_-]*)", re.IGNORECASE)

_TARGET_COMMAND_TOKENS = frozenset({"defs", "source", "refs", "callers", "impact"})


def _is_target_command_token(token: str) -> bool:
    token = token.lower()
    return token in _TARGET_COMMAND_TOKENS or token.startswith("blast-radius")


# A PROSE line (not inside a fence, not itself a `tg ...` invocation) is allowed to still mention
# `--symbol` for a target command if it is describing the deprecation/compatibility story. This no
# longer includes a bare "ledger" keyword -- see the ledger exemption below, which is invocation-
# scoped instead of keyword-scoped.
_ALLOWLIST_KEYWORDS = (
    "deprecated",
    "deprecation",
    "backward compat",
    "still accepted",
    "still works",
)


def _is_allowlisted_prose(line: str) -> bool:
    lowered = line.lower()
    return any(keyword in lowered for keyword in _ALLOWLIST_KEYWORDS)


_FENCE_RE = re.compile(r"^\s*```")


def _iter_logical_units(text: str) -> list[tuple[int, str, bool]]:
    """Yield (start_lineno, joined_text, in_fence) units.

    Inside a fenced code block, a line ending in a backslash (shell line-continuation) is joined
    with the following line(s) into one logical unit, so a `tg` invocation split across lines is
    scanned as a single string. Outside fences, and for non-continued fenced lines, each source
    line is its own unit.
    """
    lines = text.split("\n")
    units: list[tuple[int, str, bool]] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        lineno = i + 1
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence and line.rstrip().endswith("\\"):
            start = lineno
            parts = [line.rstrip()[:-1].rstrip()]
            i += 1
            while i < n and lines[i].rstrip().endswith("\\"):
                parts.append(lines[i].rstrip()[:-1].rstrip())
                i += 1
            if i < n:
                parts.append(lines[i].strip())
                i += 1
            units.append((start, " ".join(parts), True))
            continue
        units.append((lineno, line, in_fence))
        i += 1
    return units


_INLINE_CODE_SPAN_RE = re.compile(r"`([^`]*)`")


def _extract_spans(unit_text: str, in_fence: bool) -> list[str]:
    """The independently-judged sub-strings of a unit.

    A fenced code block is judged as ONE span (its whole joined content) -- that is what makes
    the multiline-invocation and deprecated-comment-inside-a-fence cases work. A non-fenced line
    is judged span-by-span, one per inline `` `code` `` pair, so a paragraph that separately shows
    `tg defs` (a bare name mention, no `--symbol` in that same span) and, elsewhere, `tg ledger
    find ... --symbol S` (its own span) does not have the two cross-contaminate: only a span that
    itself pairs a target command with `--symbol` counts as a real bad example.
    """
    if in_fence:
        return [unit_text]
    return _INLINE_CODE_SPAN_RE.findall(unit_text)


def _span_has_target_invocation_with_symbol(span: str) -> bool:
    if not _SYMBOL_FLAG_RE.search(span):
        return False
    return any(_is_target_command_token(m.group(1)) for m in _TG_INVOCATION_RE.finditer(span))


def _unit_has_ledger_invocation(unit_text: str) -> bool:
    return any(m.group(1).lower() == "ledger" for m in _TG_INVOCATION_RE.finditer(unit_text))


def _find_violations_in_text(text: str, label: str) -> list[str]:
    violations: list[str] = []
    for lineno, unit_text, in_fence in _iter_logical_units(text):
        if not _SYMBOL_FLAG_RE.search(unit_text):
            continue
        if not _TARGET_COMMAND_RE.search(unit_text):
            continue

        # Tier A: a genuine `tg <target-command> ... --symbol ...` pairing, judged within one
        # span (an inline code span, or the whole fenced block). Always a violation -- a
        # "deprecated" comment elsewhere in the same fenced block does not make a bad example a
        # good one to copy, and a real invocation is never prose.
        spans = _extract_spans(unit_text, in_fence)
        if any(_span_has_target_invocation_with_symbol(span) for span in spans):
            violations.append(f"{label}:{lineno}: {unit_text.strip()}")
            continue

        # Tier B: a genuine `tg ledger ...` invocation is present somewhere in the unit (the
        # command token immediately after `tg` is actually "ledger" -- not just the substring
        # "ledger" appearing in a sentence). Ledger's own `--symbol` is a different, non-deprecated
        # flag with no positional form. Exempt.
        if _unit_has_ledger_invocation(unit_text):
            continue

        # Tier C: prose. A line is prose only when it is neither inside a fenced code block nor
        # itself any kind of `tg ...` invocation -- that is the only place the deprecation/
        # compatibility keyword exemption is allowed to apply.
        is_prose = not in_fence and not _TG_INVOCATION_RE.search(unit_text)
        if is_prose and _is_allowlisted_prose(unit_text):
            continue

        violations.append(f"{label}:{lineno}: {unit_text.strip()}")
    return violations


def _find_violations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _find_violations_in_text(text, str(path.relative_to(_REPO_ROOT)))


def test_live_docs_teach_positional_symbol_not_deprecated_flag() -> None:
    all_violations: list[str] = []
    for path in _all_scoped_paths():
        all_violations.extend(_find_violations(path))

    assert not all_violations, (
        "Live teaching docs must teach positional SYMBOL (e.g. `tg defs [PATH] SYMBOL`), not "
        "the deprecated `--symbol` flag, for defs/source/refs/callers/impact/blast-radius*. "
        "Prose deprecation/compatibility statements and genuine `tg ledger --symbol` invocations "
        "are exempt (see _ALLOWLIST_KEYWORDS / the ledger-invocation check). Violations:\n"
        + "\n".join(all_violations)
    )


def test_scoped_paths_are_nonempty() -> None:
    # A silently-empty scope would make the gate above vacuously green. Guard against a future
    # refactor (e.g. renaming docs/rebuild-guides) hollowing this test out.
    assert len(_all_scoped_paths()) >= 5


# ---------------------------------------------------------------------------
# Scanner unit tests (synthetic inputs) -- Sol R1: prove the scanner itself catches each of the
# shapes above, independent of whatever the real docs currently say.
# ---------------------------------------------------------------------------


def test_scanner_flags_a_plain_prose_symbol_mention() -> None:
    text = (
        "- `impact --symbol` can be noisier than `blast-radius`; use `blast-radius` for direct "
        "symbol impact.\n"
    )
    violations = _find_violations_in_text(text, "synthetic.md")
    assert len(violations) == 1
    assert "synthetic.md:1" in violations[0]


def test_scanner_flags_a_multiline_backslash_continued_invocation() -> None:
    # The exact shape Sol asked for: a fenced `tg impact` invocation whose `--symbol` flag is on
    # a backslash-continued second line. A naive per-line scanner never sees "impact" and
    # "--symbol" on the same line and would silently pass this.
    text = "```\ntg impact \\\n  --symbol X\n```\n"
    violations = _find_violations_in_text(text, "synthetic.md")
    assert len(violations) == 1
    assert "impact" in violations[0] and "--symbol" in violations[0]


def test_scanner_exempts_a_genuine_ledger_invocation() -> None:
    text = "```\ntg ledger claim REPO --symbol S --agent-id A --json\n```\n"
    violations = _find_violations_in_text(text, "synthetic.md")
    assert violations == []


def test_scanner_exempts_deprecated_prose() -> None:
    # Prose (no fence, no `tg ...` invocation) describing the deprecation is allowed to still say
    # `--symbol`.
    text = (
        "`impact --symbol` is deprecated in favor of positional SYMBOL; it still works for "
        "existing automation.\n"
    )
    violations = _find_violations_in_text(text, "synthetic.md")
    assert violations == []


def test_scanner_still_flags_a_fenced_example_containing_the_word_deprecated() -> None:
    # Sol's key hardening case: the word "deprecated" appearing inside a fenced `tg impact
    # --symbol` example must NOT launder that example. Only the deprecation exemption for PROSE
    # is allowed; a code example is judged on its own (deprecated) syntax.
    text = (
        "```\n# --symbol is deprecated but this example still uses it: tg impact --symbol X\n```\n"
    )
    violations = _find_violations_in_text(text, "synthetic.md")
    assert len(violations) == 1


def test_scanner_ledger_exemption_requires_an_actual_invocation_not_just_the_word() -> None:
    # The word "ledger" appearing in prose that ALSO shows a target-command invocation must not
    # borrow ledger's exemption -- only a real `tg ledger ...` invocation does.
    text = "See the ledger docs, but note `tg impact --symbol X` is the old form.\n"
    violations = _find_violations_in_text(text, "synthetic.md")
    assert len(violations) == 1
