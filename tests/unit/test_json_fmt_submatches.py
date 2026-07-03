"""q6-jsonfmt-submatches: JsonFormatter._match_payload built each match dict from a hardcoded
key tuple that never read MatchLine.submatches (per-occurrence byte offsets), unlike
RipgrepFormatter (which reads match.submatches, see ripgrep_fmt.py::_submatch_columns). Result:
--json lost column/offset info the vimgrep/column path relies on and could not report multiple
occurrences on one line.

Fix: mirror ripgrep_fmt.py -- when MatchLine.submatches is present, emit them (same shape/keys
rg's own submatches use: "match"/"start"/"end") in the JSON match object; when absent, omit the
key cleanly (no null/empty noise, no crash).
"""

from __future__ import annotations

import json

from tensor_grep.cli.formatters.json_fmt import JsonFormatter
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


def test_json_output_includes_submatches_with_correct_start_end() -> None:
    result = SearchResult(matches=[_multi_match_line()], total_files=1, total_matches=1)

    parsed = json.loads(JsonFormatter().format(result))

    submatches = parsed["matches"][0]["submatches"]
    assert submatches == [
        {"match": {"text": "foo"}, "start": 0, "end": 3},
        {"match": {"text": "foo"}, "start": 8, "end": 11},
        {"match": {"text": "foo"}, "start": 16, "end": 19},
    ]


def test_json_output_omits_submatches_key_when_absent() -> None:
    # Non-rg backend / context line: no submatches -> key must be omitted, not null.
    ml = MatchLine(line_number=1, text="foo bar foo", file="a.log")
    result = SearchResult(matches=[ml], total_files=1, total_matches=1)

    parsed = json.loads(JsonFormatter().format(result))

    assert "submatches" not in parsed["matches"][0]
