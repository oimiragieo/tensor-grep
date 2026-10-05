"""Routing must derive non-rg eligibility from the shared flag helper, never from a hand list.

For every flag group `_pattern_semantics_flags` can emit (plus `no_fixed_strings` and a bogus
`--engine`), with `force_cpu` False and True, the pipeline must either route to rg, or produce
the same COUNT rg does, or raise BackendExecutionError -- never a silent mismatch. Smart case on
a regex with escapes/classes is decided by rg alone (it reads uppercase LITERALS only).
"""

import subprocess
from unittest.mock import patch

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli.runtime_paths import resolve_ripgrep_binary
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.pipeline import ConfigurationError, Pipeline

pytestmark = pytest.mark.skipif(resolve_ripgrep_binary() is None, reason="rg not installed")

_ERRORS = (BackendExecutionError, ConfigurationError)
_TEXT = b"foo\nFOO\nfoo bar\nfoobar\n"

_GROUPS = [
    ("ignore_case", {"ignore_case": True}),
    ("case_sensitive", {"case_sensitive": True}),
    ("smart_case", {"smart_case": True}),
    ("ignore_case+case_sensitive", {"ignore_case": True, "case_sensitive": True}),
    ("smart_case+case_sensitive", {"smart_case": True, "case_sensitive": True}),
    ("fixed", {"fixed_strings": True}),
    ("no_fixed", {"no_fixed_strings": True}),
    ("fixed+no_fixed", {"fixed_strings": True, "no_fixed_strings": True}),  # audit repro 1
    ("word", {"word_regexp": True}),
    ("line", {"line_regexp": True}),
    ("invert", {"invert_match": True}),
    ("engine_auto", {"engine": "auto"}),
    ("engine_pcre2", {"engine": "pcre2"}),
    ("engine_bogus", {"engine": "bogus"}),  # audit repro 2
    ("pcre2", {"pcre2": True}),
    ("no_pcre2", {"no_pcre2": True}),
    ("auto_hybrid", {"auto_hybrid_regex": True}),
    ("unicode", {"unicode": True}),
    ("no_unicode", {"no_unicode": True}),
    ("crlf", {"crlf": True}),
    ("null_data", {"null_data": True}),
    ("stop_on_nonmatch", {"stop_on_nonmatch": True}),
    ("multiline", {"multiline": True}),
    ("dfa_size_limit", {"dfa_size_limit": "10M"}),
    ("regex_size_limit", {"regex_size_limit": "10M"}),
    ("max_count", {"max_count": 1}),
]


def _count(call):
    try:
        return call()
    except _ERRORS:
        return "error"


def _reference(path, pattern, kw):
    cfg = SearchConfig(query_pattern=pattern, count=True, **kw)
    return _count(lambda: RipgrepBackend().search(str(path), pattern, cfg).total_matches)


def _actual(path, pattern, kw, force_cpu):
    cfg = SearchConfig(query_pattern=pattern, count=True, **kw)
    return _count(
        lambda: (
            Pipeline(force_cpu=force_cpu, config=cfg)
            .backend.search(str(path), pattern, cfg)
            .total_matches
        )
    )


@pytest.mark.parametrize("force_cpu", [False, True])
@pytest.mark.parametrize("name, kw", _GROUPS, ids=[g[0] for g in _GROUPS])
def test_count_matches_rg_or_fails_closed_for_every_flag_group(tmp_path, name, kw, force_cpu):
    f = tmp_path / "a.txt"
    f.write_bytes(_TEXT)
    reference = _reference(f, "foo", kw)
    actual = _actual(f, "foo", kw, force_cpu)
    assert actual == reference or actual == "error", (name, force_cpu, actual, reference)


@pytest.mark.parametrize("force_cpu", [False, True])
def test_audit_repro_fixed_plus_no_fixed_counts_like_rg(tmp_path, force_cpu):
    # `-F --no-fixed-strings` makes `f.o` a regex; the native count searched the literal (0)
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    kw = {"fixed_strings": True, "no_fixed_strings": True}
    assert _reference(f, "f.o", kw) == 1
    assert _actual(f, "f.o", kw, force_cpu) == 1


