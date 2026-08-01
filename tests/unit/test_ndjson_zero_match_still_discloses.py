"""A zero-match `--ndjson` run must still emit its disclosure. It used to emit NOTHING.

THE DEFECT, and it is a structural one rather than a missing field. `NdjsonFormatter.format`
merges the routing/incompleteness envelope into EACH MATCH ROW:

    for match in result.matches:
        row = {**envelope, **_match_payload(match), ...}
    return "\\n".join(rows)

So the disclosure rides on the rows -- and vanishes precisely when there are none. A search that
was CUT SHORT and found nothing returns the empty string. An agent reading that stream cannot
distinguish "nothing matched" from "the scan died before it could look", which is the exact
distinction `result_incomplete` exists to carry.

Not hypothetical: `result_incomplete` / `incomplete_reason_class` / `path_was_defaulted` /
`scope_note` are all in that envelope. Every one of them is silently dropped on the zero-match path.

WHY THE FIX IS NARROW, deliberately. The Rust engine emits a `type: "summary"` record on EVERY
`--ndjson` run, complete or not, on the documented reasoning that a record which appears only when
something went wrong is one a streaming reader never learns to expect. Bringing Python to full
parity means adding a summary line to every stream -- a WIRE CHANGE that several existing tests
(which `json.loads` the whole output as a single object) would fail on, and which arguably needs an
MCP contract bump. That is a real change, and it is tracked separately.

What this fixes is the case where the current design loses information outright: zero rows, so zero
carriers. A non-empty stream already carries the envelope on every row and needs nothing. Existing
output for any search WITH matches stays byte-identical.
"""

from __future__ import annotations

import json

from tensor_grep.cli.formatters.json_fmt import NdjsonFormatter
from tensor_grep.core.result import MatchLine, SearchResult


def _zero_match_incomplete() -> SearchResult:
    result = SearchResult(matches=[], total_matches=0, total_files=0)
    result.result_incomplete = True
    result.incomplete_reason = "1 path(s) could not be read"
    result.incomplete_reason_class = "unreadable_path"
    return result


def test_a_truncated_zero_match_ndjson_run_is_not_silent() -> None:
    """THE DEFECT. An empty stream is indistinguishable from a clean no-match."""
    output = NdjsonFormatter().format(_zero_match_incomplete())

    assert output.strip(), (
        "a zero-match INCOMPLETE --ndjson run emitted nothing at all. The reader cannot tell "
        "'nothing matched' from 'the scan died', which is the one thing result_incomplete exists "
        "to say."
    )
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert len(rows) == 1, f"expected exactly one disclosure record, got {len(rows)}: {rows}"
    assert rows[0].get("result_incomplete") is True, rows[0]
    assert rows[0].get("incomplete_reason_class") == "unreadable_path", rows[0]


def test_a_clean_zero_match_run_stays_silent() -> None:
    """CONTROL ARM, and the one that keeps this from becoming noise.

    A COMPLETE search that simply found nothing has nothing to disclose. Emitting a record there
    would put a line on every empty stream in the product to serve the rare truncated case -- and
    would train readers to skip it, which is how the disclosure stops working. Without this arm,
    'always emit something' passes the test above.
    """
    output = NdjsonFormatter().format(SearchResult(matches=[], total_matches=0, total_files=0))

    assert output == "", (
        f"a COMPLETE zero-match run must stay byte-identical (empty), got {output!r}"
    )


def test_a_defaulted_scope_zero_match_run_discloses() -> None:
    """The sibling cause from #871: the scope note has the same no-carrier problem.

    `path_was_defaulted`/`scope_note` live in the same envelope, so they vanish on an empty stream
    for the same structural reason. Covering only `result_incomplete` would fix one cause and leave
    its twin broken -- the fix-the-instance-not-the-class failure this repo keeps paying for.
    """
    result = SearchResult(matches=[], total_matches=0, total_files=0)
    result.path_was_defaulted = True
    result.scope_note = "note: no PATH was given, so the search defaulted to the current directory."

    rows = [
        json.loads(line) for line in NdjsonFormatter().format(result).splitlines() if line.strip()
    ]

    assert len(rows) == 1, f"expected a disclosure record for a defaulted zero-match run: {rows}"
    assert rows[0].get("path_was_defaulted") is True, rows[0]
    assert "scope_note" in rows[0], rows[0]


def test_a_matching_run_is_byte_identical() -> None:
    """CONTROL ARM: the fix must not add a line to any stream that already has rows.

    This is what keeps the change off the wire-format-change list. Every existing consumer of a
    non-empty --ndjson stream must see exactly what it saw before.
    """
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="hit", file="a.py")],
        total_matches=1,
        total_files=1,
    )
    result.result_incomplete = True
    result.incomplete_reason = "1 path(s) could not be read"
    result.incomplete_reason_class = "unreadable_path"

    lines = [line for line in NdjsonFormatter().format(result).splitlines() if line.strip()]

    assert len(lines) == 1, f"a 1-match stream must emit exactly 1 row, got {len(lines)}: {lines}"
    row = json.loads(lines[0])
    assert row["result_incomplete"] is True, "the envelope already rides on the match row"
    assert row["file"] == "a.py"
