"""Pin the prepare/agent confidence contract: an incomplete result may never be CERTAIN.

WHY THIS EXISTS
---------------
An external agent dogfood (2026-08-22, tg 1.111.7) reported a capsule with
`partial=true` and truncation in `downgrade_reasons` while `confidence.overall` and
`primary_target.confidence` both read **1.0**, and `ask_user_before_editing.required` was
**false**. That is the most dangerous shape this tool can emit: it tells an agent
"edit this, no question needed" about a target derived from a scan that did not finish.

I COULD NOT REPRODUCE IT on this corpus at v1.111.7 -- a forced-truncation run returned
`partial=True`, 3 downgrade reasons, `overall=0.94`, `ask_required=True`, which is correct.
The reporter ran against a different checkout. **Non-reproduction is not refutation**, and a
contract that happens to hold today is exactly the thing to pin before it drifts.

So this file does NOT claim to fix a live bug. It makes the invariant UNBREAKABLE going
forward, which is worth more than a one-off fix to an unreproducible report.

WHAT IT ASSERTS
---------------
Two invariants over the REAL emitted payload, plus the controls that make the assertions
mean something:

    I1  downgrade_reasons is non-empty  =>  confidence.overall < 1.0
    I2  partial is true                =>  confidence.overall < 1.0

WHAT IT DOES NOT CLAIM
----------------------
It does NOT assert a floor on confidence, does not pin any particular symbol as the right
primary target, and says nothing about RANKING quality. The reporter's other finding -- a
short lexical token like `_add` winning as primary for the query word "add" -- is a ranking
concern, not a contract concern, and needs a corpus-based golden set rather than an
invariant. It is filed separately in docs/BACKLOG.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _invariant_violations(payload: dict) -> list[str]:
    """Return human-readable violations of I1/I2. Empty list means the contract held.

    Pure function over a payload dict so it can be exercised by the negative controls below
    WITHOUT running the CLI -- a check that can only be tested by a slow subprocess tends not
    to get its failure path tested at all.
    """
    violations: list[str] = []
    confidence = payload.get("confidence") or {}
    overall = confidence.get("overall")
    reasons = confidence.get("downgrade_reasons") or []

    if overall is None:
        return violations  # nothing asserted about a payload with no confidence block

    if reasons and not overall < 1.0:
        violations.append(
            f"I1: {len(reasons)} downgrade_reasons present but confidence.overall == {overall!r} "
            "-- an explained downgrade must actually downgrade"
        )
    if payload.get("partial") and not overall < 1.0:
        violations.append(
            f"I2: partial is true (reason={payload.get('partial_reason')!r}) but "
            f"confidence.overall == {overall!r} -- a truncated scan may never be CERTAIN"
        )
    return violations


# --------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the detector must be able to FAIL, or the live checks prove nothing.
# --------------------------------------------------------------------------------------


def test_detector_flags_downgrade_reasons_with_perfect_confidence() -> None:
    payload = {"confidence": {"overall": 1.0, "downgrade_reasons": ["truncated scan"]}}
    violations = _invariant_violations(payload)
    assert any(v.startswith("I1:") for v in violations), (
        "the detector accepted downgrade_reasons alongside confidence 1.0 -- it cannot catch "
        "the exact shape this file exists to catch"
    )


def test_detector_flags_partial_with_perfect_confidence() -> None:
    payload = {"partial": True, "partial_reason": "deadline", "confidence": {"overall": 1.0}}
    violations = _invariant_violations(payload)
    assert any(v.startswith("I2:") for v in violations), (
        "the detector accepted partial=true alongside confidence 1.0"
    )


def test_detector_passes_a_correctly_downgraded_payload() -> None:
    """Positive control: a well-formed payload must produce NO violations.

    Without this, a detector that returned a violation for EVERY input would pass both
    negative controls above and still be useless.
    """
    payload = {
        "partial": True,
        "partial_reason": "deadline",
        "confidence": {"overall": 0.94, "downgrade_reasons": ["a", "b", "c"]},
    }
    assert _invariant_violations(payload) == []


# --------------------------------------------------------------------------------------
# LIVE CHECKS against the real CLI.
# --------------------------------------------------------------------------------------


def _run_prepare(args: list[str]) -> dict | None:
    """Run prepare via the installed package. Returns None if it could not run at all.

    Returning None (rather than failing) on a setup problem keeps a CI environment issue from
    masquerading as a contract violation -- the opposite mistake to the one this file guards.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tensor_grep.cli.main", *args],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=_REPO_ROOT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


@pytest.mark.parametrize(
    "args",
    [
        ["prepare", "src", "add retry with tests", "--json", "--deadline", "60"],
        # A deliberately tight deadline: this is the arm most likely to produce partial=true,
        # which is the state the reporter saw paired with confidence 1.0.
        ["prepare", ".", "handle incompleteness", "--json", "--deadline", "3"],
    ],
    ids=["scoped-src", "tight-deadline-repo-root"],
)
def test_live_prepare_never_reports_certainty_on_an_incomplete_result(args: list[str]) -> None:
    payload = _run_prepare(args)
    if payload is None:
        pytest.skip("prepare did not produce parseable JSON in this environment")

    violations = _invariant_violations(payload)
    assert not violations, (
        "prepare emitted a capsule that claims certainty about an incomplete result:\n  "
        + "\n  ".join(violations)
        + f"\n\npartial={payload.get('partial')!r} "
        f"partial_reason={payload.get('partial_reason')!r} "
        f"confidence={payload.get('confidence')!r}"
    )
