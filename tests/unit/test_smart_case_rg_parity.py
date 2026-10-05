# ruff: noqa: RUF001  (case-folding fixtures are deliberately ambiguous characters)
"""K1 audit repros pinned against rg on top of the shared resolver (`core.case_semantics`).

Smart case is exact only for patterns whose uppercase characters are all literals; for a regex with
escapes, classes, inline groups (so also verbose-mode comments, group names, `\\S`, `\\p{Lu}`) the
resolver REFUSES to guess and the pipeline hands the search to rg.

Every test that needs rg has two arms, chosen with the SAME resolver production uses
(`resolve_ripgrep_binary`): with rg the answer is compared to rg exactly; without rg (CI lanes that
do not install it) a non-ASCII case-insensitive search must FAIL CLOSED with `BackendExecutionError`
-- that refusal is the contract on an rg-less machine, so it is asserted, never skipped.
"""

from __future__ import annotations

import subprocess

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.case_semantics import effective_ignore_case, smart_case_is_exact
from tensor_grep.core.config import SearchConfig

_RG_MISSING_MESSAGE = "rg's Unicode case folding"

_PROBE_LINES = [
    "foo", "FOO", "Foo", "xfoo", "XFOO", "ax", "Ax", "AX", "ax1", "ǆABC", "ǅabc",
    "\\Foo", "a]x", "A]x", "Ab", "ab", "fooA", "foo#A", "foo # A", "bar", "BAR", "fooBar",
    "FOOBAR", "foobar", "foo A", "fo o", "A", "a",
]  # fmt: skip


def _rg_binary() -> str | None:
    path = resolve_ripgrep_binary()
    return str(path) if path else None


def _rg_count(tmp_path, args, pattern, lines):
    """Match count from the real rg (call only on the with-rg arm)."""
    rg = _rg_binary()
    assert rg is not None, "caller must check rg availability first"
    f = tmp_path / "probe.txt"
    f.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    proc = subprocess.run(
        [rg, *args, "-c", "-e", pattern, "--", str(f)], capture_output=True, text=True
    )
    if proc.returncode == 2:
        pytest.skip(f"rg rejects pattern {pattern!r}")
    return int(proc.stdout.strip() or 0)


def _assert_fails_closed(search):
    """The rg-less arm: the documented refusal, with the documented message."""
    with pytest.raises(BackendExecutionError, match=_RG_MISSING_MESSAGE):
        search()


# Regex patterns from the audit rounds (escapes, classes, verbose-mode comments, group names, ...).
_REGEX_PATTERNS = [
    "foo", "Foo", r"\Sfoo", r"\p{Lu}x", r"\pLx", r"\Ax", "[[:upper:]]x", "[A-Z]x", "[^A]x",
    r"[\]A]x", r"\x41x", r"[\x41]x", r"\x{1C5}abc", "(?P<Name>foo)", "(?U)foo", r"\bFoo",
    r"\d*Foo", r"\\Foo", "(?x)foo # A\n", "(?x)foo \\# A", "(?x:foo # A\n)bar",
    "(?x:foo # A\n)Bar", "(?-x)foo # A", "(?x)(?-x)foo # A", "(?x)(?:foo)# A\n",
    "(?:(?x)foo)# A", "(?x)(?P<N>foo) # A\n", "(?x)fo[o#A\n]", "fo[o#A]", "(?x) A",
]  # fmt: skip


@pytest.mark.parametrize("pattern", _REGEX_PATTERNS)
def test_resolver_is_exact_or_defers_to_rg(tmp_path, pattern):
    config = SearchConfig(smart_case=True)
    if not smart_case_is_exact(config, pattern):
        # needs no rg: the resolver refuses to guess; the pipeline routes the search to rg
        with pytest.raises(BackendExecutionError):
            effective_ignore_case(config, pattern)
        return
    if _rg_binary() is None:
        # exact plain literal: the Python answer needs no rg to be well-defined
        assert effective_ignore_case(config, pattern) is (not any(c.isupper() for c in pattern))
        return
    sensitive = _rg_count(tmp_path, [], pattern, _PROBE_LINES)
    insensitive = _rg_count(tmp_path, ["-i"], pattern, _PROBE_LINES)
    smart = _rg_count(tmp_path, ["-S"], pattern, _PROBE_LINES)
    # Python decides: it must agree with rg whenever the probe can tell the modes apart.
    if sensitive != insensitive:
        assert effective_ignore_case(config, pattern) == (smart == insensitive)


