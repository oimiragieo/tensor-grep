from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "attempt_ledger.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a machine-readable agent attempt ledger.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to input JSON describing attempts and optional replay metadata.",
    )
    parser.add_argument(
        "--output", default=str(default_output_path()), help="Output path for the ledger JSON."
    )
    return parser.parse_args(argv)


def load_input_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("attempt ledger input must be a JSON object")
    return payload


def _normalize_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    status = str(attempt.get("status") or "pending")
    retryable = bool(attempt.get("retryable", status not in {"accepted", "rejected"}))
    return {
        "attempt_id": str(attempt["attempt_id"]),
        "parent_attempt_id": attempt.get("parent_attempt_id"),
        "kind": str(attempt.get("kind") or "rewrite_apply_verify"),
        "status": status,
        "retryable": retryable,
        "retry_stage": str(
            attempt.get("retry_stage") or ("none" if not retryable else "full_attempt")
        ),
        "retry_reason": str(attempt.get("retry_reason") or status),
        "checkpoint_id": attempt.get("checkpoint_id"),
        "audit_manifest_path": attempt.get("audit_manifest_path"),
        "validation_success": bool(attempt.get("validation_success", False)),
        "score_artifact": attempt.get("score_artifact"),
        "session_id": attempt.get("session_id"),
        "inputs": list(attempt.get("inputs", [])),
        "outputs": list(attempt.get("outputs", [])),
    }


def _infer_final_outcome(
    attempts: list[dict[str, Any]], provided: dict[str, Any] | None
) -> dict[str, Any]:
    if provided:
        return {
            "status": str(provided.get("status") or "pending"),
            "accepted_attempt_id": provided.get("accepted_attempt_id"),
            "score_artifact": provided.get("score_artifact"),
            "summary": str(provided.get("summary") or ""),
        }
    accepted = next(
        (attempt for attempt in reversed(attempts) if attempt["status"] == "accepted"), None
    )
    if accepted is not None:
        return {
            "status": "accepted",
            "accepted_attempt_id": accepted["attempt_id"],
            "score_artifact": accepted.get("score_artifact"),
            "summary": f"Accepted on {accepted['attempt_id']}.",
        }
    terminal = attempts[-1] if attempts else None
    return {
        "status": str(terminal["status"]) if terminal is not None else "pending",
        "accepted_attempt_id": None,
        "score_artifact": terminal.get("score_artifact") if terminal is not None else None,
        "summary": f"Terminal status: {terminal['status']}."
        if terminal is not None
        else "No attempts recorded.",
    }


def _infer_partial_retry_ledger(
    attempts: list[dict[str, Any]], provided: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    if provided is not None:
        return list(provided)
    retry_rows: list[dict[str, Any]] = []
    by_parent = {attempt["attempt_id"]: attempt for attempt in attempts}
    for attempt in attempts:
        parent_id = attempt.get("parent_attempt_id")
        if not parent_id or parent_id not in by_parent:
            continue
        parent = by_parent[parent_id]
        retry_rows.append(
            {
                "attempt_id": parent["attempt_id"],
                "resumed_from": parent.get("retry_stage", "full_attempt"),
                "resumed_as": attempt["attempt_id"],
                "reason": parent.get("retry_reason", parent.get("status", "retry")),
            }
        )
    return retry_rows


def _infer_replay(attempts: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    replay_input = dict(payload.get("replay") or {})
    session_ids = [str(attempt["session_id"]) for attempt in attempts if attempt.get("session_id")]
    distinct_session_ids = list(dict.fromkeys(session_ids))
    tasks = list(payload.get("tasks", []))
    multi_task = bool(replay_input.get("multi_task", len(tasks) > 1))
    audit_chain = replay_input.get("audit_chain")
    if audit_chain is None:
        audit_chain = [
            attempt["audit_manifest_path"]
            for attempt in attempts
            if attempt.get("audit_manifest_path")
        ]
    handoff = replay_input.get("handoff")
    if handoff is None and len(distinct_session_ids) > 1:
        handoff = {
            "from_session_id": distinct_session_ids[0],
            "to_session_id": distinct_session_ids[1],
            "reason": "session handoff",
        }
    task_chain = replay_input.get("task_chain")
    if task_chain is None and multi_task:
        task_chain = [str(task["task_id"]) for task in tasks]
    return {
        "preserve_attempt_ids": bool(replay_input.get("preserve_attempt_ids", True)),
        "partial_retry_ledger": _infer_partial_retry_ledger(
            attempts, replay_input.get("partial_retry_ledger")
        ),
        "audit_chain": list(audit_chain),
        "next_action": str(replay_input.get("next_action") or "score accepted attempt"),
        "multi_session": bool(replay_input.get("multi_session", len(distinct_session_ids) > 1)),
        "handoff": handoff,
        "multi_task": multi_task,
        "task_chain": list(task_chain or []),
    }


def build_attempt_ledger_payload(payload: dict[str, Any]) -> dict[str, Any]:
    attempts_input = payload.get("attempts")
    if not isinstance(attempts_input, list) or not attempts_input:
        raise ValueError("attempt ledger input must include a non-empty attempts list")
    attempts = [_normalize_attempt(dict(attempt)) for attempt in attempts_input]
    tasks = list(payload.get("tasks", []))
    final_outcome = _infer_final_outcome(attempts, payload.get("final_outcome"))
    replay = _infer_replay(attempts, payload)
    return {
        "artifact": "agent_attempt_ledger",
        "suite": "agent_loop",
        "generated_at_epoch_s": float(payload.get("generated_at_epoch_s", time.time())),
        "task_id": str(payload.get("task_id") or tasks[0]["task_id"]),
        "root": str(payload["root"]),
        "tasks": tasks,
        "attempts": attempts,
        "final_outcome": final_outcome,
        "replay": replay,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_attempt_ledger_payload(load_input_payload(args.input))
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
