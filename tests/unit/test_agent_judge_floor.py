"""The required-evidence floor for a model-judged benchmark.

The floor exists to stop a fast-but-wrong answer winning, so the tests that matter are the
ones proving it can only ever REJECT -- a control that could promote an answer would inflate
the very score it claims to protect.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
if str(_BENCHMARKS) not in sys.path:
    sys.path.insert(0, str(_BENCHMARKS))

from agent_judge_floor import TaskFloor, apply_floor, check_floor  # noqa: E402


def test_an_answer_citing_the_required_evidence_passes() -> None:
    floor = TaskFloor(required_all=("execute_ripgrep_search", "bootstrap.py"))
    answer = "The guard runs first in execute_ripgrep_search, reached via bootstrap.py."

    result = check_floor(answer, floor)

    assert result.passed
    assert not result.missing


def test_a_confident_answer_missing_the_evidence_is_rejected() -> None:
    """The failure this module exists for: fluent, plausible, cites nothing."""
    floor = TaskFloor(required_all=("execute_ripgrep_search",))
    answer = "The search path validates its inputs early and then delegates to the backend."

    result = check_floor(answer, floor)

    assert not result.passed
    assert "execute_ripgrep_search" in result.missing
    assert "missing required evidence" in result.reason()


def test_required_any_accepts_either_correct_spelling() -> None:
    floor = TaskFloor(required_any=("repo_map.py", "build_repo_map"))

    assert check_floor("see build_repo_map", floor).passed
    assert check_floor("see repo_map.py", floor).passed
    assert not check_floor("see the indexer", floor).passed


def test_matching_is_word_bounded_not_substring() -> None:
    """A substring floor passes everything. Requiring `open` must not accept `reopened`,
    or the gate reports coverage it does not have.
    """
    floor = TaskFloor(required_all=("open",))

    assert not check_floor("the session was reopened later", floor).passed
    assert check_floor("call open on the path", floor).passed


def test_matching_ignores_case_but_not_word_identity() -> None:
    floor = TaskFloor(required_all=("SearchConfig",))

    assert check_floor("built from a searchconfig", floor).passed
    assert not check_floor("built from a SearchConfiguration", floor).passed


def test_the_floor_can_only_reject_never_promote() -> None:
    """THE safety property. If the judge failed an answer, no amount of correct evidence may
    turn it into a pass -- otherwise the floor is a second, weaker judge that inflates scores.
    """
    floor = TaskFloor(required_all=("execute_ripgrep_search",))
    perfect_answer = "It is execute_ripgrep_search, first statement."

    # Evidence is fully present...
    assert check_floor(perfect_answer, floor).passed
    # ...and the judge's NO still wins.
    combined = apply_floor(False, perfect_answer, floor)
    assert not combined.passed
    assert "judge rejected" in combined.missing


def test_a_judge_pass_still_fails_when_evidence_is_missing() -> None:
    """The other direction, which is the point of having a floor at all."""
    floor = TaskFloor(required_all=("execute_ripgrep_search",))

    combined = apply_floor(True, "It validates early and delegates.", floor)

    assert not combined.passed
    assert "execute_ripgrep_search" in combined.missing


def test_a_judge_pass_with_evidence_passes() -> None:
    """CONTROL. Without this, every assertion above would be satisfied by a floor that
    rejects unconditionally -- which would look rigorous and measure nothing.
    """
    floor = TaskFloor(required_all=("execute_ripgrep_search",))

    combined = apply_floor(True, "It is execute_ripgrep_search.", floor)

    assert combined.passed
    assert not combined.missing


def test_an_empty_floor_is_refused_at_construction() -> None:
    """A floor requiring nothing accepts everything while LOOKING like a gate -- the exact
    shape of a check that cannot fail.
    """
    with pytest.raises(ValueError, match="must require something"):
        TaskFloor()
