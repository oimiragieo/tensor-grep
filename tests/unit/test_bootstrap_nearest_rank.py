"""A-04: did-you-mean candidates are RANKED by edit distance before the cap of 5 (Python door).

The old code sorted alphabetically, truncated to 5, and only then (no-op) re-sorted, so a close
match late in the alphabet (`sql` for `sq`, `scan` for `scn`) was cut by five unrelated
distance-3 names. The native door (`rust_core/src/main.rs :: nearest_commands`) ranks the same
way; `tests/e2e/test_routing_parity.py :: A90_MATRIX` pins that both doors agree.
"""

from __future__ import annotations

import itertools

import pytest

from tensor_grep.cli import bootstrap


@pytest.mark.parametrize(
    ("token", "must"),
    [("sq", "sql"), ("ru", "run"), ("scn", "scan"), ("defz", "defs")],
)
def test_nearest_commands_rank_before_cap(token: str, must: str) -> None:
    near = bootstrap._nearest_commands(token)
    # closest first (rank order, shared with the Rust door); never more than 5
    assert near[0] == must and len(near) <= 5, near


def test_nearest_commands_is_ordered_by_distance_then_name() -> None:
    near = bootstrap._nearest_commands("qqq")
    distances = [bootstrap._levenshtein("qqq", name) for name in near]
    assert distances == sorted(distances), (near, distances)
    for left, right in itertools.pairwise(near):
        if bootstrap._levenshtein("qqq", left) == bootstrap._levenshtein("qqq", right):
            assert left < right, (left, right)


def test_nearest_commands_controls() -> None:
    assert bootstrap._nearest_commands("searhc") == ["search"]
    assert bootstrap._nearest_commands("qqqqzzzz") == []
