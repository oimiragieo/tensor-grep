"""Canonical ripgrep parity contract for public search flags.

Each row describes one feature:
- public_flags: the full alias family surfaced to users
- rg_args / tg_args: one canonical executable scenario for that feature
"""

from __future__ import annotations

from typing import Literal, TypedDict


class RGContractRow(TypedDict):
    id: str
    public_flags: tuple[str, ...]
    rg_args: tuple[str, ...]
    tg_args: tuple[str, ...]
    output_mode: Literal[
        "text",
        "count",
        "json",
        "ndjson",
        "files",
        "files_with_matches",
        "files_without_match",
        "help",
        "error",
    ]
    parity_expectation: Literal["exact", "normalized", "unsupported"]
    benchmarkable: bool


RG_CONTRACT_ROWS: tuple[RGContractRow, ...] = (
    {
        "id": "ignore-case",
        "public_flags": ("-i", "--ignore-case"),
        "rg_args": ("--ignore-case",),
        "tg_args": ("--ignore-case",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "invert-match",
        "public_flags": ("-v", "--invert-match"),
        "rg_args": ("--invert-match",),
        "tg_args": ("--invert-match",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "context",
        "public_flags": ("-C", "--context"),
        "rg_args": ("--context", "2"),
        "tg_args": ("--context", "2"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "after-context",
        "public_flags": ("-A", "--after-context"),
        "rg_args": ("--after-context", "2"),
        "tg_args": ("--after-context", "2"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "before-context",
        "public_flags": ("-B", "--before-context"),
        "rg_args": ("--before-context", "2"),
        "tg_args": ("--before-context", "2"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "glob",
        "public_flags": ("-g", "--glob"),
        "rg_args": ("--glob", "*.py"),
        "tg_args": ("--glob", "*.py"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "files-with-matches",
        "public_flags": ("-l", "--files-with-matches"),
        "rg_args": ("--files-with-matches",),
        "tg_args": ("--files-with-matches",),
        "output_mode": "files_with_matches",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "files-without-match",
        "public_flags": ("--files-without-match",),
        "rg_args": ("--files-without-match",),
        "tg_args": ("--files-without-match",),
        "output_mode": "files_without_match",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "json",
        "public_flags": ("--json",),
        "rg_args": ("--json",),
        "tg_args": ("--json",),
        "output_mode": "json",
        "parity_expectation": "normalized",
        "benchmarkable": False,
    },
    {
        "id": "ndjson",
        "public_flags": ("--ndjson",),
        "rg_args": ("--ndjson",),
        "tg_args": ("--ndjson",),
        "output_mode": "ndjson",
        "parity_expectation": "normalized",
        "benchmarkable": False,
    },
    {
        "id": "fixed-strings",
        "public_flags": ("-F", "--fixed-strings"),
        "rg_args": ("--fixed-strings",),
        "tg_args": ("--fixed-strings",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "word-regexp",
        "public_flags": ("-w", "--word-regexp"),
        "rg_args": ("--word-regexp",),
        "tg_args": ("--word-regexp",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "max-count",
        "public_flags": ("-m", "--max-count"),
        "rg_args": ("--max-count", "10"),
        "tg_args": ("--max-count", "10"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "type",
        "public_flags": ("-t", "--type"),
        "rg_args": ("--type", "py"),
        "tg_args": ("--type", "py"),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "hidden",
        "public_flags": ("-.", "--hidden"),
        "rg_args": ("--hidden",),
        "tg_args": ("--hidden",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "follow",
        "public_flags": ("-L", "--follow"),
        "rg_args": ("--follow",),
        "tg_args": ("--follow",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "smart-case",
        "public_flags": ("-S", "--smart-case"),
        "rg_args": ("--smart-case",),
        "tg_args": ("--smart-case",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "line-number",
        "public_flags": ("-n", "--line-number"),
        "rg_args": ("--line-number",),
        "tg_args": ("--line-number",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "column",
        "public_flags": ("--column",),
        "rg_args": ("--column",),
        "tg_args": ("--column",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "count",
        "public_flags": ("-c", "--count"),
        "rg_args": ("--count",),
        "tg_args": ("--count",),
        "output_mode": "count",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "count-matches",
        "public_flags": ("--count-matches",),
        "rg_args": ("--count-matches",),
        "tg_args": ("--count-matches",),
        "output_mode": "count",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
    {
        "id": "text",
        "public_flags": ("-a", "--text"),
        "rg_args": ("--text",),
        "tg_args": ("--text",),
        "output_mode": "text",
        "parity_expectation": "exact",
        "benchmarkable": True,
    },
)


PUBLIC_SEARCH_HELP_FLAGS: tuple[str, ...] = tuple(
    flag for row in RG_CONTRACT_ROWS for flag in row["public_flags"]
)
