"""Regression tests for L5 [LOW]: aggregate tg --json match objects now include
a `column` field (1-based column of the match within the line).
"""

import json
import re as _re

from tensor_grep.cli.formatters import json_fmt
from tensor_grep.cli.formatters.json_fmt import JsonFormatter, NdjsonFormatter, _column_for_match
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(*matches: MatchLine) -> SearchResult:
    return SearchResult(
        matches=list(matches),
        matched_file_paths=["f.py"] if matches else [],
        total_files=1 if matches else 0,
        total_matches=len(matches),
    )


def _match_no_range(text: str, line: int = 1, file: str = "f.py") -> MatchLine:
    return MatchLine(line_number=line, text=text, file=file)


def _match_with_range(text: str, col: int, line: int = 1, file: str = "f.py") -> MatchLine:
    """col is 0-based (ast-grep convention)."""
    return MatchLine(
        line_number=line,
        text=text,
        file=file,
        range={
            "start": {"line": line - 1, "column": col},
            "end": {"line": line - 1, "column": col + 5},
        },
    )


# ---------------------------------------------------------------------------
# _column_for_match unit tests
# ---------------------------------------------------------------------------


class TestColumnForMatch:
    def test_range_based_0_to_1_indexed(self):
        match = _match_with_range("  foo bar", col=2)  # 0-based col=2 → 1-based=3
        assert _column_for_match(match) == 3

    def test_range_col_zero_maps_to_one(self):
        match = _match_with_range("foo bar", col=0)
        assert _column_for_match(match) == 1

    def test_no_range_no_config_returns_none(self):
        match = _match_no_range("hello world")
        assert _column_for_match(match) is None

    def test_config_fixed_string_find(self):
        match = _match_no_range("hello world")
        config = SearchConfig(query_pattern="world", fixed_strings=True)
        assert _column_for_match(match, config) == 7  # "world" starts at index 6 → col 7

    def test_config_regex_no_match_returns_none(self):
        match = _match_no_range("no match here")
        config = SearchConfig(query_pattern="xyz")
        assert _column_for_match(match, config) is None

    def test_range_takes_priority_over_config(self):
        # range says col=0 (→1); config would find "foo" at index 4 (→5)
        match = _match_with_range("    foo", col=0)
        config = SearchConfig(query_pattern="foo", fixed_strings=True)
        # range wins: should return 1, not 5
        assert _column_for_match(match, config) == 1

    def test_config_ignore_case(self):
        match = _match_no_range("Hello World")
        config = SearchConfig(query_pattern="hello", ignore_case=True)
        assert _column_for_match(match, config) == 1

    def test_empty_pattern_returns_none(self):
        match = _match_no_range("some text")
        config = SearchConfig(query_pattern="")
        assert _column_for_match(match, config) is None

    def test_regexp_list_used_when_query_pattern_empty(self):
        match = _match_no_range("abc def ghi")
        config = SearchConfig(query_pattern="", regexp=["def"])
        assert _column_for_match(match, config) == 5  # "def" at index 4 → col 5


# ---------------------------------------------------------------------------
# JsonFormatter integration tests
# ---------------------------------------------------------------------------


class TestJsonFormatterColumnField:
    def test_column_present_when_range_available(self):
        match = _match_with_range("  foo", col=2)
        result = _make_result(match)
        output = json.loads(JsonFormatter().format(result))
        m = output["matches"][0]
        assert "column" in m
        assert m["column"] == 3

    def test_column_absent_without_range_and_no_config(self):
        """No range + no config: column must be omitted (not null) to avoid misleading callers."""
        match = _match_no_range("hello world")
        result = _make_result(match)
        output = json.loads(JsonFormatter().format(result))
        m = output["matches"][0]
        assert "column" not in m

    def test_column_present_with_config_pattern(self):
        match = _match_no_range("  error found here")
        config = SearchConfig(query_pattern="error", fixed_strings=True)
        result = _make_result(match)
        output = json.loads(JsonFormatter(config=config).format(result))
        m = output["matches"][0]
        assert "column" in m
        assert m["column"] == 3  # "error" starts at index 2 → col 3

    def test_existing_keys_unchanged(self):
        """Verify that file, line_number, text are still present and unchanged."""
        match = _match_with_range("test line", col=0)
        result = _make_result(match)
        output = json.loads(JsonFormatter().format(result))
        m = output["matches"][0]
        assert m["file"] == "f.py"
        assert m["line_number"] == 1
        assert m["text"] == "test line"

    def test_range_field_still_emitted(self):
        match = _match_with_range("test", col=1)
        result = _make_result(match)
        output = json.loads(JsonFormatter().format(result))
        m = output["matches"][0]
        assert "range" in m

    def test_no_matches_empty_list(self):
        result = _make_result()
        output = json.loads(JsonFormatter().format(result))
        assert output["matches"] == []

    def test_multiple_matches_each_get_column(self):
        m1 = _match_with_range("  alpha", col=2)
        m2 = _match_with_range("beta", col=0)
        result = _make_result(m1, m2)
        output = json.loads(JsonFormatter().format(result))
        assert output["matches"][0]["column"] == 3
        assert output["matches"][1]["column"] == 1


# ---------------------------------------------------------------------------
# NdjsonFormatter must remain unchanged (no column injection)
# ---------------------------------------------------------------------------


class TestNdjsonFormatterUnchanged:
    def test_ndjson_format_no_column_injected(self):
        """NdjsonFormatter does not call _match_payload with config; column only
        appears if range is set (because _match_payload default config=None)."""
        match = _match_no_range("hello world")
        result = _make_result(match)
        rows = NdjsonFormatter().format(result).strip().splitlines()
        assert len(rows) == 1
        row = json.loads(rows[0])
        # Without range and no config: column must not be present
        assert "column" not in row
        # Core fields still present
        assert row["file"] == "f.py"
        assert row["text"] == "hello world"

    def test_ndjson_with_range_emits_column(self):
        match = _match_with_range("  x", col=2)
        result = _make_result(match)
        rows = NdjsonFormatter().format(result).strip().splitlines()
        row = json.loads(rows[0])
        # _match_payload(match, config=None) → range path → column=3
        assert row["column"] == 3


