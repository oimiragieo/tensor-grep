from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tensor_grep.perf_guard import write_json  # noqa: E402

Scenario = dict[str, Any]
Prediction = dict[str, Any]
ResultRow = dict[str, Any]

_REQUIRED_SCENARIO_FIELDS = (
    "instance_id",
    "repo_fixture",
    "expected_primary_file",
    "expected_primary_span",
    "expected_changed_files",
    "expected_test_files",
    "validation_commands",
    "expected_validation_commands_contain",
)


class ScenarioValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(json.dumps({"errors": errors}, indent=2))


class PredictionValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(json.dumps({"errors": errors}, indent=2))


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_patch_bakeoff.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run patch-correctness bakeoff scenarios.")
    parser.add_argument("--scenarios", required=True, help="Path to the patch scenario JSON file.")
    parser.add_argument("--predictions", required=True, help="Path to JSON or JSONL patch predictions.")
    parser.add_argument("--output", default=str(default_output_path()))
    return parser.parse_args()


def load_patch_scenarios(path: str | Path) -> list[Scenario]:
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
            errors.append({"scenario_index": index, "field": "scenario", "code": "invalid_type", "expected": "object"})
            continue
        current = dict(scenario)
        missing = [field for field in _REQUIRED_SCENARIO_FIELDS if field not in current]
        for field in missing:
            errors.append({"scenario_index": index, "field": field, "code": "missing_required_field"})
        if missing:
            continue
        if not isinstance(current["instance_id"], str):
            errors.append({"scenario_index": index, "field": "instance_id", "code": "invalid_type", "expected": "str"})
        repo_fixture = current["repo_fixture"]
        if not isinstance(repo_fixture, str):
            errors.append({"scenario_index": index, "field": "repo_fixture", "code": "invalid_type", "expected": "str"})
        elif not Path(repo_fixture).is_absolute():
            current["repo_fixture"] = str((scenarios_path.parent / repo_fixture).resolve())
        if current["expected_primary_file"] is not None and not isinstance(current["expected_primary_file"], str):
            errors.append(
                {"scenario_index": index, "field": "expected_primary_file", "code": "invalid_type", "expected": "str | null"}
            )
        if current["expected_primary_span"] is not None and not _valid_span(current["expected_primary_span"]):
            errors.append({"scenario_index": index, "field": "expected_primary_span", "code": "invalid_shape"})
        for field in ("expected_changed_files", "expected_test_files", "validation_commands", "expected_validation_commands_contain"):
            value = current[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append({"scenario_index": index, "field": field, "code": "invalid_type", "expected": "list[str]"})
        validated.append(current)
    if errors:
        raise ScenarioValidationError(errors)
    return validated


def load_patch_predictions(path: str | Path) -> list[Prediction]:
    predictions_path = Path(path).expanduser().resolve()
    raw = predictions_path.read_text(encoding="utf-8")
    errors: list[dict[str, Any]] = []
    records: list[Any]
    if predictions_path.suffix.lower() == ".jsonl":
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    else:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            records = list(payload.get("records", []))
        else:
            records = payload
    if not isinstance(records, list):
        raise PredictionValidationError([{"field": "records", "code": "invalid_type", "expected": "list"}])
    validated: list[Prediction] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append({"prediction_index": index, "field": "record", "code": "invalid_type", "expected": "object"})
            continue
        current = dict(record)
        for field in ("instance_id", "system"):
            if not isinstance(current.get(field), str):
                errors.append({"prediction_index": index, "field": field, "code": "invalid_type", "expected": "str"})
        if current.get("model_patch") is not None and not isinstance(current.get("model_patch"), str):
            errors.append({"prediction_index": index, "field": "model_patch", "code": "invalid_type", "expected": "str | null"})
        if not isinstance(current.get("actual_validation_commands", []), list) or not all(
            isinstance(item, str) for item in current.get("actual_validation_commands", [])
        ):
            errors.append(
                {
                    "prediction_index": index,
                    "field": "actual_validation_commands",
                    "code": "invalid_type",
                    "expected": "list[str]",
                }
            )
        validated.append(current)
    if errors:
        raise PredictionValidationError(errors)
    return validated


def evaluate_prediction(scenario: Scenario, prediction: Prediction) -> ResultRow:
    repo_root = Path(str(scenario["repo_fixture"])).resolve()
    patch_text = str(prediction.get("model_patch") or "")
    touched_files = _files_in_patch(patch_text)
    changed_lines = {
        _normalize_path(path, repo_root): lines for path, lines in _changed_lines_by_file(patch_text).items()
    }
    validation_commands = [str(command) for command in list(scenario.get("validation_commands", []))]
    predicted_validation_commands = [str(command) for command in list(prediction.get("actual_validation_commands", []))]

    patch_applied = False
    validation_passed = False
    validation_results: list[dict[str, Any]] = []
    apply_error = ""
    if patch_text.strip():
        with tempfile.TemporaryDirectory(prefix="tg_patch_bakeoff_") as tmp_dir:
            worktree = Path(tmp_dir) / "repo"
            shutil.copytree(repo_root, worktree)
            subprocess.run(["git", "init", "-q"], cwd=worktree, check=False, capture_output=True)
            patch_path = worktree / "candidate.patch"
            patch_path.write_text(patch_text, encoding="utf-8")
            applied = subprocess.run(
                [
                    "git",
                    "apply",
                    "--ignore-space-change",
                    "--ignore-whitespace",
                    "--whitespace=nowarn",
                    str(patch_path),
                ],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            patch_applied = applied.returncode == 0
            if not patch_applied:
                apply_error = (applied.stderr or applied.stdout or "").strip()
            if patch_applied:
                validation_passed = True
                for command in validation_commands:
                    completed = subprocess.run(
                        command,
                        cwd=worktree,
                        shell=True,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    passed = completed.returncode == 0
                    validation_passed = validation_passed and passed
                    validation_results.append(
                        {
                            "command": command,
                            "passed": passed,
                            "returncode": completed.returncode,
                        }
                    )

    expected_files = {_normalize_path(path, repo_root) for path in list(scenario.get("expected_changed_files", []))}
    actual_files = {_normalize_path(path, repo_root) for path in touched_files}
    file_hits = expected_files & actual_files
    expected_tests = {_normalize_path(path, repo_root) for path in list(scenario.get("expected_test_files", []))}
    predicted_tests = {_normalize_path(path, repo_root) for path in list(prediction.get("actual_test_files", []))}
    expected_primary_file = scenario.get("expected_primary_file")
    primary_file_hit = float(
        expected_primary_file is not None and _normalize_path(str(expected_primary_file), repo_root) in actual_files
    )
    primary_span_hit = 0.0
    if primary_file_hit and scenario.get("expected_primary_span") is not None and expected_primary_file is not None:
        primary_span_hit = float(
            _span_overlaps_changed_lines(
                dict(scenario["expected_primary_span"]),
                changed_lines.get(_normalize_path(str(expected_primary_file), repo_root), set()),
            )
        )
    validation_cmd_hit_rate = _validation_cmd_hit_rate(
        list(scenario.get("expected_validation_commands_contain", [])),
        predicted_validation_commands,
    )
    return {
        "instance_id": str(scenario["instance_id"]),
        "system": str(prediction["system"]),
        "patch_applied": patch_applied,
        "validation_passed": validation_passed,
        "apply_error": apply_error,
        "actual_changed_files": sorted(actual_files),
        "primary_file_hit": primary_file_hit,
        "primary_span_hit": primary_span_hit,
        "changed_file_recall": _safe_ratio(len(file_hits), len(expected_files)),
        "changed_file_precision": 1.0 if not actual_files else _safe_ratio(len(file_hits), len(actual_files)),
        "unexpected_files_touched": sorted(actual_files - expected_files),
        "predicted_test_hit_rate": _safe_ratio(len(expected_tests & predicted_tests), len(expected_tests)),
        "predicted_validation_cmd_hit_rate": validation_cmd_hit_rate,
        "validation_results": validation_results,
    }


def build_patch_bakeoff_payload(scenarios: list[Scenario], predictions: list[Prediction]) -> dict[str, Any]:
    prediction_by_id = {str(record["instance_id"]): record for record in predictions}
    rows: list[ResultRow] = []
    missing_predictions: list[str] = []
    for scenario in scenarios:
        instance_id = str(scenario["instance_id"])
        prediction = prediction_by_id.get(instance_id)
        if prediction is None:
            missing_predictions.append(instance_id)
            continue
        rows.append(evaluate_prediction(scenario, prediction))
    summary = {
        "scenario_count": len(rows),
        "missing_predictions": missing_predictions,
        "mean_patch_applied_rate": _mean(rows, "patch_applied"),
        "mean_validation_pass_rate": _mean(rows, "validation_passed"),
        "mean_primary_file_hit_rate": _mean(rows, "primary_file_hit"),
        "mean_primary_span_hit_rate": _mean(rows, "primary_span_hit"),
        "mean_changed_file_recall": _mean(rows, "changed_file_recall"),
        "mean_changed_file_precision": _mean(rows, "changed_file_precision"),
        "mean_predicted_test_hit_rate": _mean(rows, "predicted_test_hit_rate"),
        "mean_predicted_validation_cmd_hit_rate": _mean(rows, "predicted_validation_cmd_hit_rate"),
    }
    return {
        "suite": "run_patch_bakeoff",
        "artifact": "bench_patch_bakeoff",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        },
        "summary": summary,
        "rows": rows,
    }


def _valid_span(value: object) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("start_line"), int)
        and isinstance(value.get("end_line"), int)
        and int(value["start_line"]) > 0
        and int(value["end_line"]) >= int(value["start_line"])
    )


