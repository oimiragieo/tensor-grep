"""The defaulted-scope note must be ONE string across the Python and Rust engines.

Task #26. The v1.101.22 dogfood asked for the stderr-only PATH note to reach the `--json` body.
That request quietly multiplied the number of places the sentence lives from one to three:

    src/tensor_grep/cli/bootstrap.py::_defaulted_scope_note()   Python CLI, stderr + JSON body
    rust_core/src/native_search.rs::DEFAULTED_SCOPE_NOTE        both Rust engines' JSON bodies

A doc comment on each asking the other to stay in sync is NOT synchronisation -- it is the same
"declared rule" rung that this repo has watched fail repeatedly. Only a check that reads both
sources and compares them can fail when they drift, so that is what this file is.

WHY A CROSS-LANGUAGE STRING CHECK AND NOT A SHARED CONSTANT: there is no build-time channel from
Rust to Python here. `main.rs` is a separate binary crate, the Python package ships without the
Rust source, and generating one from the other would add a codegen step to a one-line string. The
cheap, honest mechanism is to read the `.rs` file at test time and compare.

WHAT WOULD MAKE THIS INERT (checked below, because an unarmed gate is worse than none): the regex
failing to find the Rust constant at all. A `None` match that silently skipped the comparison
would let the two drift freely while this file reported green, so a missing constant is an
explicit FAILURE, not a skip.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_NATIVE_SEARCH_RS = _REPO / "rust_core" / "src" / "native_search.rs"

# `pub const DEFAULTED_SCOPE_NOTE: &str = "... \` + continuation lines + `";`
_RUST_CONST = re.compile(
    r'pub const DEFAULTED_SCOPE_NOTE:\s*&str\s*=\s*"(?P<body>.*?)"\s*;',
    re.DOTALL,
)


def _rust_note() -> str:
    """The Rust constant's VALUE, with Rust's line-continuation escapes resolved.

    A `\\` at end of line inside a Rust string literal swallows the newline AND the leading
    whitespace of the next line -- which is exactly how the constant is written, so a naive read
    of the raw source would compare against a string full of newlines and indentation that the
    compiler never produces. Resolving it here is what makes the comparison test the VALUE rather
    than the formatting.
    """
    source = _NATIVE_SEARCH_RS.read_text(encoding="utf-8")
    match = _RUST_CONST.search(source)
    assert match is not None, (
        f"DEFAULTED_SCOPE_NOTE not found in {_NATIVE_SEARCH_RS}. This check is now INERT: it "
        "cannot detect drift because it cannot find one of the two things it compares. Either "
        "the constant was renamed/moved (update the regex) or it was deleted (the Rust engines "
        "have silently lost the disclosure)."
    )
    return re.sub(r"\\\n\s*", "", match.group("body"))


def _python_note() -> str:
    from tensor_grep.cli.bootstrap import _defaulted_scope_note

    return _defaulted_scope_note()


def test_the_two_engines_emit_the_identical_note() -> None:
    """THE PIN. Byte-equal, not merely similar."""
    assert _rust_note() == _python_note(), (
        "the Python and Rust defaulted-scope notes have drifted. An agent parsing `--json` would "
        "see a different `scope_note` string depending on which engine served its query -- which "
        "is the two-front-doors-disagree failure the single-source extraction was meant to close."
    )


def test_the_note_carries_no_trailing_newline() -> None:
    """CONTROL ARM on the shape, and the specific drift that already happened once.

    The Python note originally ended in `\\n` (fine while stderr was its only consumer). Once the
    same string became a JSON string VALUE, that newline was a real divergence from the Rust side
    -- and an invisible one, because both notes still 'looked' identical in any test that used a
    substring assertion. Line termination belongs to the stream writer, not the text.

    Without this arm, re-adding `\\n` to BOTH sources would keep the equality test above green
    while putting a stray newline inside every JSON payload.
    """
    for label, note in (("python", _python_note()), ("rust", _rust_note())):
        assert not note.endswith("\n"), (
            f"the {label} note ends with a newline. It is a JSON string VALUE on both engines; "
            "the stderr writer appends its own terminator."
        )


def test_the_stderr_writer_still_terminates_the_line() -> None:
    """CONTROL ARM: stripping the newline from the TEXT must not strip it from the OUTPUT.

    This is the arm that would have caught over-correcting. `_write_defaulted_scope_note` appends
    the terminator itself; if someone later 'simplifies' that away to match the constant, the note
    would run into whatever the shell prints next.
    """
    import io
    import sys

    from tensor_grep.cli.bootstrap import _write_defaulted_scope_note

    captured = io.StringIO()
    original = sys.stderr
    sys.stderr = captured
    try:
        _write_defaulted_scope_note()
    finally:
        sys.stderr = original

    written = captured.getvalue()
    assert written.endswith("\n"), "the stderr note is no longer newline-terminated"
    assert _python_note() in written, "the stderr writer no longer emits the shared note text"


@pytest.mark.parametrize(
    "phrase",
    [
        "no PATH was given",
        "not in the repository",
        "explicit PATH",
    ],
)
def test_the_note_states_the_consequence_not_just_the_fact(phrase: str) -> None:
    """The note has to survive as a SENTENCE, not just as a matching pair of strings.

    Two engines agreeing on an empty string would pass every test above. These phrases are the
    load-bearing content the dogfood asked for: what happened (scope defaulted), what it means
    (zero here is not zero everywhere), and what to do (pass a PATH).
    """
    assert phrase in _python_note()
    assert phrase in _rust_note()
