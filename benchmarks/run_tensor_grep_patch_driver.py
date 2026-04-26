from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import build_attempt_ledger  # noqa: E402

from tensor_grep.cli import repo_map  # noqa: E402
from tensor_grep.perf_guard import write_json  # noqa: E402

Scenario = dict[str, Any]

_ALLOWED_MODES = ("context-render", "blast-radius")
_REQUIRED_FIELDS = ("instance_id", "repo_fixture", "query_or_symbol", "mode", "problem_statement")


class ScenarioValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(json.dumps({"errors": errors}, indent=2))


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "tensor_grep_patch_driver.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build patch-ready tensor-grep prediction bundles."
    )
    parser.add_argument("--scenarios", required=True, help="Path to patch scenario JSON.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument(
        "--attempt-ledger-output",
        default="",
        help="Optional path to write an inferred attempt ledger.",
    )
    parser.add_argument("--provider", default="native", choices=("native", "lsp", "hybrid"))
    parser.add_argument("--max-files", type=int, default=6)
    parser.add_argument("--max-sources", type=int, default=6)
    parser.add_argument("--max-symbols-per-file", type=int, default=6)
    return parser.parse_args()


def load_driver_scenarios(path: str | Path) -> list[Scenario]:
    scenarios_path = Path(path).expanduser().resolve()
    payload = json.loads(scenarios_path.read_text(encoding="utf-8-sig"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ScenarioValidationError(
            [{"field": "scenarios", "code": "invalid_type", "expected": "list"}]
        )
    errors: list[dict[str, Any]] = []
    validated: list[Scenario] = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            errors.append(
                {
                    "scenario_index": index,
                    "field": "scenario",
                    "code": "invalid_type",
                    "expected": "object",
                }
            )
            continue
        current = dict(scenario)
        missing = [field for field in _REQUIRED_FIELDS if field not in current]
        for field in missing:
            errors.append(
                {
                    "scenario_index": index,
                    "field": field,
                    "code": "missing_required_field",
                }
            )
        if missing:
            continue
        if current["mode"] not in _ALLOWED_MODES:
            errors.append(
                {
                    "scenario_index": index,
                    "field": "mode",
                    "code": "invalid_choice",
                    "expected": list(_ALLOWED_MODES),
                }
            )
        repo_fixture = current["repo_fixture"]
        if not isinstance(repo_fixture, str):
            errors.append(
                {
                    "scenario_index": index,
                    "field": "repo_fixture",
                    "code": "invalid_type",
                    "expected": "str",
                }
            )
        elif not Path(repo_fixture).is_absolute():
            current["repo_fixture"] = str((scenarios_path.parent / repo_fixture).resolve())
        validated.append(current)
    if errors:
        raise ScenarioValidationError(errors)
    return validated


def run_tensor_grep_scenario(
    scenario: Scenario,
    *,
    provider: str = "native",
    max_files: int = 6,
    max_sources: int = 6,
    max_symbols_per_file: int = 6,
) -> dict[str, Any]:
    repo_fixture = Path(str(scenario["repo_fixture"]))
    query_or_symbol = str(scenario["query_or_symbol"])
    mode = str(scenario["mode"])
    raw_max_repo_files = scenario.get("max_repo_files")
    max_repo_files = int(raw_max_repo_files) if raw_max_repo_files is not None else None
    raw_include_edit_plan_seed = scenario.get("include_edit_plan_seed")
    include_edit_plan_seed = (
        bool(raw_include_edit_plan_seed)
        if raw_include_edit_plan_seed is not None
        else max_repo_files is None
    )
    if mode == "context-render":
        payload = repo_map.build_context_render(
            query_or_symbol,
            repo_fixture,
            max_repo_files=max_repo_files,
            include_edit_plan_seed=include_edit_plan_seed,
        )
    elif mode == "blast-radius":
        payload = repo_map.build_symbol_blast_radius_render(
            query_or_symbol,
            repo_fixture,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
            semantic_provider=provider,
        )
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    edit_plan_seed = payload.get("edit_plan_seed", {})
    if not isinstance(edit_plan_seed, dict):
        edit_plan_seed = {}
    navigation_pack = payload.get("navigation_pack", {})
    if not isinstance(navigation_pack, dict):
        navigation_pack = {}
    primary_target = navigation_pack.get("primary_target", {})
    if not isinstance(primary_target, dict):
        primary_target = {}
    primary_start = int(primary_target.get("start_line", 0) or 0)
    primary_end = int(primary_target.get("end_line", primary_start) or primary_start)
    fallback_primary_span = (
        {
            "start_line": primary_start,
            "end_line": primary_end,
        }
        if primary_start > 0 and primary_end >= primary_start
        else None
    )
    prompt = build_patch_prompt(scenario, payload)
    return {
        "instance_id": str(scenario["instance_id"]),
        "system": "tensor-grep",
        "repo_fixture": str(repo_fixture),
        "mode": mode,
        "query_or_symbol": query_or_symbol,
        "semantic_provider": str(payload.get("semantic_provider", provider)),
        "actual_primary_file": edit_plan_seed.get("primary_file") or primary_target.get("file"),
        "actual_primary_span": edit_plan_seed.get("primary_span") or fallback_primary_span,
        "actual_dependent_files": list(edit_plan_seed.get("dependent_files", [])),
        "actual_suggested_edit_files": [
            current.get("file")
            for current in list(edit_plan_seed.get("suggested_edits", []))
            if isinstance(current, dict)
        ],
        "actual_test_files": list(
            edit_plan_seed.get(
                "validation_tests",
                navigation_pack.get("related_tests", payload.get("tests", [])),
            )
        ),
        "actual_validation_commands": list(
            edit_plan_seed.get(
                "validation_commands", navigation_pack.get("validation_commands", [])
            )
        ),
        "rendered_context": str(payload.get("rendered_context", "")),
        "edit_plan_seed": edit_plan_seed,
        "navigation_pack": navigation_pack,
        "token_estimate": int(payload.get("token_estimate", 0)),
        "prompt": prompt,
        "problem_statement": str(scenario["problem_statement"]),
    }


def build_patch_prompt(scenario: Scenario, payload: dict[str, Any]) -> str:
    rendered_context = str(payload.get("rendered_context", "")).strip()
    problem_statement = str(scenario["problem_statement"]).strip()
    return "\n\n".join(
        [
            "You are preparing a repository patch.",
            "Apply the smallest correct repository change for the problem statement.",
            "Prefer editing the repository files directly. If you do that, do not create unrelated files.",
            "If you choose not to edit files directly, return a git-style unified diff patch only. Do not include prose.",
            "Make the patch safe for git apply: include diff --git headers and enough unchanged context lines around every edit.",
            "Do not emit fragile one-line hunks. Include the full surrounding block when needed so the patch applies cleanly.",
            "Do not run the test suite or create caches like .pytest_cache.",
            f"Problem statement:\n{problem_statement}",
            f"Context:\n{rendered_context}",
        ]
    ).strip()


def build_payload(
    scenarios: list[Scenario],
    *,
    provider: str = "native",
    max_files: int = 6,
    max_sources: int = 6,
    max_symbols_per_file: int = 6,
) -> dict[str, Any]:
    records = [
        run_tensor_grep_scenario(
            scenario,
            provider=provider,
            max_files=max_files,
            max_sources=max_sources,
            max_symbols_per_file=max_symbols_per_file,
        )
        for scenario in scenarios
    ]
    return {
        "suite": "run_tensor_grep_patch_driver",
        "artifact": "tensor_grep_patch_driver",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "semantic_provider": provider,
        "records": records,
    }


def build_attempt_ledger_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    records = [
        dict(record) for record in list(payload.get("records", [])) if isinstance(record, dict)
    ]
    if not records:
        raise ValueError("patch driver payload missing records")
    first = records[0]
    task_id = str(first.get("instance_id") or "tensor-grep-task")
    root = str(first.get("repo_fixture") or ROOT_DIR)
    attempts: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for record in records:
        instance_id = str(record.get("instance_id") or "")
        attempt_id = f"{instance_id}:tensor-grep"
        attempts.append(
            {
                "attempt_id": attempt_id,
                "parent_attempt_id": None,
                "kind": "tensor_grep_patch_driver",
                "status": "accepted",
                "retryable": False,
                "retry_stage": "none",
                "retry_reason": "accepted",
                "checkpoint_id": None,
                "audit_manifest_path": None,
                "validation_success": True,
                "score_artifact": None,
                "session_id": None,
                "inputs": [str(record.get("repo_fixture") or "")],
                "outputs": [str(record.get("prompt") or "")],
            }
        )
        tasks.append(
            {
                "task_id": instance_id,
                "status": "accepted",
                "accepted_attempt_id": attempt_id,
            }
        )
    ledger_input = {
        "task_id": task_id,
        "root": root,
        "tasks": tasks if len(tasks) > 1 else [],
        "attempts": attempts,
        "final_outcome": {
            "status": "accepted",
            "accepted_attempt_id": attempts[-1]["attempt_id"],
            "score_artifact": None,
            "summary": "Accepted tensor-grep patch-driver prompt bundle.",
        },
        "replay": {
            "preserve_attempt_ids": True,
            "partial_retry_ledger": [],
            "audit_chain": [],
            "next_action": "run patch system",
            "multi_task": len(tasks) > 1,
            "task_chain": [task["task_id"] for task in tasks] if len(tasks) > 1 else [],
        },
    }
    return build_attempt_ledger.build_attempt_ledger_payload(ledger_input)


def main() -> int:
    args = parse_args()
    scenarios = load_driver_scenarios(args.scenarios)
    payload = build_payload(
        scenarios,
        provider=args.provider,
        max_files=args.max_files,
        max_sources=args.max_sources,
        max_symbols_per_file=args.max_symbols_per_file,
    )
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)
    if args.attempt_ledger_output:
        ledger_path = Path(args.attempt_ledger_output).expanduser().resolve()
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(ledger_path, build_attempt_ledger_for_payload(payload))
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
