from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a markdown scorecard from patch bakeoff artifacts.")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 6)


def render_patch_scorecard(payloads: list[dict[str, Any]]) -> str:
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(list(payload.get("rows", [])))
    by_system: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_system[str(row.get("system", ""))].append(dict(row))

    lines = [
        "# Patch Evaluation Scorecard",
        "",
        f"- Systems: `{len(by_system)}`",
        f"- Records: `{len(rows)}`",
        "",
        "## System Summary",
    ]
    ordered = sorted(
        by_system.items(),
        key=lambda item: (
            -_mean([float(row.get("patch_applied", 0.0)) for row in item[1]]),
            -_mean([float(row.get("validation_passed", 0.0)) for row in item[1]]),
            str(item[0]),
        ),
    )
    for system, system_rows in ordered:
        lines.append(
            "- "
            f"`{system}`: "
            f"patch_applied=`{_mean([float(row.get('patch_applied', 0.0)) for row in system_rows])}` "
            f"validation_passed=`{_mean([float(row.get('validation_passed', 0.0)) for row in system_rows])}` "
            f"primary_file=`{_mean([float(row.get('primary_file_hit', 0.0)) for row in system_rows])}` "
            f"primary_span=`{_mean([float(row.get('primary_span_hit', 0.0)) for row in system_rows])}` "
            f"changed_file_recall=`{_mean([float(row.get('changed_file_recall', 0.0)) for row in system_rows])}` "
            f"test_hit=`{_mean([float(row.get('predicted_test_hit_rate', 0.0)) for row in system_rows])}` "
            f"validation_cmd_hit=`{_mean([float(row.get('predicted_validation_cmd_hit_rate', 0.0)) for row in system_rows])}`"
        )

    lines.extend(["", "## Failed Applies"])
    failed_rows = [row for row in rows if not bool(row.get("patch_applied", False))]
    if not failed_rows:
        lines.append("- none")
    else:
        for row in failed_rows[:20]:
            apply_error = str(row.get("apply_error", "")).strip() or "no patch emitted"
            lines.append(
                f"- `{row.get('system', '')}` / `{row.get('instance_id', '')}`: "
                f"primary_file=`{row.get('primary_file_hit', 0.0)}` "
                f"primary_span=`{row.get('primary_span_hit', 0.0)}` "
                f"reason=`{apply_error}`"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    payloads = [
        json.loads(Path(current).expanduser().resolve().read_text(encoding="utf-8"))
        for current in args.inputs
    ]
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "suite": "render_patch_scorecard",
        "generated_at_epoch_s": time.time(),
        "inputs": [str(Path(current).expanduser().resolve()) for current in args.inputs],
    }
    output_path.write_text(
        "<!-- " + json.dumps(metadata, sort_keys=True) + " -->\n" + render_patch_scorecard(payloads),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
