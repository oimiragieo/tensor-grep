from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_JOIN_PATH = Path(__file__).resolve().parent / "agent_outcome_join.py"
_MISSING = object()


def _load_outcome_join_module() -> Any:
    """Load the sibling join module while restoring the caller's module table."""
    spec = importlib.util.spec_from_file_location("agent_outcome_join", _JOIN_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load the outcome-join module from {_JOIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    previous: object = sys.modules.get(spec.name, _MISSING)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is _MISSING:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = cast(ModuleType, previous)
    return module


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


def _outcome_state(system: dict[str, Any]) -> str:
    """Return observed validation state separately from planned command fit."""
    outcome = system.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("execution_observed") is not True:
        return "unavailable"
    return "passed" if outcome.get("validation_passed") is True else "failed"


def build_scorecard_payload(comparison: dict[str, Any]) -> dict[str, Any]:
    if "systems" not in comparison:
        systems_input: Any = []
    else:
        systems_input = comparison["systems"]
        if not isinstance(systems_input, list):
            raise TypeError("systems must be a list")
    systems: list[dict[str, Any]] = []
    for system in systems_input:
        if not isinstance(system, dict):
            raise TypeError("systems entries must be objects")
        systems.append(dict(system))
    seen_system_names: set[str] = set()
    for system in systems:
        system_name = str(system.get("system") or "")
        if system_name in seen_system_names:
            raise ValueError(f"duplicate system name: {system_name!r}")
        seen_system_names.add(system_name)
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
        outcome_state = _outcome_state(system)
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
            "outcome_state": outcome_state,
            "verified_task_success": outcome_state == "passed",
            "overall_score": overall_score,
        }
    mean_compactness = (
        round(sum(compactness_scores) / len(compactness_scores), 6) if compactness_scores else 0.0
    )
    mean_validation_fit = round(sum(fit_scores) / len(fit_scores), 6) if fit_scores else 0.0
    mean_parallel_reduction = (
        round(sum(parallel_scores) / len(parallel_scores), 6) if parallel_scores else 0.0
    )
    outcome_states = [entry["outcome_state"] for entry in by_system.values()]
    complete_outcomes = [state for state in outcome_states if state != "unavailable"]
    success_count = sum(1 for state in complete_outcomes if state == "passed")
    if "outcome_join" not in comparison:
        join_inputs = {}
    else:
        join_inputs = comparison["outcome_join"]
        if not isinstance(join_inputs, dict):
            raise TypeError("outcome_join must be an object")
    predictions = join_inputs.get("predictions", [])
    if not isinstance(predictions, list):
        raise TypeError("outcome_join predictions must be a list")
    outcomes = join_inputs.get("outcomes", [])
    if not isinstance(outcomes, list):
        raise TypeError("outcome_join outcomes must be a list")
    join_module = _load_outcome_join_module()
    outcome_join = join_module.build_outcome_join_report(
        predictions=predictions,
        outcomes=outcomes,
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
            "complete_outcome_systems": len(complete_outcomes),
            "incomplete_outcome_systems": len(outcome_states) - len(complete_outcomes),
            "verified_task_success_systems": success_count,
            "verified_task_success_rate": (
                round(success_count / len(complete_outcomes), 6) if complete_outcomes else None
            ),
            "next_action": str(
                dict(comparison.get("common_contract", {})).get("next_action") or ""
            ),
        },
        "by_system": by_system,
        "outcome_join": outcome_join,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if input_path == output_path:
        raise ValueError("input and output paths must differ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    temporary_path: Path | None = None
    try:
        payload = build_scorecard_payload(load_comparison(input_path))
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(json.dumps(payload, indent=2) + "\n")
        temporary_path.replace(output_path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
