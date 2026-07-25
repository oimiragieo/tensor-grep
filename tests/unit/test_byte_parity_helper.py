"""Bidirectional-oracle proof for `tests/helpers/byte_parity.py` (task #262).

No `tg`/`rg` binary is invoked anywhere in this file -- these are pure unit tests against
synthetic byte strings, which is what makes them runnable without a compiled binary and
what makes the bidirectional gate below meaningful: the ONLY acceptable evidence that the
oracle is fixed is that it REJECTS what the broken (pre-fix) oracle ACCEPTED.

Each divergence class below is proven in both directions:
  1. the NEW byte-exact comparison REJECTS it (the fix works), and
  2. the OLD lossy comparison (kept here, explicitly named, as
     `_legacy_lossy_normalize` / inline `errors="replace"` decode) ACCEPTED it (proving the
     old oracle really was blind -- a passing "new" test alone proves nothing).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.byte_parity import (  # noqa: E402
    assert_bytes_equal,
    decode_for_display,
    split_lines_bytes,
    split_lines_preserve_cr,
)
from helpers.rg_parity import RGParityCorpus, _normalize_line  # noqa: E402


def _legacy_lossy_normalize(text: str) -> str:
    """The PRE-FIX transform this task replaces (mirrors `_normalize` in
    `test_rg_parity_edges.py` and `test_multi_pattern_native.py` before task #262):
    ``text.replace("\\r\\n", "\\n")``. Kept here, explicitly named, ONLY to demonstrate the
    contrast with the fixed oracle below -- no production test imports this function.
    """
    return text.replace("\r\n", "\n")


# --- 1. CRLF vs LF: the fixed oracle must REJECT, the legacy one ACCEPTED. -----------------


def test_new_comparison_rejects_crlf_vs_lf_only_difference() -> None:
    crlf = b"match one\r\nmatch two\r\n"
    lf_only = b"match one\nmatch two\n"

    with pytest.raises(AssertionError):
        assert_bytes_equal(crlf, lf_only)


def test_legacy_lossy_normalize_would_have_accepted_the_same_crlf_pair() -> None:
    # Proves the FAILING direction demanded by the bidirectional gate: the pre-fix oracle
    # (a blanket \r\n -> \n replace applied identically to both arms, as
    # `test_rg_parity_edges.py::_normalize` used to do) could not see this divergence at
    # all -- it reported these two byte strings as equal.
    crlf_text = "match one\r\nmatch two\r\n"
    lf_text = "match one\nmatch two\n"

    assert _legacy_lossy_normalize(crlf_text) == _legacy_lossy_normalize(lf_text)


# --- 2. Raw invalid UTF-8 vs an already-mangled U+FFFD: fixed REJECTS, legacy ACCEPTED. ----


def test_new_comparison_rejects_raw_invalid_utf8_vs_replacement_char() -> None:
    raw_invalid_byte = b"needle \xff tail\n"
    already_mangled = "needle � tail\n".encode()

    with pytest.raises(AssertionError):
        assert_bytes_equal(raw_invalid_byte, already_mangled)


def test_legacy_errors_replace_would_have_accepted_the_same_encoding_pair() -> None:
    # Mirrors `errors="replace"` at (pre-fix) test_rg_parity_edges.py:64,
    # tests/helpers/rg_parity.py:464, and test_multi_pattern_native.py:84: decoding BOTH
    # arms with errors="replace" maps the raw invalid byte onto the same U+FFFD glyph the
    # "already mangled" string already contains, so the two collapse to an identical
    # decoded string and the raw-byte divergence disappears before comparison.
    raw_invalid_byte = b"needle \xff tail\n"
    already_mangled = "needle � tail\n".encode()

    decoded_invalid = raw_invalid_byte.decode("utf-8", errors="replace")
    decoded_mangled = already_mangled.decode("utf-8", errors="replace")

    assert decoded_invalid == decoded_mangled


# --- 3. Genuinely identical bytes: the fixed oracle must still ACCEPT (not always-fail). --


def test_new_comparison_accepts_genuinely_identical_bytes() -> None:
    payload = b"identical payload\nacross both arms\n"

    assert_bytes_equal(payload, payload)  # must not raise


def test_new_comparison_accepts_identical_bytes_containing_real_crlf_on_both_sides() -> None:
    # A REAL, matching CRLF stream on both arms is still parity -- the fix must not turn
    # every CRLF-emitting tool into a permanent failure, only a genuine cross-arm mismatch.
    payload = b"one\r\ntwo\r\n"

    assert_bytes_equal(payload, payload)


# --- split_lines_bytes: must not silently borrow str.splitlines()'s universal-newline set. -


def test_split_lines_bytes_keeps_trailing_cr_attached_to_its_line() -> None:
    assert split_lines_bytes(b"a\r\nb\n") == [b"a\r", b"b", b""]


def test_split_lines_bytes_does_not_treat_a_bare_cr_as_a_line_break() -> None:
    # bytes.splitlines() WOULD split b"a\rb\n" into [b"a", b"b", b""], eating the \r; the
    # narrow LF-only split here must not.
    assert split_lines_bytes(b"a\rb\n") == [b"a\rb", b""]


# --- split_lines_preserve_cr: the str-typed sibling, used by callers that must decode ------
# before splitting (e.g. to json.loads a line). Same CR-preservation guarantee, but mirrors
# str.splitlines()'s line COUNT for well-formed trailing-newline input.


def test_split_lines_preserve_cr_keeps_trailing_cr_attached_to_its_line() -> None:
    assert split_lines_preserve_cr("a\r\nb\n") == ["a\r", "b"]


def test_split_lines_preserve_cr_matches_splitlines_count_on_lf_only_input() -> None:
    text = "a\nb\nc\n"
    assert split_lines_preserve_cr(text) == text.splitlines()


def test_split_lines_preserve_cr_diverges_from_splitlines_only_on_real_cr() -> None:
    text = "a\r\nb\r\n"
    # str.splitlines() silently eats both \r characters -- exactly the blindness task #262
    # removes. The two must disagree here, or this helper would not be doing its job.
    assert split_lines_preserve_cr(text) != text.splitlines()
    assert split_lines_preserve_cr(text) == ["a\r", "b\r"]
    assert text.splitlines() == ["a", "b"]


def test_split_lines_preserve_cr_empty_string_returns_empty_list() -> None:
    assert split_lines_preserve_cr("") == []


# --- decode_for_display: failure messages must not re-introduce the U+FFFD collapse. ------


def test_decode_for_display_escapes_invalid_bytes_instead_of_replacing_them() -> None:
    rendered = decode_for_display(b"\xff")

    assert rendered == "\\xff"
    assert "�" not in rendered


def test_assert_bytes_equal_failure_message_distinguishes_the_two_divergent_arms() -> None:
    with pytest.raises(AssertionError) as excinfo:
        assert_bytes_equal(b"a\xffb", "a�".encode(), label="demo")

    message = str(excinfo.value)
    assert "demo:" in message
    assert "\\xff" in message


# --- RESIDUAL, PROVEN: rg_parity._normalize_line is STILL whole-line lossy. ----------------
#
# `tests/helpers/rg_parity.py::_normalize_line` carries a 13-line comment (rg_parity.py:560)
# declaring itself a "KNOWN, ACKNOWLEDGED LIMIT (not closed by task #262)": its
# `.replace(b"\\", b"/")` runs against the WHOLE line, not just the leading path prefix,
# because the helper does not parse "path:line:text" apart and cannot do so safely when the
# CONTENT may itself contain ":" or "\\".
#
# That comment states the risk but calls it "a structural gap, not a proven-safe one" and
# notes it "has not been observed to mask a real failure". The tests below convert that from
# an argued claim into a MEASURED one. Nothing here changes production behaviour; the limit
# is deliberately retained (the comparator backs every `test_rg_parity_matrix.py` row, and an
# unproven path-prefix parse is the riskier change). This is the characterization test that
# was missing.
#
# If someone later narrows the replacement to the path prefix, the FIRST test below will
# start failing. That is the intended signal, not a regression: delete it, and update
# rg_parity.py:560's comment to say the limit is closed.


def _sentinel_parity_corpus() -> RGParityCorpus:
    # A root that cannot appear in any line below, so the root-substitution arms are inert
    # and the ONLY transform under test is the whole-line `\` -> `/` replacement.
    return RGParityCorpus(
        root=Path("Z:/tg-parity-sentinel-root-that-does-not-exist"),
        locations={},
        follow_supported=False,
    )


def test_normalize_line_masks_a_backslash_divergence_inside_matched_content() -> None:
    # Two engines emitting GENUINELY different match CONTENT -- one a Windows-style path
    # literal inside the matched text, one a POSIX-style literal. Same file, same line
    # number; the divergence is entirely in the payload, which is contractual output.
    tg_arm = rb'src/cfg.py:12:default = "C:\Users\app\data"'
    rg_arm = rb'src/cfg.py:12:default = "C:/Users/app/data"'

    assert tg_arm != rg_arm, "precondition: the two arms must genuinely differ"

    corpus = _sentinel_parity_corpus()
    assert _normalize_line(tg_arm, corpus=corpus) == _normalize_line(rg_arm, corpus=corpus), (
        "PROVEN LOSSY: _normalize_line collapses a real backslash-vs-forward-slash "
        "divergence inside matched CONTENT into parity. See rg_parity.py:560. If this "
        "assertion starts failing, the limit has been closed -- delete this test and "
        "update that comment."
    )


def test_normalize_line_still_does_its_intended_job_on_the_path_prefix() -> None:
    # The other direction, so the test above cannot be satisfied by a normalizer that has
    # simply stopped working: the separator fold MUST still apply to the path prefix, which
    # is the whole reason the replacement exists.
    corpus = _sentinel_parity_corpus()

    assert _normalize_line(rb"src\pkg\mod.py:3:hit", corpus=corpus) == rb"src/pkg/mod.py:3:hit"


def test_normalize_line_is_byte_exact_on_content_without_separators() -> None:
    # Guards the inert case: a line whose content contains no separator characters must
    # survive completely untouched, so the two tests above are isolating the separator fold
    # and not some broader mangling.
    corpus = _sentinel_parity_corpus()
    line = rb"src/pkg/mod.py:7:alpha beta gamma"

    assert _normalize_line(line, corpus=corpus) == line


def test_a_path_prefix_only_normalizer_would_distinguish_the_same_pair() -> None:
    # THE CONTROL that makes the pin above non-tautological. Asserting "these two become
    # equal" proves nothing on its own -- a normalizer that mangled everything, or one that
    # did nothing to two already-equal inputs, would also satisfy it. This shows the pair is
    # discriminable in principle: a hypothetical normalizer that folds separators ONLY in the
    # path prefix keeps them distinct. So the collapse above is genuinely caused by the
    # whole-line scope, not by the inputs being equivalent.
    #
    # This local function is NOT a proposed fix -- rg_parity.py:560 explains why a naive
    # partition on b":" is unsafe for real output (content may contain its own ":"). It
    # exists only to demonstrate discriminability.
    def _path_prefix_only(line: bytes) -> bytes:
        head, sep, tail = line.partition(b":")
        return head.replace(b"\\", b"/") + sep + tail

    tg_arm = rb'src/cfg.py:12:default = "C:\Users\app\data"'
    rg_arm = rb'src/cfg.py:12:default = "C:/Users/app/data"'

    assert _path_prefix_only(tg_arm) != _path_prefix_only(rg_arm), (
        "the divergent pair must remain distinguishable under a prefix-scoped transform, "
        "otherwise the whole-line test above proves nothing about scope"
    )
