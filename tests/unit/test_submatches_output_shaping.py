"""PR-A slice 2 (submatches output-shaping): rg supplies per-occurrence byte offsets, but the
parser discarded them, so --vimgrep/--column reported only the FIRST occurrence's column and one
row for a multi-match line. Fix: stash rg's submatches on MatchLine (counting unchanged — still
one-per-matching-line) and emit one output row per occurrence in --vimgrep/--column, at each
occurrence's true byte column. Non-rg backends / context lines (no submatches) are unchanged.
"""

from __future__ import annotations

import json as _json
from types import SimpleNamespace

import tensor_grep.backends.ripgrep_backend as rb
from tensor_grep.backends.ripgrep_backend import RipgrepBackend
from tensor_grep.cli.formatters.ripgrep_fmt import RipgrepFormatter
from tensor_grep.core.config import SearchConfig
from tensor_grep.core.result import MatchLine, SearchResult


def _multi_match_line() -> MatchLine:
    # "foo bar foo baz foo": three occurrences of foo at 0-based byte offsets 0, 8, 16.
    return MatchLine(
        line_number=1,
        text="foo bar foo baz foo",
        file="a.log",
        submatches=(
            {"match": {"text": "foo"}, "start": 0, "end": 3},
            {"match": {"text": "foo"}, "start": 8, "end": 11},
            {"match": {"text": "foo"}, "start": 16, "end": 19},
        ),
    )


def test_backend_stashes_submatches_without_inflating_count(monkeypatch) -> None:
    record = {
        "type": "match",
        "data": {
            "path": {"text": "a.log"},
            "lines": {"text": "foo bar foo\n"},
            "line_number": 1,
            "submatches": [
                {"match": {"text": "foo"}, "start": 0, "end": 3},
                {"match": {"text": "foo"}, "start": 8, "end": 11},
            ],
        },
    }
    fake = SimpleNamespace(returncode=0, stdout=_json.dumps(record) + "\n", stderr="")
    monkeypatch.setattr(rb, "run_subprocess", lambda *a, **k: fake)
    monkeypatch.setattr(RipgrepBackend, "_get_binary_name", lambda self: "rg")

    result = RipgrepBackend().search("a.log", "foo", SearchConfig())
    assert result.total_matches == 1  # counting stays one-per-matching-LINE (parity preserved)
    assert result.matches[0].submatches is not None
    assert len(result.matches[0].submatches) == 2


def test_vimgrep_emits_one_row_per_submatch_with_true_columns() -> None:
    result = SearchResult(matches=[_multi_match_line()], total_files=1, total_matches=1)
    rows = RipgrepFormatter(SearchConfig(vimgrep=True)).format(result).splitlines()
    assert len(rows) == 3  # was 1 (first occurrence only)
    # vimgrep row = path:line:COLUMN:text; columns are 1-based byte columns 0+1, 8+1, 16+1.
    assert [r.split(":")[2] for r in rows] == ["1", "9", "17"]


def test_column_emits_one_row_per_submatch() -> None:
    result = SearchResult(matches=[_multi_match_line()], total_files=1, total_matches=1)
    rows = RipgrepFormatter(SearchConfig(column=True, line_number=True)).format(result).splitlines()
    assert len(rows) == 3
    # row = line:COLUMN:text (no filename when total_files==1 and no with_filename)
    assert [r.split(":")[1] for r in rows] == ["1", "9", "17"]


def test_no_submatches_stays_single_row() -> None:
    # Non-rg backend / context line: no submatches -> unchanged single-row behavior.
    ml = MatchLine(line_number=1, text="foo bar foo", file="a.log")
    result = SearchResult(matches=[ml], total_files=1, total_matches=1)
    assert len(RipgrepFormatter(SearchConfig(vimgrep=True)).format(result).splitlines()) == 1
    assert (
        len(
            RipgrepFormatter(SearchConfig(column=True, line_number=True))
            .format(result)
            .splitlines()
        )
        == 1
    )
