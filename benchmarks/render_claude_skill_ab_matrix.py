from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a markdown scorecard from Claude A/B matrix artifacts.")
    parser.add_argument("--inputs", nargs="+", required=True, help="One or more matrix JSON artifacts.")
    parser.add_argument("--output", required=True, help="Markdown output path.")
    return parser.parse_args()


def _load_experiments(paths: list[Path]) -> list[dict[str, Any]]:
    experiments: list[dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for experiment in list(payload.get("experiments", [])):
            if isinstance(experiment, dict):
                current = dict(experiment)
                current["_source"] = str(path)
                experiments.append(current)
    return experiments


def _enhanced_score(experiment: dict[str, Any]) -> tuple[float, float, float]:
    score = dict(experiment.get("system_score_summary", {})).get("claude-enhanced", {})
    trace = dict(experiment.get("trace_summary", {})).get("claude-enhanced", {})
    patch_applied = float(score.get("mean_patch_applied_rate", 0.0) or 0.0)
    validation = float(score.get("mean_validation_pass_rate", 0.0) or 0.0)
    post_edit = trace.get("mean_post_edit_deliberation_seconds")
    if post_edit is None:
        post_edit_value = float("inf")
    else:
        post_edit_value = float(post_edit)
    return (-patch_applied, -validation, post_edit_value)


def render_markdown(paths: list[Path]) -> str:
    experiments = _load_experiments(paths)
    ordered = sorted(experiments, key=_enhanced_score)
    lines = [
        "# Claude Skill A/B Matrix",
        "",
        f"- Artifacts: `{len(paths)}`",
        f"- Experiments: `{len(experiments)}`",
        "",
        "## Experiment Summary",
    ]
    for experiment in ordered:
        score = dict(experiment.get("system_score_summary", {})).get("claude-enhanced", {})
        trace = dict(experiment.get("trace_summary", {})).get("claude-enhanced", {})
        lines.append(
            "- "
            f"`{experiment.get('name', 'unknown')}` "
            f"patch_applied=`{score.get('mean_patch_applied_rate', 0.0)}` "
            f"validation=`{score.get('mean_validation_pass_rate', 0.0)}` "
            f"meta_question_rate=`{trace.get('meta_question_rate', 0.0)}` "
            f"first_tg=`{trace.get('mean_first_tg_seconds', None)}` "
            f"post_edit=`{trace.get('mean_post_edit_deliberation_seconds', None)}`"
        )
    if ordered:
        winner = ordered[0]
        lines.extend(
            [
                "",
                "## Recommended Next Default Probe",
                "",
                f"- `{winner.get('name', 'unknown')}` from `{winner.get('_source', 'unknown')}`",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    input_paths = [Path(path).expanduser().resolve() for path in args.inputs]
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(input_paths), encoding="utf-8")
    print(f"Scorecard written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
