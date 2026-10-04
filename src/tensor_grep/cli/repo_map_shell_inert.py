"""Shell-inertness checks for generated validation commands (split out of repo_map.py).

A generated validation command is pasted into an UNKNOWN shell (bash, PowerShell or cmd), so
quoting for any one of them is not safe: ``"$(x)"`` expands in bash AND PowerShell, ``%VAR%``
expands in cmd even inside quotes, and ``!``/backtick/``\\`` are live in others. The contract is
therefore fail-closed: a path or test filter made only of the conservative characters below is
emitted bare (identical in every shell); anything else is NEVER interpolated into a command
string -- the caller omits the runnable string and discloses the raw value instead.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any

# ASCII letters, digits and `. _ - / + @ = , :` only. Non-ASCII letters are deliberately NOT
# included: their handling depends on the console code page / shell encoding, which is not
# something this module can show to be inert in all three shells.
_INERT_CHARS = frozenset(string.ascii_letters + string.digits + "._-/+@=,:")
# A leading `-` would turn a path into an option; a leading `@` is PowerShell splatting.
_FORBIDDEN_LEADING = ("-", "@")
MANUAL_QUOTING_NOTE = "path requires manual quoting"


def is_shell_inert_path(value: str) -> bool:
    """True when ``value`` can be emitted unquoted and reach every shell byte-for-byte."""
    return (
        bool(value)
        and not value.startswith(_FORBIDDEN_LEADING)
        and all(char in _INERT_CHARS for char in value)
    )


def is_shell_inert_filter(value: str) -> bool:
    """A test-name filter: the inert set plus plain spaces (the caller double-quotes spaces,
    which is inert in every shell when no other metacharacter is present)."""
    return is_shell_inert_path(value.replace(" ", "_"))


class Derived(str):
    """Marks a command token derived from user-controlled input (a path, a derived target, a
    test name). ``render_command`` checks EVERY ``Derived`` token, not just the path they came
    from: ``tests/@audit.rs`` is inert as a path but yields the PowerShell-splatted ``@audit``."""


class DerivedFilter(Derived):
    """A derived test-name filter: like ``Derived`` but plain spaces are allowed (double-quoted)."""


@dataclass(frozen=True)
class Omission:
    """A command that was NOT rendered, with the raw tokens that failed the inert check."""

    tokens: tuple[str, ...]


def render_command(*parts: str) -> str | Omission:
    """THE only way a validation command string is built from derived values.

    Literal parts pass through; every ``Derived`` part must be shell-inert (no leading ``-`` or
    ``@``, only the inert character set). All tokens pass -> the joined command string; any token
    fails -> an ``Omission`` naming the raw tokens, and no command string exists at all.
    """
    rendered: list[str] = []
    rejected: list[str] = []
    for part in parts:
        if isinstance(part, DerivedFilter) and is_shell_inert_filter(part):
            rendered.append(f'"{part}"' if " " in part else str(part))
        elif isinstance(part, Derived) and not isinstance(part, DerivedFilter):
            if is_shell_inert_path(part):
                rendered.append(str(part))
            else:
                rejected.append(str(part))
        elif isinstance(part, Derived):
            rejected.append(str(part))
        else:
            rendered.append(part)
    return Omission(tuple(rejected)) if rejected else " ".join(rendered)


def unsafe_neighbour_entry(argv: list[str], relative_test: str) -> dict[str, Any]:
    """The neighbour-heuristic suggestion for a path that must not enter a command string."""
    return {
        "argv": argv,
        "command_omitted": MANUAL_QUOTING_NOTE,
        "target_test": relative_test,
        "basis": "test-neighbor-heuristic",
        "verified": False,
    }


def omission_step(omitted_tokens: list[str]) -> dict[str, Any]:
    """A plan entry with NO ``command`` carrying the raw omitted tokens. It is kept separate
    from runnable steps: ``split_omissions`` removes it before any consumer sees the plan."""
    return {
        "scope": "omitted",
        "runner": "none",
        "confidence": 0.0,
        "detection": "omitted",
        "omitted_unsafe_paths": sorted(set(omitted_tokens)),
        "omitted_note": MANUAL_QUOTING_NOTE,
    }


def split_omissions(
    plan: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(runnable steps, omission entries)."""
    omitted = [step for step in plan if step.get("scope") == "omitted"]
    return [step for step in plan if step.get("scope") != "omitted"], omitted


def merge_omissions(alignment: dict[str, Any], omitted: list[dict[str, Any]]) -> dict[str, Any]:
    """Disclose omissions on the validation alignment (consumers already carry it)."""
    if not omitted:
        return alignment
    paths = sorted({str(p) for step in omitted for p in step.get("omitted_unsafe_paths", [])})
    merged = dict(alignment)
    merged["omitted_unsafe_paths"] = paths
    merged["omitted_note"] = MANUAL_QUOTING_NOTE
    issues = merged.get("issues")
    merged["issues"] = [
        *(issues if isinstance(issues, list) else []),
        f"{MANUAL_QUOTING_NOTE}: {', '.join(paths)}",
    ]
    return merged


def best_test_function_candidate(
    candidates: list[str],
    *,
    symbol_terms: list[str],
    query_terms: list[str],
) -> str | None:
    """Pick the test name whose terms best match; only shell-inert names may reach a command
    (moved out of repo_map.py unchanged apart from the inert pre-filter)."""
    candidates = [name for name in candidates if is_shell_inert_filter(name)]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    best_name: str | None = None
    best_score = 0
    for candidate in candidates:
        haystack = candidate.lower()
        score = 0
        if symbol_terms:
            if all(term in haystack for term in symbol_terms):
                score += 6
            score += sum(2 for term in symbol_terms if term in haystack)
        if query_terms:
            score += sum(1 for term in query_terms if term in haystack)
        if score > 0 and candidate.startswith("test_"):
            score += 1
        if score > best_score or (
            score == best_score
            and score > 0
            and best_name is not None
            and len(candidate) < len(best_name)
        ):
            best_name = candidate
            best_score = score
    if best_score <= 0:
        return None
    return best_name
