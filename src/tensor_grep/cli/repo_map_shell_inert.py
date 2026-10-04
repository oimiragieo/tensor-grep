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


def unsafe_neighbour_entry(argv: list[str], relative_test: str) -> dict[str, object]:
    """The neighbour-heuristic suggestion for a path that must not enter a command string."""
    return {
        "argv": argv,
        "command_omitted": MANUAL_QUOTING_NOTE,
        "target_test": relative_test,
        "basis": "test-neighbor-heuristic",
        "verified": False,
    }


def disclose_unsafe_paths(plan: list[dict[str, object]], unsafe_paths: list[str]) -> None:
    """Fail closed: attach the RAW unsafe paths to the first repo-level step as data, never as
    part of any command string."""
    if not unsafe_paths:
        return
    for step in plan:
        if step.get("scope") == "repo":
            step["omitted_unsafe_paths"] = sorted(set(unsafe_paths))
            step["omitted_note"] = MANUAL_QUOTING_NOTE
            return


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
