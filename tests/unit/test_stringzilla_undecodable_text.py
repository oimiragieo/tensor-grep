"""A non-UTF-8 TEXT file must not be reported as `skipped_binary` with no disclosure.

The fixture is the whole difficulty: the file must be latin-1 AND contain no NUL byte. The NUL
probe in `_load_searchable_text` runs BEFORE the decode attempt, so any NUL short-circuits and
the decode path is never reached; a valid-UTF-8 fixture never fails to decode. No fixture in the
existing suite is both, which is why CI has never caught this -- the population is empty by
construction.

The two controls are load-bearing. Without CONTROL A a broken harness looks like the defect;
without CONTROL B the `skipped_binary` label looks universally wrong rather than MISapplied.
"""

from __future__ import annotations

import pytest

from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.config import SearchConfig


@pytest.fixture
def corpus(tmp_path):
    ascii_file = tmp_path / "plain.txt"
    ascii_file.write_bytes(b"hello needle world\n")

    latin1_file = tmp_path / "latin1.txt"
    latin1_file.write_bytes(b"caf\xe9 needle here\nsecond line\n")
    assert b"\x00" not in latin1_file.read_bytes()  # the fixture must BITE

    binary_file = tmp_path / "real.bin"
    binary_file.write_bytes(b"\x00\x01 needle \x00\xff")
    assert b"\x00" in binary_file.read_bytes()

    return ascii_file, latin1_file, binary_file


def _search(path):
    return StringZillaBackend().search(
        str(path), "needle", SearchConfig(fixed_strings=True, query_pattern="needle")
    )


def test_control_a_plain_ascii_is_searched(corpus):
    """If this fails, nothing else in this file is interpretable."""
    result = _search(corpus[0])
    assert result.total_matches == 1


def test_control_b_real_binary_is_correctly_skipped(corpus):
    """`skipped_binary` has a legitimate use; this pins it so the defect reads as a MISlabel."""
    result = _search(corpus[2])
    assert result.total_matches == 0
    assert result.routing_reason == "stringzilla_fixed_strings_skipped_binary"


def test_undecodable_text_is_searched_or_disclosed_fixed_strings(corpus):
    """Caller 1 (stringzilla_backend.py:307-316), reached via `_search_with_index`."""
    result = _search(corpus[1])
    if result.total_matches:
        return  # searched -- acceptable under the contract
    assert result.routing_reason != "stringzilla_fixed_strings_skipped_binary", (
        "undecodable TEXT was labelled binary -- indistinguishable from a genuine binary"
    )
    assert getattr(result, "result_incomplete", False) is True
    assert getattr(result, "incomplete_reason_class", None) == "unreadable_path"


def test_undecodable_text_is_searched_or_disclosed_general_search(corpus):
    """Caller 2 (stringzilla_backend.py:400-410), reached when fixed_strings is FALSE.

    Without this arm the suite goes GREEN after fixing caller 1 alone, while every
    non-fixed-string search still mislabels undecodable text as binary. The two callers are
    separate code paths with the same defect; one arm cannot see both.
    """
    result = StringZillaBackend().search(
        str(corpus[1]),
        "needle",
        SearchConfig(fixed_strings=False, query_pattern="needle"),
    )
    if result.total_matches:
        return
    assert result.routing_reason != "stringzilla_fixed_strings_skipped_binary", (
        "caller 2 still labels undecodable TEXT as binary"
    )
    assert getattr(result, "result_incomplete", False) is True
    assert getattr(result, "incomplete_reason_class", None) == "unreadable_path"