@pytest.mark.parametrize(
    "pattern",
    [p for p in _REGEX_PATTERNS if any(m in p for m in ("\\", "[", "(?"))],
)
def test_every_escape_class_or_group_pattern_is_deferred_to_rg(pattern):
    # Includes each verbose-mode case: none of them is ever decided by a Python guess.
    assert not smart_case_is_exact(SearchConfig(smart_case=True), pattern)


@pytest.mark.parametrize("index", ["1", "0"])
def test_stringzilla_titlecase_smart_case_matches_rg(tmp_path, monkeypatch, index):
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", index)
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(tmp_path / "idx"))
    f = tmp_path / "t.txt"
    f.write_bytes("ǆABC\n".encode())
    cfg = SearchConfig(fixed_strings=True, smart_case=True)
    if _rg_binary() is None:
        _assert_fails_closed(lambda: StringZillaBackend().search(str(f), "ǅabc", cfg))
        return
    got = StringZillaBackend().search(str(f), "ǅabc", cfg).total_matches
    assert got == 1 == _rg_count(tmp_path, ["-F", "-S"], "ǅabc", ["ǆABC"])
    # second pattern: also all-lowercase, so smart-case stays insensitive on both sides
    lower = StringZillaBackend().search(str(f), "ǆabc", cfg).total_matches
    assert lower == _rg_count(tmp_path, ["-F", "-S"], "ǆabc", ["ǆABC"])


@pytest.mark.parametrize("fixed", [True, False])
def test_cpu_backend_titlecase_smart_case_matches_rg(tmp_path, fixed):
    f = tmp_path / "t.txt"
    f.write_bytes("ǆABC\n".encode())
    cfg = SearchConfig(fixed_strings=fixed, smart_case=True)
    if _rg_binary() is None:
        _assert_fails_closed(lambda: CPUBackend().search(str(f), "ǅabc", cfg))
        return
    got = CPUBackend().search(str(f), "ǅabc", cfg).total_matches
    flags = ["-F", "-S"] if fixed else ["-S"]
    assert got == _rg_count(tmp_path, flags, "ǅabc", ["ǆABC"]) == 1


def test_ascii_case_insensitive_search_needs_no_rg(tmp_path, monkeypatch):
    # Control for the fail-closed arm: an all-ASCII search is exact in Python and never refuses.
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", "0")
    f = tmp_path / "a.txt"
    f.write_text("Hello World\nhello\nnope\n", encoding="utf-8")
    cfg = SearchConfig(fixed_strings=True, smart_case=True)
    assert StringZillaBackend().search(str(f), "hello", cfg).total_matches == 2


_UNICODE_FOLD_CASES = [
    ("ς", "σ"),  # final sigma vs sigma
    ("ſ", "s"),  # long s
    ("K", "k"),  # Kelvin sign (spelled as an escape so no tool can normalize it to ASCII K)
    ("ı", "i"),  # dotless i: no simple fold to i
    ("İ", "i"),  # dotted capital I: no simple fold to i
    ("ß", "ss"),  # sharp s must not expand
    ("SS", "ß"),
    ("ẞ", "ß"),
]  # fmt: skip


@pytest.mark.parametrize("index", ["1", "0"])
@pytest.mark.parametrize(("text", "pattern"), _UNICODE_FOLD_CASES)
@pytest.mark.parametrize("mode", ["ignore_case", "smart_case"])
def test_stringzilla_unicode_case_folding_matches_rg(
    tmp_path, monkeypatch, index, text, pattern, mode
):
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX", index)
    monkeypatch.setenv("TENSOR_GREP_STRING_INDEX_DIR", str(tmp_path / "idx"))
    f = tmp_path / "u.txt"
    f.write_bytes((text + "\n").encode("utf-8"))
    cfg = SearchConfig(fixed_strings=True, **{mode: True})
    if _rg_binary() is None:
        _assert_fails_closed(lambda: StringZillaBackend().search(str(f), pattern, cfg))
        return
    got = StringZillaBackend().search(str(f), pattern, cfg).total_matches
    flag = "-i" if mode == "ignore_case" else "-S"
    assert got == _rg_count(tmp_path, ["-F", flag], pattern, [text])


def test_fold_cases_keep_their_non_ascii_code_points():
    # a text tool once normalized the Kelvin sign to ASCII "K", silently voiding that case
    texts = {text for text, _pattern in _UNICODE_FOLD_CASES}
    assert {"K", "ς", "ſ", "ı", "İ", "ß", "ẞ"} <= texts
