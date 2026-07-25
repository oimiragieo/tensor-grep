"""Shared RAW-BYTE comparison helper for the rg-vs-tg (and launcher-vs-launcher) parity
test suites.

Task #262: the parity suites compared `tg` output against `rg` output (or one launcher
against another) after applying the SAME lossy transformation to BOTH arms -- `text=True`
subprocess captures (Python's universal-newlines mode silently rewrites ``\\r\\n`` ->
``\\n`` in captured stdout on Windows) and/or an explicit ``text.replace("\\r\\n", "\\n")``
normalize step, plus ``errors="replace"`` mapping invalid UTF-8 bytes to U+FFFD on both
sides. A genuine divergence between the two engines cancels out under an identical lossy
transform and reads as parity even though it is not. An independent gate on PR #742 found
three real DATA-level divergences this blindness hid: a lost trailing ``\\r`` on CRLF
input, non-UTF-8 bytes becoming U+FFFD, and a spurious empty-pattern stderr line.

The fix: capture subprocess output as raw ``bytes`` (no ``text=``/``encoding=``/``errors=``
on the ``subprocess.run`` call) and compare ``bytes`` to ``bytes``. Decode only when
rendering a human-readable failure message, and even then with ``errors="backslashreplace"``
so an invalid byte stays visibly distinguishable from a real U+FFFD character rather than
collapsing into an identical-looking replacement glyph.

Any normalization that is genuinely warranted (platform path separators, for instance) must
stay NARROW and explicitly named at the call site, with a comment justifying why it cannot
hide a real divergence -- never a blanket newline or encoding flattening applied here.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path


def run_bytes(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    stdin: int | None = None,
    check: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a subprocess and capture RAW bytes -- no decoding, no newline translation.

    Deliberately omits ``text=``/``encoding=``/``errors=``. Passing ``text=True`` (or
    ``encoding=``) switches ``subprocess.run`` into universal-newlines mode, which
    translates ``\\r\\n`` -> ``\\n`` in captured stdout on Windows *before the caller ever
    sees it* -- exactly the blindness this module exists to remove. Callers that need a
    human-readable string must call ``decode_for_display`` explicitly; nothing here decodes
    implicitly.
    """
    if stdin is None:
        stdin = subprocess.DEVNULL if input_bytes is None else None
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input_bytes,
        stdin=stdin,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def decode_for_display(data: bytes) -> str:
    """Decode bytes ONLY for a human-readable assertion message -- never for comparison.

    Uses ``errors="backslashreplace"`` so an invalid byte renders as a visible escape
    (``\\xNN``) instead of silently collapsing to U+FFFD, the same glyph a genuinely valid
    UTF-8 replacement character would produce. Using ``errors="replace"`` here would
    reintroduce, in the failure message alone, the exact masking this helper is meant to
    eliminate from the comparison.
    """
    return data.decode("utf-8", errors="backslashreplace")


def assert_bytes_equal(actual: bytes, expected: bytes, *, label: str = "") -> None:
    """The core byte-exact parity comparator. No implicit normalization of any kind.

    A caller that legitimately needs to ignore a cosmetic difference (platform path
    separators, for instance) must perform a NARROW, explicitly-named, commented
    transformation on both sides *before* calling this -- never bake a blanket newline or
    encoding flattening in here, which would silently re-open the oracle blindness this
    helper exists to close.
    """
    if actual == expected:
        return
    prefix = f"{label}: " if label else ""
    raise AssertionError(
        f"{prefix}byte-level mismatch\n"
        f"--- expected ({len(expected)} bytes) ---\n{decode_for_display(expected)}\n"
        f"--- actual ({len(actual)} bytes) ---\n{decode_for_display(actual)}"
    )


def split_lines_bytes(data: bytes) -> list[bytes]:
    """Split on a bare LF only, leaving any trailing CR attached to its line.

    ``bytes.splitlines()`` treats ``\\r\\n``, a bare ``\\r``, and a bare ``\\n`` (plus
    several Unicode-only separators) as equivalent line breaks -- exactly the blindness
    this module exists to avoid, since it would silently strip a genuine trailing ``\\r``
    from a CRLF-terminated line before a caller ever gets to compare it. Splitting on
    ``b"\\n"`` alone preserves a trailing ``\\r`` as part of the line's content, so a
    CRLF-vs-LF divergence stays visible in the split result.
    """
    return data.split(b"\n")


def split_lines_preserve_cr(text: str) -> list[str]:
    r"""Split on a bare ``\n`` only, leaving any trailing ``\r`` attached to its line.

    A ``str``-typed sibling of ``split_lines_bytes`` for call sites that must decode before
    splitting (e.g. to run the result through ``json.loads`` or an existing string-based
    normalizer). ``str.splitlines()`` has the exact same blindness as
    ``bytes.splitlines()`` -- it treats ``\r\n`` as one line break, silently dropping a real
    ``\r``. Unlike ``split_lines_bytes``, this mirrors ``str.splitlines()``'s line COUNT for
    well-formed input by dropping one spurious trailing empty element produced by a single
    final ``\n`` (``"a\nb\n".split("\n")`` has a trailing ``""`` that ``"a\nb\n".splitlines()``
    does not), so a caller that previously assumed that invariant does not silently gain an
    extra blank entry -- only a genuine embedded/trailing ``\r`` now survives.
    """
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines
