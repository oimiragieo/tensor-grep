"""A required-evidence floor for a model-judged benchmark answer.

WHY THIS EXISTS. A benchmark that scores agent answers with another model measures TWO things
at once -- the agent and the judge -- and reports one number. The failure that matters is not
a judge being merely noisy; it is a judge rewarding a FAST answer that is wrong, because speed
is exactly what a retrieval change is trying to buy. A tool that makes the agent answer sooner
then scores better for the wrong reason, and the benchmark certifies its own regression.

The floor is a cheap, deterministic pre-filter applied BEFORE the judge: an answer that does
not cite the evidence the task requires cannot pass, no matter how confident the judge is.
It can only ever REJECT -- it never promotes an answer the judge failed, so it cannot inflate
a score. That asymmetry is the whole safety property, and it is pinned by a test.

SCOPE, stated rather than implied: this checks that required TOKENS APPEAR. It is not
comprehension, it cannot tell a correct explanation from a plausible one that happens to name
the right symbol, and a task whose required tokens are too generic will pass everything. It
raises the floor; it does not measure the ceiling. Pair it with a judge -- never replace one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FloorResult:
    """Outcome of the pre-judge check, with the reason kept rather than flattened to a bool.

    A bare False tells a benchmark author nothing about WHICH evidence was missing, and a
    floor whose rejections cannot be inspected is one nobody can calibrate.
    """

    passed: bool
    missing: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()

    def reason(self) -> str:
        if self.passed:
            return "all required evidence present"
        return f"missing required evidence: {', '.join(self.missing)}"


@dataclass(frozen=True)
class TaskFloor:
    """The evidence one benchmark task requires of any answer.

    ``required_all`` must ALL appear. ``required_any`` is satisfied by at least one member,
    for tasks where several spellings are equally correct (a symbol vs the file that defines
    it). Both are matched case-insensitively on WORD BOUNDARIES, so requiring ``open`` does
    not accept ``reopened``; a substring floor is the kind that passes everything.
    """

    required_all: tuple[str, ...] = ()
    required_any: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.required_all and not self.required_any:
            # A floor that requires nothing accepts everything, which is indistinguishable
            # from having no floor while LOOKING like a control. Refuse it at construction.
            raise ValueError(
                "a TaskFloor must require something; an empty floor silently accepts every "
                "answer while appearing to gate them"
            )


def _appears(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, re.IGNORECASE) is not None


def check_floor(answer: str, floor: TaskFloor) -> FloorResult:
    """Does ``answer`` cite the evidence ``floor`` requires?"""
    matched: list[str] = []
    missing: list[str] = []

    for needle in floor.required_all:
        (matched if _appears(needle, answer) else missing).append(needle)

    if floor.required_any:
        hits = [n for n in floor.required_any if _appears(n, answer)]
        if hits:
            matched.extend(hits)
        else:
            missing.append(f"any of ({'|'.join(floor.required_any)})")

    return FloorResult(passed=not missing, matched=tuple(matched), missing=tuple(missing))


def apply_floor(judge_passed: bool, answer: str, floor: TaskFloor) -> FloorResult:
    """Combine the judge's verdict with the floor.

    REJECT-ONLY, by construction: the result can never be ``passed=True`` unless the judge
    already said so. A floor that could promote an answer would be a second, weaker judge
    wearing a control's clothes.
    """
    result = check_floor(answer, floor)
    if not judge_passed:
        return FloorResult(
            passed=False,
            missing=result.missing or ("judge rejected",),
            matched=result.matched,
        )
    return result