def _normalize_path(path: str, repo_root: Path) -> str:
    current = Path(path)
    if not current.is_absolute():
        current = (repo_root / current).resolve()
    else:
        current = current.resolve()
    return str(current)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def _mean(rows: list[ResultRow], key: str) -> float:
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        total += float(row.get(key, 0.0))
    return round(total / float(len(rows)), 6)


def _files_in_patch(patch_text: str) -> list[str]:
    files: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/") :]
            if current != "/dev/null" and current not in files:
                files.append(current)
    return files


def _changed_lines_by_file(patch_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = {}
    current_file: str | None = None
    current_line = 0
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/") :]
            changed.setdefault(current_file, set())
            continue
        if line.startswith("@@"):
            parts = line.split(" ")
            new_part = next((part for part in parts if part.startswith("+")), None)
            if new_part is None:
                continue
            start_text = new_part[1:].split(",", 1)[0]
            current_line = int(start_text or "0")
            continue
        if current_file is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            changed[current_file].add(current_line)
            current_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            continue
        else:
            current_line += 1
    return changed


def _span_overlaps_changed_lines(span: dict[str, Any], changed_lines: set[int]) -> bool:
    start_line = int(span["start_line"])
    end_line = int(span["end_line"])
    return any(start_line <= line <= end_line for line in changed_lines)


def _validation_cmd_hit_rate(expected_substrings: list[str], actual_commands: list[str]) -> float:
    if not expected_substrings:
        return 0.0
    hits = 0
    for expected in expected_substrings:
        if any(expected in command for command in actual_commands):
            hits += 1
    return _safe_ratio(hits, len(expected_substrings))


def main() -> int:
    args = parse_args()
    scenarios = load_patch_scenarios(args.scenarios)
    predictions = load_patch_predictions(args.predictions)
    payload = build_patch_bakeoff_payload(scenarios, predictions)
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, payload)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
