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
