"""Registration-completeness detector — catches the "added X but missed registration site N" bug.

A universal silent-failure class: an entity (a CLI flag, a route, a command) must be registered in
N places, one is missed, and it fails *quietly*. This is what shipped tg's v1.15.0 `--rank` crash
(the flag was in the native allow-list but not `bootstrap._TG_ONLY_SEARCH_FLAGS`) and what a downstream
billing app hit (a `/v1` route missing its cron registration). `tg callers` can't catch this class —
set/list/decorator registrations are invisible to the call graph (see the `tensor-grep-code-audit`
skill, P7) — so this does a membership set-diff across the declared sites instead.

You declare "registration groups" (the N sites that must stay in sync) in a small TOML config (JSON
also accepted); this extracts each site's string-literal members and reports any entity present in
some-but-not-all sites.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_OPEN_TO_CLOSE = {"{": "}", "[": "]", "(": ")"}
_DECL_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _declaration_re(symbol: str) -> re.Pattern[str]:
    """Anchor for `symbol`'s assignment line (cached).

    Matches `SYMBOL ... =` on a single line where the `=` is a real assignment — not a
    comparison (`==`/`!=`/`<=`/`>=`) and not separated from the symbol by a `#` comment — so a
    mention of the symbol in a comment or docstring is never mistaken for the declaration.
    """
    pattern = _DECL_RE_CACHE.get(symbol)
    if pattern is None:
        # Audit HIGH: the old pattern only forbade a `#` BETWEEN the symbol and the `=`, so
        # a `# SYMBOL = ...` comment (or Rust `// SYMBOL = ...`) matched as the declaration
        # and extract_members returned the comment's wrong member set — corrupting the very
        # CI-gating registration tool. Anchor to line-start (re.MULTILINE) and require the
        # line-prefix before the symbol to carry NO comment marker (`#` or `//`), while still
        # allowing real declaration keywords (`const `/`pub `/type annotations), so a Rust
        # `const SYMBOL: &[&str] =` — where the symbol is not the first token — still matches.
        pattern = re.compile(
            rf"^(?:[^\n#/]|/(?!/))*?(?<![\w]){re.escape(symbol)}\b[^\n=#]*(?<![!<>=])=(?!=)",
            re.MULTILINE,
        )
        _DECL_RE_CACHE[symbol] = pattern
    return pattern


def _consume_string(text: str, index: int) -> tuple[str | None, int]:
    """Consume a quoted string starting at `index` (a quote char); return (content, next_index).

    Honors backslash escapes; returns (None, ...) for an unterminated single-line string (a
    newline before the close), so a stray quote cannot run the scanner away.
    """
    quote = text[index]
    parts: list[str] = []
    j = index + 1
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "\\" and j + 1 < n:
            parts.append(text[j + 1])
            j += 2
            continue
        if ch == quote:
            return "".join(parts), j + 1
        if ch == "\n":
            return None, j
        parts.append(ch)
        j += 1
    return None, j


@dataclass(frozen=True)
class RegistrationSite:
    """One place an entity must be registered — a set/list/array named `symbol` in `file`."""

    file: str
    symbol: str


@dataclass(frozen=True)
class RegistrationGroup:
    """A set of sites that must stay in sync.

    When `entities` is non-empty, the check is ENTITY-SCOPED (false-positive-free): each listed entity
    must appear in ALL sites; the sites may otherwise legitimately differ. When `entities` is empty,
    the check falls back to set-EQUALITY (valid only for sites that are genuinely meant to mirror each
    other — it will false-positive on legitimately-asymmetric sites, so prefer declaring `entities`).
    """

    name: str
    sites: tuple[RegistrationSite, ...]
    entities: tuple[str, ...] = ()


def extract_members(file_path: str, symbol: str) -> set[str]:
    """Extract the string-literal members of a set/list/array declaration named `symbol`.

    Handles Python sets/lists (`SYMBOL = {...}` / `[...]`) and Rust arrays (`SYMBOL: &[&str] = &[...]`)
    by jumping past the `=` (so a `&[&str]` type annotation is not mistaken for the value), then
    bracket-matching the value and collecting quoted strings.
    """
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        return set()
    match = _declaration_re(symbol).search(text)
    if match is None:
        return set()
    index = match.end()
    n = len(text)
    # Skip whitespace and Rust reference/array prefixes (`&`) to the value's opening bracket. A
    # scalar value (e.g. `X = 5`) has no opening bracket here -> no members.
    while index < n and text[index] in " \t\r\n&":
        index += 1
    if index >= n or text[index] not in "{[(":
        return set()
    open_ch = text[index]
    close_ch = _OPEN_TO_CLOSE[open_ch]
    members: set[str] = set()
    depth = 0
    # Bracket-match the value while skipping string literals and `#` / `//` comments, so a bracket
    # or quoted string *inside* a literal or comment cannot corrupt the depth count or inject a
    # spurious member (a `#`-commented entry is the realistic false-NEGATIVE vector: it would read
    # as a registered member and mask a genuine registration gap).
    while index < n:
        ch = text[index]
        if ch in "\"'":
            literal, index = _consume_string(text, index)
            if literal is not None:
                members.add(literal)
            continue
        if ch == "#" or (ch == "/" and index + 1 < n and text[index + 1] == "/"):
            while index < n and text[index] != "\n":
                index += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                break
        index += 1
    return members


def check_group(group: RegistrationGroup, *, repo_root: str | Path = ".") -> dict[str, Any]:
    """Diff a group's sites; report entities present in some-but-not-all sites."""
    root = Path(repo_root)
    members_by_site: dict[str, set[str]] = {}
    for site in group.sites:
        path = site.file if Path(site.file).is_absolute() else str(root / site.file)
        members_by_site[f"{site.symbol} @ {site.file}"] = extract_members(path, site.symbol)
    union: set[str] = set().union(*members_by_site.values()) if members_by_site else set()
    missing: list[dict[str, Any]] = []
    for entity in sorted(union):
        present_in = [k for k, members in members_by_site.items() if entity in members]
        missing_from = [k for k, members in members_by_site.items() if entity not in members]
        if missing_from:  # present in some sites but not all
            missing.append({
                "entity": entity,
                "present_in": present_in,
                "missing_from": missing_from,
            })
    return {
        "name": group.name,
        "complete": not missing,
        "site_member_counts": {k: len(v) for k, v in members_by_site.items()},
        "missing": missing,
    }


