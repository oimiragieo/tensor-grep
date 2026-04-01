from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score external-agent patch-driver comparison artifacts."
    )
    parser.add_argument(
        "--input", required=True, help="Path to external_agent_patch_driver_comparison.json"
    )
    parser.add_argument("--output", required=True, help="Path to write the scorecard JSON")
    return parser.parse_args(argv)


def load_comparison(path: str | Path) -> dict[str, Any]:
    comparison_path = Path(path).expanduser().resolve()
    payload = json.loads(comparison_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("comparison payload must be an object")
    return payload


def _compactness_score(follow_up_count: int) -> float:
    if follow_up_count <= 5:
        return 1.0
    overflow = follow_up_count - 5
    return round(max(0.0, 1.0 - (overflow / 5.0)), 6)


def _validation_fit(primary_file: str, validation_commands: list[str]) -> str:
    lower_file = primary_file.lower()
    joined = " ".join(validation_commands).lower()
    if lower_file.endswith(".rs"):
        return "strong" if "cargo test" in joined else "weak"
    if lower_file.endswith((".ts", ".tsx", ".js", ".jsx")):
        if any(
            token in joined for token in ("pnpm test", "npm test", "yarn test", "vitest", "jest")
        ):
            return "strong"
        return "weak"
    if lower_file.endswith(".py"):
        if any(token in joined for token in ("pytest", "python -m pytest", "uv run pytest")):
            return "strong"
        return "weak"
    return "unknown"


def _fit_score(validation_fit: str) -> float:
    if validation_fit == "strong":
        return 1.0
    if validation_fit == "unknown":
        return 0.5
    return 0.0


def _parallel_read_reduction_score(follow_up_count: int, parallel_read_group_count: int) -> float:
    if follow_up_count <= 1:
        return 1.0
    if parallel_read_group_count <= 0:
        return 0.0
    saved_steps = max(0, follow_up_count - parallel_read_group_count)
    max_savable = max(1, follow_up_count - 1)
    return round(min(1.0, saved_steps / max_savable), 6)


def build_scorecard_payload(comparison: dict[str, Any]) -> dict[str, Any]:
    systems = [
        dict(system) for system in list(comparison.get("systems", [])) if isinstance(system, dict)
    ]
    by_system: dict[str, dict[str, Any]] = {}
    compactness_scores: list[float] = []
    fit_scores: list[float] = []
    parallel_scores: list[float] = []
    for system in systems:
        system_name = str(system.get("system") or "")
        follow_up_count = int(system.get("follow_up_count") or 0)
        compactness_score = _compactness_score(follow_up_count)
        parallel_read_group_count = int(system.get("parallel_read_group_count") or 0)
        parallel_score = _parallel_read_reduction_score(follow_up_count, parallel_read_group_count)
        validation_commands = [str(item) for item in list(system.get("validation_commands", []))]
        validation_fit = _validation_fit(str(system.get("primary_file") or ""), validation_commands)
        fit_score = _fit_score(validation_fit)
        overall_score = round((compactness_score + fit_score + parallel_score) / 3.0, 6)
        compactness_scores.append(compactness_score)
        fit_scores.append(fit_score)
        parallel_scores.append(parallel_score)
        by_system[system_name] = {
            "primary_file": str(system.get("primary_file") or ""),
            "follow_up_count": follow_up_count,
            "compactness_score": compactness_score,
            "compactness_target_met": follow_up_count <= 5,
            "parallel_read_group_count": parallel_read_group_count,
            "estimated_saved_read_steps": int(system.get("estimated_saved_read_steps") or 0),
            "parallel_read_reduction_score": parallel_score,
            "validation_fit": validation_fit,
            "validation_fit_score": fit_score,
            "validation_commands": validation_commands,
            "overall_score": overall_score,
        }
    mean_compactness = (
        round(sum(compactness_scores) / len(compactness_scores), 6) if compactness_scores else 0.0
    )
    mean_validation_fit = round(sum(fit_scores) / len(fit_scores), 6) if fit_scores else 0.0
    mean_parallel_reduction = (
        round(sum(parallel_scores) / len(parallel_scores), 6) if parallel_scores else 0.0
    )
    return {
        "artifact": "external_agent_patch_driver_scorecard",
        "generated_at_epoch_s": time.time(),
        "input_artifact": str(comparison.get("artifact") or ""),
        "summary": {
            "system_count": len(by_system),
            "mean_compactness_score": mean_compactness,
            "mean_validation_fit_score": mean_validation_fit,
            "mean_parallel_read_reduction_score": mean_parallel_reduction,
            "mean_overall_score": round(
                (mean_compactness + mean_validation_fit + mean_parallel_reduction) / 3.0, 6
            ),
            "next_action": str(
                dict(comparison.get("common_contract", {})).get("next_action") or ""
            ),
        },
        "by_system": by_system,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_scorecard_payload(load_comparison(args.input))
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
