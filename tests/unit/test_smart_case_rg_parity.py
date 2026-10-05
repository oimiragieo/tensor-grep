# ruff: noqa: RUF001  (case-folding fixtures are deliberately ambiguous characters)
"""K1 audit repros pinned against rg on top of the shared resolver (`core.case_semantics`).

Smart case is exact only for patterns whose uppercase characters are all literals; for a regex with
escapes, classes, inline groups (so also verbose-mode comments, group names, `\\S`, `\\p{Lu}`) the
resolver REFUSES to guess and the pipeline hands the search to rg. These tests check both halves
against a live rg: every pattern either is decided by rg, or the Python answer equals rg's.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.cpu_backend import CPUBackend
from tensor_grep.backends.stringzilla_backend import StringZillaBackend
from tensor_grep.core.case_semantics import effective_ignore_case, smart_case_is_exact
from tensor_grep.core.config import SearchConfig

_PROBE_LINES = [
    "foo", "FOO", "Foo", "xfoo", "XFOO", "ax", "Ax", "AX", "ax1", "ǆABC", "ǅabc",
    "\\Foo", "a]x", "A]x", "Ab", "ab", "fooA", "foo#A", "foo # A", "bar", "BAR", "fooBar",
    "FOOBAR", "foobar", "foo A", "fo o", "A", "a",
]  # fmt: skip


def _rg_count(tmp_path, args, pattern, lines):
    rg = shutil.which("rg")
    if rg is None:
        pytest.skip("rg not installed")
    f = tmp_path / "probe.txt"
    f.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    proc = subprocess.run(
        [rg, *args, "-c", "-e", pattern, "--", str(f)], capture_output=True, text=True
    )
    if proc.returncode == 2:
        pytest.skip(f"rg rejects pattern {pattern!r}")
    return int(proc.stdout.strip() or 0)


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
    sensitive = _rg_count(tmp_path, [], pattern, _PROBE_LINES)
    insensitive = _rg_count(tmp_path, ["-i"], pattern, _PROBE_LINES)
    smart = _rg_count(tmp_path, ["-S"], pattern, _PROBE_LINES)
    if smart_case_is_exact(config, pattern):
        # Python decides: it must agree with rg whenever the probe can tell the modes apart.
        if sensitive != insensitive:
            assert effective_ignore_case(config, pattern) == (smart == insensitive)
    else:
        with pytest.raises(BackendExecutionError):  # refuses to guess; the pipeline routes to rg
            effective_ignore_case(config, pattern)


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
    got = StringZillaBackend().search(str(f), "ǅabc", cfg).total_matches
    assert got == 1 == _rg_count(tmp_path, ["-F", "-S"], "ǅabc", ["ǆABC"])
    # control: a real uppercase literal keeps smart-case case-sensitive on both sides
    upper = StringZillaBackend().search(str(f), "ǆabc", cfg).total_matches
    assert upper == _rg_count(tmp_path, ["-F", "-S"], "ǆabc", ["ǆABC"])


@pytest.mark.parametrize("fixed", [True, False])
def test_cpu_backend_titlecase_smart_case_matches_rg(tmp_path, fixed):
    f = tmp_path / "t.txt"
    f.write_bytes("ǆABC\n".encode())
    cfg = SearchConfig(fixed_strings=fixed, smart_case=True)
    got = CPUBackend().search(str(f), "ǅabc", cfg).total_matches
    flags = ["-F", "-S"] if fixed else ["-S"]
    assert got == _rg_count(tmp_path, flags, "ǅabc", ["ǆABC"]) == 1


_UNICODE_FOLD_CASES = [
    ("ς", "σ"),  # final sigma vs sigma
    ("ſ", "s"),  # long s
    ("K", "k"),  # Kelvin sign
    ("ı", "i"),  # dotless i: no simple fold to i
    ("İ", "i"),  # dotted capital I: no simple fold to i
    ("ß", "ss"),  # sharp s must not expand
    ("SS", "ß"),
    ("ẞ", "ß"),
]


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
    got = StringZillaBackend().search(str(f), pattern, cfg).total_matches
    flag = "-i" if mode == "ignore_case" else "-S"
    assert got == _rg_count(tmp_path, ["-F", flag], pattern, [text])