def check_entity(
    group: RegistrationGroup, entity: str, *, repo_root: str | Path = "."
) -> dict[str, Any]:
    """Verify a SPECIFIC entity (e.g. a flag you just added) is registered in ALL of a group's sites.

    This is the false-positive-free check: real registration sites often legitimately differ in
    membership (tg's native-passthrough list has 109 flags; the bootstrap tg-only list has 25 — they
    are NOT meant to be equal). So instead of requiring set-equality, this answers the actual question
    you have after adding something — "is THIS entity wired up everywhere it needs to be?"
    """
    root = Path(repo_root)
    present_in: list[str] = []
    missing_from: list[str] = []
    for site in group.sites:
        path = site.file if Path(site.file).is_absolute() else str(root / site.file)
        label = f"{site.symbol} @ {site.file}"
        (present_in if entity in extract_members(path, site.symbol) else missing_from).append(label)
    return {
        "name": group.name,
        "entity": entity,
        "complete": not missing_from,
        "present_in": present_in,
        "missing_from": missing_from,
    }


def _empty_sites(group: RegistrationGroup, root: Path) -> list[str]:
    out: list[str] = []
    for site in group.sites:
        path = site.file if Path(site.file).is_absolute() else str(root / site.file)
        if not extract_members(path, site.symbol):
            out.append(f"{site.symbol} @ {site.file}")
    return out


