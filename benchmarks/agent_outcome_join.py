"""Join observed patch outcomes to benchmark predictions on a complete identity.

AGT-01 (docs/plans/2026-09-07-agentic-quality-simplification.md#task-01).

Pure logic: stdlib only, no I/O, no subprocess, no import from any other benchmarks
module. A partial identity is never completed by guessing, a duplicate identity fails
closed, and an unobserved execution is UNAVAILABLE -- never counted as success.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any, TypeAlias, get_type_hints

IDENTITY_FIELDS: tuple[str, ...] = (
    "system_id",
    "instance_id",
    "repo_commit",
    "tool_version",
    "model_id",
    "budget_id",
)

#: The type of every identity component. A named alias rather than a bare ``str`` so the
#: import-time drift guard below actually resolves a module-global name -- see the guard.
IdentityValue: TypeAlias = str


@dataclass(frozen=True)
class OutcomeIdentity:
    system_id: IdentityValue
    instance_id: IdentityValue
    repo_commit: IdentityValue
    tool_version: IdentityValue
    model_id: IdentityValue
    budget_id: IdentityValue

    def as_dict(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in IDENTITY_FIELDS}

    def sort_key(self) -> tuple[str, ...]:
        """Deterministic ordering key.

        Declared as its own method returning tuple[str, ...] so the sorted() call
        below has a single, non-union key type under mypy strict.
        """
        return tuple(getattr(self, field) for field in IDENTITY_FIELDS)


#: Import-time drift guard. A field added to OutcomeIdentity but not to IDENTITY_FIELDS
#: (or the reverse) would make the join key disagree with the declared contract in
#: run_agent_workflow_benchmarks.py, silently. Resolving the annotations also requires
#: this module to be present in sys.modules while it executes -- every loader in this
#: repo that reaches this file by path registers it first (PLAN section 0, CORRECTION 7).
ANNOTATED_IDENTITY_FIELDS: tuple[str, ...] = tuple(get_type_hints(OutcomeIdentity))

if ANNOTATED_IDENTITY_FIELDS != IDENTITY_FIELDS:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "OutcomeIdentity fields drifted from IDENTITY_FIELDS: "
        f"{ANNOTATED_IDENTITY_FIELDS!r} != {IDENTITY_FIELDS!r}"
    )


def extract_identity(record: dict[str, Any]) -> tuple[OutcomeIdentity | None, list[str]]:
    """Return (identity, []) when complete, else (None, sorted missing field names)."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for field in IDENTITY_FIELDS:
        raw = record.get(field)
        text = raw.strip() if isinstance(raw, str) else ""
        if not text:
            missing.append(field)
        else:
            values[field] = text
    if missing:
        return None, sorted(missing)
    return OutcomeIdentity(**values), []


