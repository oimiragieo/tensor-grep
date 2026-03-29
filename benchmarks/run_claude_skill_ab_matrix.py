from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import run_claude_skill_ab as ab_runner  # noqa: E402
import run_patch_bakeoff as patch_bakeoff  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "claude_skill_ab_matrix.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a matrix of Claude baseline vs tensor-grep-enhanced prompt contracts."
    )
    parser.add_argument("--input", required=True, help="Path to tensor-grep patch driver JSON.")
    parser.add_argument("--scenarios", required=True, help="Path to patch bakeoff scenarios JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--model", default="")
    parser.add_argument("--permission-mode", default="bypassPermissions")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--skill-dir", default=str(ab_runner.DEFAULT_SKILL_DIR))
    parser.add_argument("--work-root", default=str(ab_runner.DEFAULT_WORK_ROOT))
    parser.add_argument("--output-contracts", default="standard,terse")
    parser.add_argument("--task-contracts", default="standard,engage")
    parser.add_argument("--resume", action="store_true", help="Resume from an existing matrix output artifact.")
    return parser.parse_args()


def _parse_contract_values(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("expected at least one contract value")
    return values


def build_experiment_configs(output_contracts: list[str], task_contracts: list[str]) -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    for output_contract in output_contracts:
        for task_contract in task_contracts:
            configs.append(
                {
                    "name": f"output-{output_contract}__task-{task_contract}",
                    "enhanced_output_contract": output_contract,
                    "enhanced_task_contract": task_contract,
                }
            )
    return configs


def _mean_numeric(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / float(len(values)), 6)


def summarize_trace_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_system: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_system.setdefault(str(row.get("system", "unknown")), []).append(row)
    summary: dict[str, dict[str, Any]] = {}
    for system, system_rows in sorted(by_system.items()):
        response_counts = Counter(str(row.get("response_shape", "unknown")) for row in system_rows)
        summary[system] = {
            "record_count": len(system_rows),
            "response_shape_counts": dict(response_counts),
            "meta_question_rate": round(
                sum(1.0 for row in system_rows if bool(row.get("asked_meta_question", False))) / float(len(system_rows)),
                6,
            ),
            "mean_first_tg_seconds": _mean_numeric(
                [float(value) for row in system_rows if (value := row.get("first_tg_seconds")) is not None]
            ),
            "mean_first_patch_seconds": _mean_numeric(
                [float(value) for row in system_rows if (value := row.get("first_patch_seconds")) is not None]
            ),
            "mean_first_file_change_seconds": _mean_numeric(
                [float(value) for row in system_rows if (value := row.get("first_file_change_seconds")) is not None]
            ),
            "mean_post_edit_deliberation_seconds": _mean_numeric(
                [float(value) for row in system_rows if (value := row.get("post_edit_deliberation_seconds")) is not None]
            ),
            "mean_tg_invocation_count": _mean_numeric(
                [float(row.get("tg_invocation_count", 0.0)) for row in system_rows]
            ),
            "mean_tg_seconds_total": _mean_numeric(
                [float(row.get("tg_seconds_total", 0.0)) for row in system_rows]
            ),
            "mean_changed_file_count": _mean_numeric(
                [float(row.get("changed_file_count", 0.0)) for row in system_rows]
            ),
        }
    return summary


def summarize_score_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_system: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_system.setdefault(str(row.get("system", "unknown")), []).append(row)
    summary: dict[str, dict[str, Any]] = {}
    for system, system_rows in sorted(by_system.items()):
        summary[system] = {
            "record_count": len(system_rows),
            "mean_patch_applied_rate": _mean_numeric(
                [float(row.get("patch_applied", 0.0)) for row in system_rows]
            ),
            "mean_validation_pass_rate": _mean_numeric(
                [float(row.get("validation_passed", 0.0)) for row in system_rows]
            ),
            "mean_primary_file_hit_rate": _mean_numeric(
                [float(row.get("primary_file_hit", 0.0)) for row in system_rows]
            ),
            "mean_primary_span_hit_rate": _mean_numeric(
                [float(row.get("primary_span_hit", 0.0)) for row in system_rows]
            ),
        }
    return summary


def build_partial_payload(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "artifact": "claude_skill_ab_matrix",
        "suite": "run_claude_skill_ab_matrix",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "experiment_count": len(experiments),
        "experiments": experiments,
    }


def load_existing_experiments(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    experiments = payload.get("experiments", [])
    if not isinstance(experiments, list):
        return []
    return [dict(experiment) for experiment in experiments if isinstance(experiment, dict)]


def write_checkpoint(output_path: Path, experiments: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, build_partial_payload(experiments))


def build_matrix_payload(
    *,
    input_path: Path,
    scenarios_path: Path,
    model: str,
    permission_mode: str,
    timeout_seconds: int,
    skill_dir: Path,
    work_root: Path,
    limit: int,
    output_contracts: list[str],
    task_contracts: list[str],
    output_path: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    driver_payload = ab_runner.load_driver_payload(input_path)
    scenarios = patch_bakeoff.load_patch_scenarios(scenarios_path)
    experiments: list[dict[str, Any]] = []
    completed_names: set[str] = set()
    if resume and output_path is not None:
        experiments = load_existing_experiments(output_path)
        completed_names = {str(experiment.get("name", "")) for experiment in experiments}
    for config in build_experiment_configs(output_contracts, task_contracts):
        if config["name"] in completed_names:
            continue
        ab_payload = ab_runner.build_payload(
            driver_payload,
            model=model,
            permission_mode=permission_mode,
            timeout_seconds=timeout_seconds,
            skill_dir=skill_dir,
            work_root=work_root,
            enhanced_output_contract=config["enhanced_output_contract"],
            enhanced_task_contract=config["enhanced_task_contract"],
            limit=limit,
        )
        bakeoff_payload = patch_bakeoff.build_patch_bakeoff_payload(scenarios, list(ab_payload.get("records", [])))
        experiments.append(
            {
                **config,
                "prediction_record_count": len(list(ab_payload.get("records", []))),
                "trace_record_count": len(list(ab_payload.get("trace_records", []))),
                "trace_summary": summarize_trace_rows(list(ab_payload.get("trace_records", []))),
                "bakeoff_summary": dict(bakeoff_payload.get("summary", {})),
                "system_score_summary": summarize_score_rows(list(bakeoff_payload.get("rows", []))),
            }
        )
        if output_path is not None:
            write_checkpoint(output_path, experiments)
    return build_partial_payload(experiments)


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    payload = build_matrix_payload(
        input_path=Path(args.input).expanduser().resolve(),
        scenarios_path=Path(args.scenarios).expanduser().resolve(),
        model=args.model,
        permission_mode=args.permission_mode,
        timeout_seconds=args.timeout_seconds,
        skill_dir=Path(args.skill_dir).expanduser().resolve(),
        work_root=Path(args.work_root).expanduser().resolve(),
        limit=args.limit,
        output_contracts=_parse_contract_values(args.output_contracts),
        task_contracts=_parse_contract_values(args.task_contracts),
        output_path=output_path,
        resume=args.resume,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