def check_group_smart(group: RegistrationGroup, *, repo_root: str | Path = ".") -> dict[str, Any]:
    """Entity-scoped when `group.entities` is set (zero false positives); set-equality otherwise.

    Also surfaces `empty_sites`: a declared symbol that resolves to no members is almost always a typo
    or a renamed symbol — a silent false-negative vector — so it makes the group INCOMPLETE.
    """
    root = Path(repo_root)
    empty = _empty_sites(group, root)
    if group.entities:
        entity_reports = [check_entity(group, e, repo_root=repo_root) for e in group.entities]
        incomplete = [r for r in entity_reports if not r["complete"]]
        return {
            "name": group.name,
            "mode": "entity-scoped",
            "complete": not incomplete and not empty,
            "incomplete_entities": incomplete,
            "empty_sites": empty,
        }
    report = check_group(group, repo_root=repo_root)
    report["mode"] = "set-equality"
    report["empty_sites"] = empty
    report["complete"] = report["complete"] and not empty
    return report


def check_groups(groups: list[RegistrationGroup], *, repo_root: str | Path = ".") -> dict[str, Any]:
    """Run every group (entity-scoped or set-equality per group); aggregate with an overall flag."""
    group_reports = [check_group_smart(g, repo_root=repo_root) for g in groups]
    incomplete = [r for r in group_reports if not r["complete"]]
    return {
        "routing_reason": "registration-check",
        "complete": not incomplete,
        "groups_checked": len(group_reports),
        "incomplete_groups": len(incomplete),
        "groups": group_reports,
    }


def load_config(config_path: str) -> list[RegistrationGroup]:
    """Load registration groups from a TOML config (or legacy JSON, by extension).

    TOML is the canonical/human-authored format (it supports comments and matches tg's pyproject /
    planned tg-workspace.toml); `.json` is still accepted for machine-generated configs. Both parse to
    the identical shape: `{"registration_groups": [{name, entities?, sites: [{file, symbol}]}]}`.
    """
    path = Path(config_path)
    if path.suffix == ".toml":
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    groups: list[RegistrationGroup] = []
    for raw in data.get("registration_groups", []):
        sites = tuple(
            RegistrationSite(file=str(s["file"]), symbol=str(s["symbol"]))
            for s in raw.get("sites", [])
        )
        entities = tuple(str(e) for e in raw.get("entities", []))
        groups.append(RegistrationGroup(name=str(raw["name"]), sites=sites, entities=entities))
    return groups


def check_from_config(config_path: str, *, repo_root: str | Path = ".") -> dict[str, Any]:
    """Convenience: load a config and run all its groups."""
    return check_groups(load_config(config_path), repo_root=repo_root)


def render_report(report: dict[str, Any]) -> str:
    """Human-readable one-line-per-finding rendering of a check report."""
    lines: list[str] = []
    for group in report["groups"]:
        if group["complete"]:
            lines.append(f"OK    {group['name']} ({group.get('mode', '?')})")
            continue
        lines.append(f"FAIL  {group['name']} ({group.get('mode', '?')})")
        for site in group.get("empty_sites", []):
            lines.append(f"        empty/missing symbol (typo or renamed?): {site}")
        for entity in group.get("incomplete_entities", []):
            lines.append(
                f"        {entity['entity']} missing from: {', '.join(entity['missing_from'])}"
            )
        for miss in group.get("missing", []):
            lines.append(
                f"        {miss['entity']} missing from: {', '.join(miss['missing_from'])}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: `python -m tensor_grep.core.registration_check CONFIG` → exit 1 on incompleteness."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="registration-check",
        description="Verify multi-site registration completeness (catches 'added X, missed site N').",
    )
    parser.add_argument("config", help="Path to the registration config (TOML, or legacy JSON).")
    parser.add_argument(
        "--repo-root", default=".", help="Root the site file paths are relative to."
    )
    parser.add_argument("--json", action="store_true", help="Emit the full report as JSON.")
    args = parser.parse_args(argv)

    report = check_from_config(args.config, repo_root=args.repo_root)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_report(report) or "OK    (no groups configured)")
        done = report["groups_checked"] - report["incomplete_groups"]
        print(f"\n{done}/{report['groups_checked']} groups complete")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
