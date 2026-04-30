from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import build_attempt_ledger


def prediction_attempt_status(record: dict[str, Any]) -> str:
    if str(record.get("model_patch") or "").strip():
        return "completed"
    return "needs_retry"


def scored_attempt_status(row: dict[str, Any]) -> str:
    if bool(row.get("validation_passed")):
        return "accepted"
    if bool(row.get("patch_applied")):
        return "validation_failed"
    return "rejected"


def build_prediction_attempt_ledgers(
    driver_payload: dict[str, Any],
    prediction_records: list[dict[str, Any]],
    *,
    reason_getter: Callable[[str, dict[str, Any]], str],
    outputs_getter: Callable[[str, dict[str, Any]], list[str]],
) -> dict[str, dict[str, Any]]:
    driver_records = list(driver_payload.get("records", []))
    driver_by_instance = {
        str(record["instance_id"]): dict(record)
        for record in driver_records
        if isinstance(record, dict) and str(record.get("instance_id", "")).strip()
    }
    predictions_by_instance: dict[str, list[dict[str, Any]]] = {}
    for record in prediction_records:
        instance_id = str(record.get("instance_id", "")).strip()
        if not instance_id:
            continue
        predictions_by_instance.setdefault(instance_id, []).append(dict(record))
    ledgers: dict[str, dict[str, Any]] = {}
    for instance_id, rows in predictions_by_instance.items():
        driver_record = driver_by_instance.get(instance_id)
        if driver_record is None:
            continue
        repo_root = str(Path(str(driver_record["repo_fixture"])).resolve())
        attempts: list[dict[str, Any]] = []
        any_completed = False
        for row in rows:
            system = str(row["system"])
            status = prediction_attempt_status(row)
            any_completed = any_completed or status == "completed"
            attempts.append(
                {
                    "attempt_id": f"{instance_id}:{system}",
                    "kind": "patch_prediction",
                    "status": status,
                    "retryable": status != "completed",
                    "retry_stage": "none" if status == "completed" else "full_attempt",
                    "retry_reason": reason_getter(instance_id, row),
                    "validation_success": False,
                    "score_artifact": "run_patch_bakeoff",
                    "inputs": [system],
                    "outputs": outputs_getter(instance_id, row),
                }
            )
        final_status = "completed" if any_completed else "needs_retry"
        ledger_input = {
            "generated_at_epoch_s": time.time(),
            "task_id": instance_id,
            "root": repo_root,
            "tasks": [{"task_id": instance_id, "root": repo_root}],
            "attempts": attempts,
            "final_outcome": {
                "status": final_status,
                "accepted_attempt_id": None,
                "score_artifact": None,
                "summary": "Patch predictions recorded; score before acceptance.",
            },
            "replay": {"multi_task": False, "task_chain": [], "next_action": "score patch bakeoff"},
        }
        ledgers[instance_id] = build_attempt_ledger.build_attempt_ledger_payload(ledger_input)
    return ledgers


def build_scored_attempt_ledgers(
    payload: dict[str, Any],
    scenarios: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows = list(payload.get("rows", []))
    rows_by_instance: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_instance.setdefault(str(row["instance_id"]), []).append(dict(row))
    scenarios_by_instance = {
        str(scenario["instance_id"]): dict(scenario)
        for scenario in scenarios
        if isinstance(scenario, dict) and str(scenario.get("instance_id", "")).strip()
    }
    ledgers: dict[str, dict[str, Any]] = {}
    for instance_id, instance_rows in rows_by_instance.items():
        scenario = scenarios_by_instance.get(instance_id)
        if scenario is None:
            continue
        repo_root = str(Path(str(scenario["repo_fixture"])).resolve())
        attempts: list[dict[str, Any]] = []
        for row in instance_rows:
            status = scored_attempt_status(row)
            attempts.append(
                {
                    "attempt_id": f"{instance_id}:{row['system']}",
                    "kind": "rewrite_apply_verify",
                    "status": status,
                    "retryable": not bool(row.get("validation_passed")),
                    "retry_stage": "validation"
                    if bool(row.get("patch_applied"))
                    else "full_attempt",
                    "retry_reason": str(row.get("reason") or status),
                    "validation_success": bool(row.get("validation_passed")),
                    "score_artifact": payload.get("artifact"),
                    "inputs": [str(row["system"])],
                    "outputs": [str(row.get("reason") or "")],
                }
            )
        ledger_input = {
            "generated_at_epoch_s": float(payload.get("generated_at_epoch_s", time.time())),
            "task_id": instance_id,
            "root": repo_root,
            "tasks": [{"task_id": instance_id, "root": repo_root}],
            "attempts": attempts,
            "final_outcome": None,
            "replay": {
                "multi_task": False,
                "task_chain": [],
                "next_action": "score accepted attempt",
            },
        }
        ledgers[instance_id] = build_attempt_ledger.build_attempt_ledger_payload(ledger_input)
    return ledgers