# ---------------------------------------------------------------------------
# Byte-offset column parity (audit MED): ripgrep, --vimgrep and --json all emit
# BYTE columns, not character indices. Non-ASCII bytes before the match must
# advance the reported column by their UTF-8 width, matching real ripgrep.
# ---------------------------------------------------------------------------


class TestColumnByteOffsetParity:
    def test_json_pattern_column_is_byte_offset_for_nonascii(self):
        # "café " is 5 codepoints but 6 UTF-8 bytes (é is 2 bytes); the match 'x' is
        # at char index 5 / byte 6, so the 1-based column must be 7 (rg parity), not 6.
        match = _match_no_range("café x")
        config = SearchConfig(query_pattern="x", fixed_strings=True)
        assert _column_for_match(match, config) == 7

    def test_json_pattern_column_unchanged_for_ascii(self):
        # ASCII: byte offset == char index, so existing behavior is preserved.
        match = _match_no_range("hello world")
        config = SearchConfig(query_pattern="world", fixed_strings=True)
        assert _column_for_match(match, config) == 7

    def test_ripgrep_pattern_column_is_byte_offset_for_nonascii(self):
        from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter

        config = SearchConfig(query_pattern="x", fixed_strings=True)
        fmt = RipgrepFormatter(config=config)
        match = _match_no_range("café x")
        assert fmt._column_for_match(match) == 7


# ---------------------------------------------------------------------------
# Part D.1 (I-01): column comes from rg submatches; the user regex is never run in Python.
# ---------------------------------------------------------------------------


def _forbid_re(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("user pattern must not be evaluated by Python re")

    monkeypatch.setattr(_re, "search", boom)
    monkeypatch.setattr(_re, "compile", boom)


def test_column_prefers_rg_submatch_byte_offset_over_pattern_guess():
    match = MatchLine(
        line_number=1,
        text="foobar foo",
        file="f.py",
        submatches=[{"match": {"text": "foo"}, "start": 7, "end": 10}],
    )
    cfg = SearchConfig(query_pattern="foo", word_regexp=True)
    assert json_fmt._column_for_match(match, cfg) == 8


def test_column_is_byte_offset_for_multibyte_line():
    match = MatchLine(
        line_number=1,
        text="é foo",
        file="f.py",
        submatches=[{"match": {"text": "foo"}, "start": 3, "end": 6}],
    )
    assert json_fmt._column_for_match(match, SearchConfig(query_pattern="foo")) == 4


def test_regex_without_submatches_is_omitted_and_never_evaluated(monkeypatch):
    _forbid_re(monkeypatch)
    match = _match_no_range("a" * 40)
    assert json_fmt._column_for_match(match, SearchConfig(query_pattern="(a+)+b|a$")) is None
    other = _match_no_range("foo: bar baz")
    assert json_fmt._column_for_match(other, SearchConfig(query_pattern=r"\bbar\b")) is None


def test_explicit_case_sensitive_overrides_smart_case_for_column():
    m = _match_no_range("FOO foo")
    cfg = SearchConfig(query_pattern="foo", smart_case=True, case_sensitive=True)
    assert json_fmt._column_for_match(m, cfg) == 5
    fmt = RipgrepFormatter(
        SearchConfig(query_pattern="foo", smart_case=True, case_sensitive=True, column=True)
    )
    assert fmt._column_for_match(m) == 5


def test_non_ascii_case_insensitive_literal_omits_column():
    # final-sigma vs sigma with -i matches at col 1 under Unicode caseless rules; lower().find says 4
    m = _match_no_range("\u03c2 \u03c3")
    cfg = SearchConfig(query_pattern="\u03c3", ignore_case=True)
    assert json_fmt._column_for_match(m, cfg) is None
    fmt = RipgrepFormatter(SearchConfig(query_pattern="\u03c3", ignore_case=True, column=True))
    assert fmt._column_for_match(m) == 1  # the formatter's documented no-column fallback
    ascii_cfg = SearchConfig(query_pattern="foo", ignore_case=True)
    assert json_fmt._column_for_match(_match_no_range("xx FOO"), ascii_cfg) == 4


def test_word_or_line_regexp_without_submatches_omits_column():
    m = _match_no_range("foobar foo")
    word = SearchConfig(query_pattern="foo", word_regexp=True)
    line = SearchConfig(query_pattern="foo", line_regexp=True)
    assert json_fmt._column_for_match(m, word) is None
    assert json_fmt._column_for_match(m, line) is None


def test_literal_pattern_still_gets_byte_column(monkeypatch):
    _forbid_re(monkeypatch)
    cfg = SearchConfig(query_pattern="foo")
    assert json_fmt._column_for_match(_match_no_range("xx foo"), cfg) == 4


def test_ripgrep_formatter_column_does_not_run_user_regex(monkeypatch):
    _forbid_re(monkeypatch)
    fmt = RipgrepFormatter(SearchConfig(query_pattern="(a+)+b|a$", column=True))
    assert fmt._column_for_match(_match_no_range("a" * 40)) == 1


def test_ripgrep_formatter_word_regexp_column_never_guessed(monkeypatch):
    _forbid_re(monkeypatch)
    fmt = RipgrepFormatter(SearchConfig(query_pattern="foo", word_regexp=True, column=True))
    assert fmt._column_for_match(_match_no_range("foobar foo")) == 1