def adapt_legacy_bakeoff_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map a run_patch_bakeoff.py result row onto the outcome-record shape.

    The legacy row (benchmarks/run_patch_bakeoff.py evaluate_prediction) carries only
    instance_id + system. The four remaining identity fields are NOT invented: the
    adapted record stays incomplete and lands in unidentified_outcomes.
    """
    adapted = dict(row)
    system = str(row.get("system") or "").strip()
    if system and not str(adapted.get("system_id") or "").strip():
        adapted["system_id"] = system
    adapted.setdefault("execution_observed", row.get("patch_applied") is True)
    adapted.setdefault("tokens_in", None)
    adapted.setdefault("tokens_out", None)
    adapted.setdefault("elapsed_s", None)
    return adapted


def _index(
    records: list[dict[str, Any]],
) -> tuple[
    dict[OutcomeIdentity, dict[str, Any]],
    list[dict[str, Any]],
    Counter[OutcomeIdentity],
]:
    by_identity: dict[OutcomeIdentity, dict[str, Any]] = {}
    unidentified: list[dict[str, Any]] = []
    counts: Counter[OutcomeIdentity] = Counter()
    for record in records:
        identity, missing = extract_identity(record)
        if identity is None:
            unidentified.append({"record": dict(record), "missing_fields": missing})
            continue
        counts[identity] += 1
        by_identity[identity] = dict(record)
    return by_identity, unidentified, counts


def _outcome_state(outcome: dict[str, Any]) -> str:
    if outcome.get("execution_observed") is not True:
        return "unavailable"
    return "passed" if outcome.get("validation_passed") is True else "failed"


def _command_fit(prediction: dict[str, Any]) -> bool:
    commands = prediction.get("predicted_validation_commands")
    if not isinstance(commands, list):
        return False
    return any(isinstance(item, str) and bool(item.strip()) for item in commands)


def _nullable_total(values: list[Any], *, integer_only: bool = False) -> Any:
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (integer_only and not isinstance(value, int))
        or (isinstance(value, float) and not isfinite(value))
        for value in values
    ):
        return None
    if not values:
        return 0
    try:
        total = sum(values)
    except OverflowError:
        return None
    if isinstance(total, float) and not isfinite(total):
        return None
    return round(total, 6) if isinstance(total, float) else total


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def build_outcome_join_report(
    *,
    predictions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    pred_by_id, unidentified_predictions, pred_counts = _index(predictions)
    out_by_id, unidentified_outcomes, out_counts = _index(outcomes)

    duplicates: list[dict[str, Any]] = []
    for side, counts in (("prediction", pred_counts), ("outcome", out_counts)):
        for identity, count in sorted(counts.items(), key=lambda item: item[0].sort_key()):
            if count > 1:
                duplicates.append({
                    "identity": identity.as_dict(),
                    "side": side,
                    "count": count,
                })
    duplicate_keys = {OutcomeIdentity(**entry["identity"]) for entry in duplicates}

    joined: list[dict[str, Any]] = []
    for identity, prediction in sorted(pred_by_id.items(), key=lambda item: item[0].sort_key()):
        if identity in duplicate_keys:
            continue
        outcome = out_by_id.get(identity)
        if outcome is None:
            continue
        state = _outcome_state(outcome)
        joined.append({
            "identity": identity.as_dict(),
            "command_fit": _command_fit(prediction),
            "verified_task_success": state == "passed",
            "outcome_state": state,
            "tokens_in": outcome.get("tokens_in"),
            "tokens_out": outcome.get("tokens_out"),
            "elapsed_s": outcome.get("elapsed_s"),
        })

    joined_keys = {OutcomeIdentity(**row["identity"]) for row in joined}
    unmatched_predictions = [
        identity.as_dict()
        for identity in sorted(pred_by_id, key=lambda item: item.sort_key())
        if identity not in joined_keys and identity not in duplicate_keys
    ]
    unmatched_outcomes = [
        identity.as_dict()
        for identity in sorted(out_by_id, key=lambda item: item.sort_key())
        if identity not in joined_keys and identity not in duplicate_keys
    ]

    complete = [row for row in joined if row["outcome_state"] != "unavailable"]
    success_cases = sum(1 for row in complete if row["verified_task_success"])
    command_fit_cases = sum(1 for row in joined if row["command_fit"])

    return {
        "artifact": "agent_outcome_join",
        "identity_fields": list(IDENTITY_FIELDS),
        "joined": joined,
        "unidentified_predictions": unidentified_predictions,
        "unidentified_outcomes": unidentified_outcomes,
        "duplicate_identities": duplicates,
        "unmatched_predictions": unmatched_predictions,
        "unmatched_outcomes": unmatched_outcomes,
        "summary": {
            "complete_cases": len(complete),
            "incomplete_cases": len(joined) - len(complete),
            "verified_task_success_cases": success_cases,
            "verified_task_success_rate": _rate(success_cases, len(complete)),
            "command_fit_cases": command_fit_cases,
            "command_fit_rate": _rate(command_fit_cases, len(joined)),
            "duplicate_identity_count": len(duplicates),
            "unidentified_prediction_count": len(unidentified_predictions),
            "unidentified_outcome_count": len(unidentified_outcomes),
            "unmatched_prediction_count": len(unmatched_predictions),
            "unmatched_outcome_count": len(unmatched_outcomes),
            "tokens_in_total": _nullable_total(
                [row["tokens_in"] for row in joined], integer_only=True
            ),
            "tokens_out_total": _nullable_total(
                [row["tokens_out"] for row in joined], integer_only=True
            ),
            "elapsed_s_total": _nullable_total([row["elapsed_s"] for row in joined]),
        },
    }
