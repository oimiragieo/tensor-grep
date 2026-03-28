from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a final world-class evaluation report.")
    parser.add_argument("--external-eval", required=True)
    parser.add_argument("--profiling", required=True)
    parser.add_argument("--competitor", help="Optional normalized competitor evaluation JSON.")
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _format_summary_table(summary: dict[str, Any]) -> list[str]:
    return [
        f"- scenarios: `{summary.get('scenario_count', 0)}`",
        f"- file hit rate: `{summary.get('mean_file_hit_rate', 0.0)}`",
        f"- span hit rate: `{summary.get('mean_span_hit_rate', 0.0)}`",
        f"- file precision: `{summary.get('mean_file_precision', 0.0)}`",
        f"- test hit rate: `{summary.get('mean_test_hit_rate', 0.0)}`",
        f"- validation cmd hit rate: `{summary.get('mean_validation_cmd_hit_rate', 0.0)}`",
        f"- false positive files: `{summary.get('mean_false_positive_file_count', 0.0)}`",
        f"- context token count: `{summary.get('mean_context_token_count', 0.0)}`",
    ]


def render_world_class_report(
    *,
    external_eval: dict[str, Any],
    profiling: dict[str, Any],
    competitor: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# World-Class Evaluation Report",
        "",
        "## External Baseline",
        *_format_summary_table(dict(external_eval.get("summary", {}))),
        "",
        "## By Language",
    ]
    for language, summary in sorted(dict(external_eval.get("by_language", {})).items()):
        lines.append(f"### {language}")
        lines.extend(_format_summary_table(dict(summary)))
        lines.append("")

    lines.extend(["## Dominant Profiling Phases"])
    for phase in list(profiling.get("dominant_phases", []))[:8]:
        current = dict(phase)
        lines.append(
            f"- `{current.get('name', '')}`: elapsed=`{current.get('elapsed_s', 0.0)}` "
            f"avg=`{current.get('avg_elapsed_s', 0.0)}` pct=`{current.get('percent_total_elapsed', 0.0)}`"
        )

    if competitor is None:
        lines.extend(
            [
                "",
                "## Competitor Status",
                "- competitor-normalized input not provided",
                "- manual Claude Code / Aider / OpenHands runs are still required",
            ]
        )
    else:
        lines.extend(["", "## Competitor Summary"])
        for system, metrics in sorted(
            dict(competitor.get("by_system", {})).items(),
            key=lambda item: (-float(dict(item[1]).get("mean_overall_score", 0.0)), str(item[0])),
        ):
            current = dict(metrics)
            lines.append(
                f"- `{system}`: overall=`{current.get('mean_overall_score', 0.0)}` "
                f"primary_file=`{current.get('mean_primary_file_hit', 0.0)}` "
                f"primary_span=`{current.get('mean_primary_span_hit', 0.0)}` "
                f"wall_clock=`{current.get('mean_wall_clock_seconds', 0.0)}`"
            )

    lines.extend(
        [
            "",
            "## Decision",
            "- keep pursuing Python precision; that remains the weakest engineering area",
            "- runtime work should stay benchmark-led because caller_scan/repo_map_build/file_parse still dominate",
            "- competitor comparison is operationally ready but still needs manual external runs",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    external_eval = json.loads(Path(args.external_eval).expanduser().resolve().read_text(encoding="utf-8"))
    profiling = json.loads(Path(args.profiling).expanduser().resolve().read_text(encoding="utf-8"))
    competitor = None
    if args.competitor:
        competitor = json.loads(Path(args.competitor).expanduser().resolve().read_text(encoding="utf-8"))
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": "render_world_class_report",
        "generated_at_epoch_s": time.time(),
        "external_eval": external_eval.get("artifact", ""),
        "profiling": profiling.get("artifact", ""),
        "competitor": competitor.get("artifact", "") if isinstance(competitor, dict) else "",
    }
    metadata = "<!-- " + json.dumps(payload, sort_keys=True) + " -->\n"
    output_path.write_text(
        metadata
        + render_world_class_report(
            external_eval=external_eval,
            profiling=profiling,
            competitor=competitor,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
