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

from tensor_grep.cli import repo_map  # noqa: E402
from tensor_grep.perf_guard import write_json  # noqa: E402

Scenario = dict[str, Any]
ResultRow = dict[str, Any]

_ALLOWED_MODES = ("context-render", "blast-radius")
_REQUIRED_FIELDS = (
    "repo_fixture",
    "query_or_symbol",
    "mode",
    "expected_primary_file",
    "expected_primary_span",
    "expected_dependent_files",
    "expected_suggested_edit_files",
    "expected_test_files",
    "expected_validation_commands_contain",
)
_LIST_FIELDS = (
    "expected_dependent_files",
    "expected_suggested_edit_files",
    "expected_test_files",
    "expected_validation_commands_contain",
)


class ScenarioValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(json.dumps({"errors": errors}, indent=2))


class DeterminismError(RuntimeError):
    pass


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_bakeoff.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run edit-planning bakeoff scenarios.")
    parser.add_argument("--scenarios", required=True, help="Path to the scenarios JSON file.")
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--profile", action="store_true", help="Include per-scenario profiling output.")
    return parser.parse_args()


def load_scenarios(path: str | Path) -> list[Scenario]:
    scenarios_path = Path(path).expanduser().resolve()
    payload = json.loads(scenarios_path.read_text(encoding="utf-8"))
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

        repo_fixture = current["repo_fixture"]
        query_or_symbol = current["query_or_symbol"]
        mode = current["mode"]
        expected_primary_file = current["expected_primary_file"]
        expected_primary_span = current["expected_primary_span"]

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
        if not isinstance(query_or_symbol, str):
            errors.append(
                {
                    "scenario_index": index,
                    "field": "query_or_symbol",
                    "code": "invalid_type",
                    "expected": "str",
                }
            )
        if mode not in _ALLOWED_MODES:
            errors.append(
                {
                    "scenario_index": index,
                    "field": "mode",
                    "code": "invalid_choice",
                    "expected": list(_ALLOWED_MODES),
                }
            )
        if expected_primary_file is not None and not isinstance(expected_primary_file, str):
            errors.append(
                {
                    "scenario_index": index,
                    "field": "expected_primary_file",
                    "code": "invalid_type",
                    "expected": "str | null",
                }
            )
        if expected_primary_span is not None and not _valid_span(expected_primary_span):
            errors.append(
                {
                    "scenario_index": index,
                    "field": "expected_primary_span",
                    "code": "invalid_shape",
                }
            )

        for field in _LIST_FIELDS:
            value = current[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(
                    {
                        "scenario_index": index,
                        "field": field,
                        "code": "invalid_type",
                        "expected": "list[str]",
                    }
                )

        validated.append(current)

    if errors:
        raise ScenarioValidationError(errors)
    return validated


def run_scenario(scenario: Scenario, *, profile: bool = False) -> ResultRow:
    repo_fixture = Path(str(scenario["repo_fixture"]))
    query_or_symbol = str(scenario["query_or_symbol"])
    mode = str(scenario["mode"])
    if mode == "context-render":
        payload = repo_map.build_context_render(query_or_symbol, repo_fixture, profile=profile)
    elif mode == "blast-radius":
        payload = repo_map.build_symbol_blast_radius_render(query_or_symbol, repo_fixture, profile=profile)
    else:
        raise ValueError(f"Unsupported bakeoff mode: {mode}")

    edit_plan_seed = payload.get("edit_plan_seed", {})
    if not isinstance(edit_plan_seed, dict):
        edit_plan_seed = {}

    result: ResultRow = {
        "actual_primary_file": edit_plan_seed.get("primary_file"),
        "actual_primary_span": edit_plan_seed.get("primary_span"),
        "actual_dependent_files": _ordered_unique_strings(edit_plan_seed.get("dependent_files")),
        "actual_suggested_edit_files": _ordered_unique_strings(
            [
                current.get("file")
                for current in list(edit_plan_seed.get("suggested_edits", []))
                if isinstance(current, dict)
            ]
        ),
        "actual_test_files": _ordered_unique_strings(
            edit_plan_seed.get("validation_tests", payload.get("tests", []))
        ),
        "actual_validation_commands": _ordered_unique_strings(edit_plan_seed.get("validation_commands")),
        "context_token_count": int(payload.get("token_estimate", 0)),
    }
    if profile and "_profiling" in payload:
        result["_profiling"] = payload["_profiling"]
    return result


def score_scenario(scenario: Scenario, actual: ResultRow) -> ResultRow:
    repo_root = Path(str(scenario["repo_fixture"]))
    expected_files = _ordered_unique_strings(
        [
            scenario.get("expected_primary_file"),
            *list(scenario.get("expected_dependent_files", [])),
            *list(scenario.get("expected_suggested_edit_files", [])),
        ]
    )
    actual_files = _ordered_unique_strings(
        [
            actual.get("actual_primary_file"),
            *list(actual.get("actual_dependent_files", [])),
            *list(actual.get("actual_suggested_edit_files", [])),
        ]
    )

    normalized_expected_files = {_normalize_path(path, repo_root) for path in expected_files}
    normalized_actual_files = {_normalize_path(path, repo_root) for path in actual_files}
    normalized_expected_tests = {
        _normalize_path(path, repo_root)
        for path in _ordered_unique_strings(scenario.get("expected_test_files", []))
    }
    normalized_actual_tests = {
        _normalize_path(path, repo_root)
        for path in _ordered_unique_strings(actual.get("actual_test_files", []))
    }
    file_hits = normalized_expected_files & normalized_actual_files
    test_hits = normalized_expected_tests & normalized_actual_tests

    actual_commands = [str(command) for command in list(actual.get("actual_validation_commands", []))]
    expected_command_substrings = [
        str(command) for command in list(scenario.get("expected_validation_commands_contain", []))
    ]
    validation_hits = sum(
        1
        for fragment in expected_command_substrings
        if any(fragment in command for command in actual_commands)
    )

    row: ResultRow = {
        "name": _scenario_name(scenario),
        "repo_fixture": str(repo_root),
        "query_or_symbol": str(scenario["query_or_symbol"]),
        "mode": str(scenario["mode"]),
        "expected_primary_file": scenario.get("expected_primary_file"),
        "expected_primary_span": scenario.get("expected_primary_span"),
        "expected_dependent_files": list(scenario.get("expected_dependent_files", [])),
        "expected_suggested_edit_files": list(scenario.get("expected_suggested_edit_files", [])),
        "expected_test_files": list(scenario.get("expected_test_files", [])),
        "expected_validation_commands_contain": expected_command_substrings,
        "actual_primary_file": actual.get("actual_primary_file"),
        "actual_primary_span": actual.get("actual_primary_span"),
        "actual_dependent_files": list(actual.get("actual_dependent_files", [])),
        "actual_suggested_edit_files": list(actual.get("actual_suggested_edit_files", [])),
        "actual_test_files": list(actual.get("actual_test_files", [])),
        "actual_validation_commands": actual_commands,
        "file_hit_rate": _rate_hits(
            hits=len(file_hits),
            expected_count=len(normalized_expected_files),
            empty_expected_value=0.0,
        ),
        "file_precision": _rate_precision(
            hits=len(file_hits),
            actual_count=len(normalized_actual_files),
            empty_actual_value=1.0,
        ),
        "span_hit_rate": _span_hit_rate(
            scenario.get("expected_primary_span"),
            actual.get("actual_primary_span"),
        ),
        "test_hit_rate": _rate_hits(
            hits=len(test_hits),
            expected_count=len(normalized_expected_tests),
            empty_expected_value=0.0,
        ),
        "validation_cmd_hit_rate": _rate_hits(
            hits=validation_hits,
            expected_count=len(expected_command_substrings),
            empty_expected_value=0.0,
        ),
        "context_token_count": int(actual.get("context_token_count", 0)),
        "false_positive_files": [
            path
            for path in actual_files
            if _normalize_path(path, repo_root) not in normalized_expected_files
        ],
    }
    if "_profiling" in actual:
        row["_profiling"] = actual["_profiling"]
    return row


def evaluate_scenario(scenario: Scenario, *, profile: bool = False) -> ResultRow:
    first = score_scenario(scenario, run_scenario(scenario, profile=profile))
    second = score_scenario(scenario, run_scenario(scenario, profile=profile))
    if _determinism_snapshot(first) != _determinism_snapshot(second):
        raise DeterminismError(f"Scenario was not deterministic: {_scenario_name(scenario)}")
    first["deterministic"] = True
    return first


def build_summary(rows: list[ResultRow]) -> dict[str, float | int]:
    scenario_count = len(rows)
    if scenario_count == 0:
        return {
            "scenario_count": 0,
            "mean_file_hit_rate": 0.0,
            "mean_file_precision": 0.0,
            "mean_span_hit_rate": 0.0,
            "mean_test_hit_rate": 0.0,
            "mean_validation_cmd_hit_rate": 0.0,
            "mean_context_token_count": 0.0,
            "mean_false_positive_file_count": 0.0,
        }

    return {
        "scenario_count": scenario_count,
        "mean_file_hit_rate": _mean(float(row["file_hit_rate"]) for row in rows),
        "mean_file_precision": _mean(float(row["file_precision"]) for row in rows),
        "mean_span_hit_rate": _mean(float(row["span_hit_rate"]) for row in rows),
        "mean_test_hit_rate": _mean(float(row["test_hit_rate"]) for row in rows),
        "mean_validation_cmd_hit_rate": _mean(float(row["validation_cmd_hit_rate"]) for row in rows),
        "mean_context_token_count": _mean(float(row["context_token_count"]) for row in rows),
        "mean_false_positive_file_count": _mean(float(len(row["false_positive_files"])) for row in rows),
    }


def main() -> int:
    args = parse_args()
    try:
        scenarios = load_scenarios(args.scenarios)
    except ScenarioValidationError as exc:
        print(json.dumps({"errors": exc.errors}, indent=2), file=sys.stderr)
        return 2

    rows = [evaluate_scenario(scenario, profile=args.profile) for scenario in scenarios]
    payload = {
        "artifact": "bench_bakeoff",
        "suite": "run_bakeoff",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "repeats": 2,
        "rows": rows,
        "summary": build_summary(rows),
    }
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


def _scenario_name(scenario: Scenario) -> str:
    fixture_name = Path(str(scenario["repo_fixture"])).name
    return f"{fixture_name}:{scenario['mode']}:{scenario['query_or_symbol']}"


def _valid_span(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    keys = {"start_line", "end_line"}
    if set(value) != keys:
        return False
    return isinstance(value.get("start_line"), int) and isinstance(value.get("end_line"), int)


def _ordered_unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        if value in seen:
            continue
        seen.add(value)
        items.append(value)
    return items


def _normalize_path(path_value: str | None, repo_root: Path) -> str:
    if not path_value:
        return ""
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate)


def _rate_hits(*, hits: int, expected_count: int, empty_expected_value: float) -> float:
    if expected_count <= 0:
        return empty_expected_value
    return hits / expected_count


def _rate_precision(*, hits: int, actual_count: int, empty_actual_value: float) -> float:
    if actual_count <= 0:
        return empty_actual_value
    return hits / actual_count


def _span_hit_rate(expected_span: Any, actual_span: Any) -> float:
    if not _valid_span(expected_span) or not _valid_span(actual_span):
        return 0.0
    expected_start = int(expected_span["start_line"])
    expected_end = int(expected_span["end_line"])
    actual_start = int(actual_span["start_line"])
    actual_end = int(actual_span["end_line"])
    return 1.0 if max(expected_start, actual_start) <= min(expected_end, actual_end) else 0.0


def _determinism_snapshot(row: ResultRow) -> ResultRow:
    snapshot = dict(row)
    snapshot.pop("_profiling", None)
    return snapshot


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


if __name__ == "__main__":
    raise SystemExit(main())
