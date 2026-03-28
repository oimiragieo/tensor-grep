from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a markdown scorecard from normalized competitor evaluation output.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def render_scorecard(payload: dict[str, Any]) -> str:
    by_system = dict(payload.get("by_system", {}))
    lines = [
        "# Competitor Evaluation Scorecard",
        "",
        f"- Systems: `{len(by_system)}`",
        f"- Records: `{len(list(payload.get('records', [])))}`",
        "",
        "## System Summary",
    ]
    ordered_systems = sorted(
        by_system.items(),
        key=lambda item: (-float(dict(item[1]).get("mean_overall_score", 0.0)), str(item[0])),
    )
    for system, metrics in ordered_systems:
        current = dict(metrics)
        lines.append(
            f"- `{system}`: overall=`{current.get('mean_overall_score', 0.0)}` "
            f"primary_file=`{current.get('mean_primary_file_hit', 0.0)}` "
            f"primary_span=`{current.get('mean_primary_span_hit', 0.0)}` "
            f"wall_clock=`{current.get('mean_wall_clock_seconds', 0.0)}`"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_scorecard(payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
