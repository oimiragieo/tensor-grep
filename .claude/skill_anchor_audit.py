#!/usr/bin/env python3
"""Mechanically re-derive every ``file:line`` citation in the skill library.

WHY THIS EXISTS (read before "improving" it).

The skill library cites source anchors as ``repo_map.py:99`` / ``main.py:14763`` so a reader can
jump straight to the code a claim rests on. Those anchors go stale every time the cited file grows,
and `src/tensor_grep/cli/repo_map.py` and `main.py` grow constantly -- `repo_map.py` passed 19,000
lines and `main.py` 17,000. Five consecutive maintenance passes (2026-07-02, -07-14, -07-16,
-07-22, -07-27) re-stamped these numbers BY HAND. Every one of those passes shipped anchors that
were already wrong, and the 2026-07-27 audit's own proposed corrections were themselves stale --
they had been computed against a worktree 28 commits behind `origin/main`.

That is the tell for a class, not an instance: when round N+1 keeps finding new members of the
same defect family, the fix is a MODEL that finds them mechanically, not another careful reviewer.
Sibling precedent in this repo: `.claude/rg_argv_differential_fuzz.py`.

WHAT IT CHECKS, and what each tier is worth:

* ``OUT_OF_RANGE`` -- the cited line number exceeds the file's length. Zero false positives: the
  citation is definitively stale. This is the tier to gate on.
* ``FILE_MISSING`` -- the cited path resolves to nothing in the repo. Usually a moved file.
* ``SYMBOL_MOVED`` -- the citation names a backticked symbol, that symbol is DEFINED in the
  cited file, and no definition sits within ``--slack`` lines of the cited number. Reports the
  real definition lines, so the fix is a copy-paste. Heuristic, so it is a warning, not a gate.
  Symbols with no definition in the file are skipped rather than reported: a mention in prose
  makes no positional claim, and treating it as one buried the real findings under ~100 false
  ones when this tool was first run.

DELIBERATELY NOT A TEST. A pytest that pinned these numbers would fail on every unrelated PR that
adds a line to `main.py`, which is most of them -- the gate would be turned off within a week, and
a disabled gate is worse than none. This is a maintenance command you run during a skills pass.

Usage:
    python .claude/skill_anchor_audit.py                 # human-readable report
    python .claude/skill_anchor_audit.py --json          # machine-readable
    python .claude/skill_anchor_audit.py --tier OUT_OF_RANGE   # only the zero-false-positive tier

Exit codes: 0 = nothing in the requested tiers, 1 = findings reported, 2 = usage/IO error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A citation looks like `repo_map.py:99`, `src/tensor_grep/cli/main.py:11298`, or
# `docs/CONTRACTS.md:156-158`. Require a real source/doc suffix so prose like "3:1" or a version
# string never matches -- a checker that reports noise gets ignored, which is the failure mode
# this whole file exists to avoid.
_CITATION = re.compile(
    r"(?P<path>[A-Za-z0-9_./-]+\.(?:py|rs|md|toml|ya?ml|json)):(?P<line>\d+)(?:-(?P<end>\d+))?"
)

# A backticked identifier appearing shortly before the citation, e.g.
# ``_emit_symbol_command_result` (`main.py:11298`)``. Captures the LAST one before the cite.
#
# The trailing `` `? `` is load-bearing and was found by the control arm, not by review: the
# citation is itself usually inside a code span, so the text before it ends with the citation's
# OPENING backtick. Without allowing that one trailing backtick, `[^`]{0,80}$` can never match the
# dominant `` (`symbol`, `file.py:123`) `` form -- the SYMBOL_MOVED tier looked healthy while being
# structurally incapable of firing on the real corpus. A tier that cannot fire is not a lenient
# check, it is a decoration.
_SYMBOL_BEFORE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`[^`]{0,80}`?$")

_SKIP_PATH_PARTS = ("node_modules", ".git", "__pycache__")


def _repo_root(start: Path) -> Path:
    """Walk up for the repo marker rather than assuming CWD.

    The `__file__`-marker walk, not `Path.cwd()`: this script is run from the repo root, from
    `.claude/`, and from a git worktree whose path shares no prefix with the main checkout.
    """
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / ".claude").is_dir():
            return candidate
    raise SystemExit("could not locate repo root (no pyproject.toml + .claude/ ancestor)")


def _build_path_index(root: Path) -> dict[str, list[Path]]:
    """Map every repo-relative path AND bare basename to the real files that match.

    Skills cite inconsistently -- `repo_map.py:99`, `cli/repo_map.py:99`, and the full
    `src/tensor_grep/cli/repo_map.py:99` all appear. Indexing by every path SUFFIX lets all three
    resolve, and an ambiguous basename (two files with the same name) is reported rather than
    guessed, because guessing is how a checker starts lying.
    """
    index: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_PATH_PARTS for part in path.parts):
            continue
        rel = path.relative_to(root)
        parts = rel.parts
        for depth in range(1, len(parts) + 1):
            key = "/".join(parts[len(parts) - depth :])
            index.setdefault(key, []).append(path)
    return index


def _definition_lines(lines: list[str], symbol: str) -> list[int]:
    """1-indexed lines where ``symbol`` is DEFINED (not merely mentioned).

    Covers the shapes the cited files actually use: Python ``def``/``class``, Rust
    ``fn``/``struct``/``enum``/``trait``/``const``/``static``/``type``, and a module- or
    workflow-level binding (``NAME = ...`` / ``NAME: ...``) which is how the constants and the
    ci.yml env keys in these skills are cited.
    """
    escaped = re.escape(symbol)
    keyword = re.compile(
        rf"\b(?:def|class|fn|struct|enum|trait|impl|const|static|type)\s+{escaped}\b"
    )
    binding = re.compile(rf"^\s*{escaped}\s*[:=]")
    return [i + 1 for i, line in enumerate(lines) if keyword.search(line) or binding.match(line)]


def _line_count(path: Path) -> int:
    # Binary read + split: `read_text()` collapses CRLF on Windows and would under-report a
    # byte/line budget. Same trap that under-reported a doc's size earlier in this campaign.
    return len(path.read_bytes().split(b"\n"))


def audit(root: Path, slack: int) -> list[dict[str, object]]:
    index = _build_path_index(root)
    findings: list[dict[str, object]] = []
    skills_dir = root / ".claude" / "skills"
    if not skills_dir.is_dir():
        raise SystemExit(f"no skills directory at {skills_dir}")

    line_cache: dict[Path, list[str]] = {}

    for skill_md in sorted(skills_dir.rglob("*.md")):
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            for match in _CITATION.finditer(raw):
                cited_path = match.group("path")
                cited_line = int(match.group("line"))
                candidates = index.get(cited_path, [])

                base = {
                    "skill": str(skill_md.relative_to(root)).replace("\\", "/"),
                    "skill_line": lineno,
                    "citation": f"{cited_path}:{cited_line}",
                }

                if not candidates:
                    findings.append({
                        **base,
                        "tier": "FILE_MISSING",
                        "detail": "no such file in repo",
                    })
                    continue
                if len(candidates) > 1:
                    # Ambiguous -- report rather than pick. Silent disambiguation would let a
                    # citation "pass" against a file the author never meant.
                    findings.append({
                        **base,
                        "tier": "AMBIGUOUS_PATH",
                        "detail": f"{len(candidates)} files match this suffix",
                    })
                    continue

                target = candidates[0]
                total = _line_count(target)
                if cited_line > total:
                    findings.append({
                        **base,
                        "tier": "OUT_OF_RANGE",
                        "detail": f"file has {total} lines",
                    })
                    continue

                symbol_match = _SYMBOL_BEFORE.search(raw[: match.start()])
                if not symbol_match:
                    continue
                symbol = symbol_match.group(1)
                if target not in line_cache:
                    line_cache[target] = target.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                lines = line_cache[target]

                # DEFINITION sites only -- never bare occurrences. The first version of this check
                # matched the symbol anywhere in the file, which made `tg`, `find`, `list`, `file`
                # and `None` "move" constantly: they appear on hundreds of lines, so the nearest
                # occurrence to any cited line is meaningless. 114 findings, nearly all noise, and a
                # tool that cries wolf gets switched off -- the same disabled-gate failure this
                # file's header warns about. Anchoring to where the symbol is DEFINED makes the
                # signal specific: if the definition is not at the cited line, the citation is
                # genuinely stale; if the symbol has no definition here it is prose, and prose
                # carries no positional claim to check.
                defs = _definition_lines(lines, symbol)
                if not defs:
                    continue

                if any(abs(d - cited_line) <= slack for d in defs):
                    continue

                findings.append({
                    **base,
                    "tier": "SYMBOL_MOVED",
                    "symbol": symbol,
                    "detail": f"defined at {defs[:5]}",
                })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    parser.add_argument(
        "--tier",
        action="append",
        default=None,
        help="only report these tiers (repeatable); default reports all",
    )
    parser.add_argument(
        "--slack",
        type=int,
        default=4,
        help="lines of tolerance when matching a symbol to its cited line (default 4)",
    )
    args = parser.parse_args(argv)

    root = _repo_root(Path(__file__).resolve().parent)
    findings = audit(root, args.slack)
    if args.tier:
        wanted = set(args.tier)
        findings = [f for f in findings if f["tier"] in wanted]

    if args.json:
        print(json.dumps({"root": str(root), "findings": findings}, indent=2))
        return 1 if findings else 0

    if not findings:
        print("skill anchor audit: no findings in the requested tiers")
        return 0

    by_tier: dict[str, int] = {}
    for finding in findings:
        by_tier[str(finding["tier"])] = by_tier.get(str(finding["tier"]), 0) + 1
    order = ["OUT_OF_RANGE", "FILE_MISSING", "AMBIGUOUS_PATH", "SYMBOL_MOVED"]
    for tier in order:
        rows = [f for f in findings if f["tier"] == tier]
        if not rows:
            continue
        print(f"\n=== {tier} ({len(rows)}) ===")
        for finding in rows:
            symbol = f" [{finding['symbol']}]" if "symbol" in finding else ""
            print(
                f"  {finding['skill']}:{finding['skill_line']}"
                f"  {finding['citation']}{symbol} -- {finding['detail']}"
            )
    print("\nsummary: " + ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))
    return 1


if __name__ == "__main__":
    sys.exit(main())
