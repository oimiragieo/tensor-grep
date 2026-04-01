from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an external-agent patch-driver comparison artifact."
    )
    parser.add_argument(
        "--summary",
        action="append",
        default=[],
        help="Comparison input in the form system=path/to/validation_summary.json",
    )
    parser.add_argument("--output", required=True, help="Path to write the comparison artifact.")
    return parser.parse_args(argv)


def _parse_summary_argument(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError(f"invalid summary argument: {value!r}")
    system, raw_path = value.split("=", 1)
    normalized_system = system.strip()
    if not normalized_system:
        raise ValueError(f"invalid summary argument: {value!r}")
    return normalized_system, Path(raw_path).expanduser().resolve()


def load_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"summary payload must be an object: {path}")
    return payload


def _load_patch_driver_record(summary_payload: dict[str, Any]) -> dict[str, Any]:
    output_file = str(summary_payload.get("output_file") or "").strip()
    instance_id = str(summary_payload.get("instance_id") or "").strip()
    if not output_file or not instance_id:
        return {}
    output_path = Path(output_file).expanduser().resolve()
    if not output_path.is_file():
        return {}
    payload = json.loads(output_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        return {}
    for record in list(payload.get("records", [])):
        if isinstance(record, dict) and str(record.get("instance_id") or "") == instance_id:
            return dict(record)
    return {}


def build_payload(summaries: list[tuple[str, Path]]) -> dict[str, Any]:
    systems: list[dict[str, Any]] = []
    next_actions: set[str] = set()
    for system, path in summaries:
        payload = load_summary(path)
        record = _load_patch_driver_record(payload)
        follow_up_reads = [str(item) for item in list(payload.get("follow_up_reads", []))]
        validation_commands = [str(item) for item in list(payload.get("validation_commands", []))]
        navigation_pack = (
            dict(record.get("navigation_pack", {}))
            if isinstance(record.get("navigation_pack", {}), dict)
            else {}
        )
        parallel_read_groups = [
            {
                "phase": int(group.get("phase", 0) or 0),
                "label": str(group.get("label", "") or ""),
                "can_parallelize": bool(group.get("can_parallelize", False)),
                "mentions": [str(item) for item in list(group.get("mentions", [])) if str(item)],
                "files": [str(item) for item in list(group.get("files", [])) if str(item)],
                "roles": [str(item) for item in list(group.get("roles", [])) if str(item)],
            }
            for group in list(navigation_pack.get("parallel_read_groups", []))
            if isinstance(group, dict)
        ]
        parallel_phase_count = len(parallel_read_groups)
        estimated_saved_read_steps = (
            max(0, len(follow_up_reads) - parallel_phase_count) if parallel_phase_count > 0 else 0
        )
        next_action = str(payload.get("ledger_next_action") or "run patch system")
        next_actions.add(next_action)
        systems.append({
            "system": system,
            "instance_id": str(payload.get("instance_id") or ""),
            "primary_file": str(payload.get("actual_primary_file") or ""),
            "follow_up_count": len(follow_up_reads),
            "follow_up_reads": follow_up_reads,
            "parallel_read_group_count": parallel_phase_count,
            "parallel_read_groups": parallel_read_groups,
            "estimated_saved_read_steps": estimated_saved_read_steps,
            "validation_commands": validation_commands,
            "summary_artifact": str(path),
        })
    return {
        "artifact": "external_agent_patch_driver_comparison",
        "generated_at_epoch_s": time.time(),
        "systems": systems,
        "common_contract": {
            "next_action": sorted(next_actions)[0] if len(next_actions) == 1 else "mixed",
            "ledger_artifact": "agent_attempt_ledger",
            "record_preserves_navigation_pack": True,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summaries = [_parse_summary_argument(item) for item in args.summary]
    payload = build_payload(summaries)
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
