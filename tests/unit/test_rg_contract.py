from __future__ import annotations

from tensor_grep.cli.rg_contract import (
    PUBLIC_SEARCH_HELP_FLAGS,
    RG_CONTRACT_ROWS,
    RGContractRow,
)

EXPECTED_ROWS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "ignore-case": (("-i", "--ignore-case"), ("--ignore-case",)),
    "invert-match": (("-v", "--invert-match"), ("--invert-match",)),
    "context": (("-C", "--context"), ("--context", "2")),
    "after-context": (("-A", "--after-context"), ("--after-context", "2")),
    "before-context": (("-B", "--before-context"), ("--before-context", "2")),
    "glob": (("-g", "--glob"), ("--glob", "*.py")),
    "files-with-matches": (("-l", "--files-with-matches"), ("--files-with-matches",)),
    "files-without-match": (("--files-without-match",), ("--files-without-match",)),
    "json": (("--json",), ("--json",)),
    "ndjson": (("--ndjson",), ("--ndjson",)),
    "fixed-strings": (("-F", "--fixed-strings"), ("--fixed-strings",)),
    "word-regexp": (("-w", "--word-regexp"), ("--word-regexp",)),
    "max-count": (("-m", "--max-count"), ("--max-count", "10")),
    "type": (("-t", "--type"), ("--type", "py")),
    "hidden": (("-.", "--hidden"), ("--hidden",)),
    "follow": (("-L", "--follow"), ("--follow",)),
    "smart-case": (("-S", "--smart-case"), ("--smart-case",)),
    "line-number": (("-n", "--line-number"), ("--line-number",)),
    "column": (("--column",), ("--column",)),
    "count": (("-c", "--count"), ("--count",)),
    "count-matches": (("--count-matches",), ("--count-matches",)),
    "text": (("-a", "--text"), ("--text",)),
}


def _load_rows() -> tuple[RGContractRow, ...]:
    return RG_CONTRACT_ROWS


def _assert_row_contract(row: RGContractRow) -> None:
    assert row["id"] in EXPECTED_ROWS, f"Unexpected row id {row['id']}"
    assert row["public_flags"], f"Row {row['id']} must advertise at least one public flag"
    assert row["rg_args"], f"Row {row['id']} must define a canonical rg scenario"
    assert row["tg_args"], f"Row {row['id']} must define a canonical tg scenario"
    assert len(set(row["public_flags"])) == len(row["public_flags"]), (
        f"Row {row['id']} has duplicate public flags"
    )
    assert len(set(row["rg_args"])) == len(row["rg_args"]), f"Row {row['id']} has duplicate rg args"
    assert len(set(row["tg_args"])) == len(row["tg_args"]), f"Row {row['id']} has duplicate tg args"
    assert row["rg_args"] == row["tg_args"], f"Row {row['id']} must use one canonical scenario"
    assert row["output_mode"] in {
        "text",
        "count",
        "json",
        "ndjson",
        "files",
        "files_with_matches",
        "files_without_match",
        "help",
        "error",
    }, f"Unexpected output_mode on row {row['id']}"
    assert row["parity_expectation"] in {"exact", "normalized", "unsupported"}, (
        f"Unexpected parity_expectation on row {row['id']}"
    )

    expected_public_flags, expected_args = EXPECTED_ROWS[row["id"]]
    assert row["public_flags"] == expected_public_flags
    assert row["rg_args"] == expected_args
    assert row["tg_args"] == expected_args


def test_rg_contract_rows_have_unique_ids_and_expected_shapes() -> None:
    rows = _load_rows()
    ids = [row["id"] for row in rows]

    assert len(ids) == len(set(ids)), "Contract row ids must be unique"
    assert set(ids) == set(EXPECTED_ROWS), "Contract rows must cover the expected feature set"

    for row in rows:
        _assert_row_contract(row)


def test_rg_contract_rows_cover_required_public_flags() -> None:
    rows = _load_rows()
    covered_flags = {flag for row in rows for flag in row["public_flags"]}

    assert tuple(sorted(covered_flags)) == tuple(sorted(PUBLIC_SEARCH_HELP_FLAGS))


def test_rg_contract_rows_do_not_merge_count_rows() -> None:
    rows_by_id = {row["id"]: row for row in _load_rows()}

    assert rows_by_id["count"]["public_flags"] != rows_by_id["count-matches"]["public_flags"]
    assert rows_by_id["count"]["rg_args"] != rows_by_id["count-matches"]["rg_args"]
    assert rows_by_id["count"]["tg_args"] != rows_by_id["count-matches"]["tg_args"]