@pytest.mark.parametrize("force_cpu", [False, True])
def test_audit_repro_bogus_engine_raises_before_any_backend_runs(tmp_path, force_cpu):
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    cfg = SearchConfig(query_pattern="foo", count=True, engine="bogus")
    with pytest.raises(BackendExecutionError):
        Pipeline(force_cpu=force_cpu, config=cfg)  # at construction, not at search time


@pytest.mark.parametrize("force_cpu", [False, True])
def test_unsupported_flag_without_rg_fails_closed_never_ignored(tmp_path, force_cpu):
    f = tmp_path / "a.txt"
    f.write_bytes(b"foo\n")
    cfg = SearchConfig(query_pattern="f.o", count=True, fixed_strings=True, no_fixed_strings=True)
    with patch.object(RipgrepBackend, "is_available", return_value=False):
        with pytest.raises(BackendExecutionError):
            Pipeline(force_cpu=force_cpu, config=cfg)


def _rg_count(path, pattern, *flags):
    proc = subprocess.run(
        [
            str(resolve_ripgrep_binary()),
            "--no-config",
            "-c",
            *flags,
            "-e",
            pattern,
            "--",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return int(proc.stdout.strip() or 0)


_SMART = [
    (r"foo\S", False),  # rg -S: no uppercase LITERAL => insensitive => matches FOOX
    (r"foo\D", False),
    (r"foo\p{L}", False),
    ("Foo", False),  # real uppercase literal => sensitive => no match on FOOX
    ("foo", True),  # fixed-string control
]


@pytest.mark.parametrize("force_cpu", [False, True])
@pytest.mark.parametrize("pattern, fixed", _SMART)
def test_smart_case_matches_rg_or_fails_closed(tmp_path, pattern, fixed, force_cpu):
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOOX\n")
    expected = _rg_count(f, pattern, "-S", *(["-F"] if fixed else []))
    cfg = SearchConfig(query_pattern=pattern, count=True, smart_case=True, fixed_strings=fixed)
    actual = _count(
        lambda: (
            Pipeline(force_cpu=force_cpu, config=cfg)
            .backend.search(str(f), pattern, cfg)
            .total_matches
        )
    )
    assert actual == expected or actual == "error", (pattern, actual, expected)


@pytest.mark.parametrize("pattern", [r"foo\S", r"foo\D", r"foo\p{L}"])
@pytest.mark.parametrize("force_cpu", [False, True])
def test_smart_case_regex_with_escape_routes_to_rg_and_never_guesses(tmp_path, pattern, force_cpu):
    f = tmp_path / "a.txt"
    f.write_bytes(b"FOOX\n")
    cfg = SearchConfig(query_pattern=pattern, count=True, smart_case=True)
    p = Pipeline(force_cpu=force_cpu, config=cfg)
    assert type(p.backend).__name__ == "RipgrepBackend"
    assert p.backend.search(str(f), pattern, cfg).total_matches == _rg_count(f, pattern, "-S") == 1
    with patch.object(RipgrepBackend, "is_available", return_value=False):
        with pytest.raises(BackendExecutionError):
            Pipeline(force_cpu=force_cpu, config=cfg)


def test_resolver_refuses_to_guess_ambiguous_smart_case():
    from tensor_grep.core.case_semantics import effective_ignore_case, smart_case_is_exact

    cfg = SearchConfig(smart_case=True)
    for pattern in (r"foo\S", r"foo\D", r"foo\p{L}", "[a-z]x", "(?P<Name>x)"):
        assert smart_case_is_exact(cfg, pattern) is False
        with pytest.raises(BackendExecutionError):
            effective_ignore_case(cfg, pattern)
        assert effective_ignore_case(cfg, pattern, strict=False) is False
    assert effective_ignore_case(cfg, "Foo") is False  # real uppercase literal
    assert effective_ignore_case(cfg, "foo") is True
    fixed = SearchConfig(smart_case=True, fixed_strings=True)
    assert effective_ignore_case(fixed, r"foo\s") is True  # exact for a fixed string
    assert effective_ignore_case(fixed, r"foo\S") is False  # the S is a real uppercase literal
