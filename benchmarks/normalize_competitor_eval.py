from __future__ import annotations

import argparse
import json
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

import run_bakeoff  # noqa: E402

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "competitor_eval_normalized.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize manual competitor evaluation records into a common score schema.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    return parser.parse_args()


def _resolve_against_base(path: str, *, base_dir: Path | None) -> str:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and base_dir is not None:
        candidate = base_dir / candidate
    return str(candidate.resolve())


def _load_scenario_lookup(packs: list[str], *, base_dir: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for pack in packs:
        pack_path = Path(_resolve_against_base(pack, base_dir=base_dir))
        for scenario in run_bakeoff.load_scenarios(pack_path):
            scenario_id = str(scenario.get("id", ""))
            if scenario_id:
                lookup[(str(pack_path), scenario_id)] = dict(scenario)
    return lookup


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def _normalize_repo_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed.replace("\\", "/")


def _score_row(record: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    actual = {
        "actual_primary_file": _normalize_repo_relative_path(record.get("actual_primary_file")),
        "actual_primary_span": record.get("actual_primary_span"),
        "actual_dependent_files": [
            item
            for item in (
                _normalize_repo_relative_path(entry) for entry in list(record.get("actual_dependent_files", []))
            )
            if item is not None
        ],
        "actual_suggested_edit_files": [
            item
            for item in (
                _normalize_repo_relative_path(entry)
                for entry in list(record.get("actual_suggested_edit_files", []))
            )
            if item is not None
        ],
        "actual_test_files": [
            item
            for item in (_normalize_repo_relative_path(entry) for entry in list(record.get("actual_test_files", [])))
            if item is not None
        ],
        "actual_validation_commands": list(record.get("actual_validation_commands", [])),
        "context_token_count": int(record.get("context_token_count", 0)),
    }
    scored = run_bakeoff.score_scenario(scenario, actual)
    primary_file_hit = 1.0 if scored.get("expected_primary_file") == scored.get("actual_primary_file") else 0.0
    primary_span_hit = float(scored.get("span_hit_rate", 0.0))
    dependent_expected = set(scored.get("expected_dependent_files", []))
    dependent_actual = set(scored.get("actual_dependent_files", []))
    dependent_hits = len(dependent_expected & dependent_actual)
    dependent_file_recall = dependent_hits / len(dependent_expected) if dependent_expected else 0.0
    validation_quality_score = (
        float(scored.get("test_hit_rate", 0.0)) + float(scored.get("validation_cmd_hit_rate", 0.0))
    ) / 2.0
    edit_accuracy_score = (
        primary_file_hit
        + primary_span_hit
        + dependent_file_recall
        + float(scored.get("file_precision", 0.0))
    ) / 4.0
    context_efficiency_score = min(1000.0 / max(int(record.get("context_token_count", 0)), 1), 1.0)
    overall_score = (
        edit_accuracy_score * 0.6
        + validation_quality_score * 0.3
        + context_efficiency_score * 0.1
    )
    return {
        "system": str(record.get("system", "")),
        "repo": str(record.get("repo", Path(str(scenario["repo_fixture"])).name)),
        "scenario_id": str(record.get("scenario_id", "")),
        "language": str(record.get("language", scenario.get("language", ""))),
        "difficulty": str(record.get("difficulty", "unknown")),
        "primary_file_hit": primary_file_hit,
        "primary_span_hit": primary_span_hit,
        "dependent_file_recall": round(dependent_file_recall, 6),
        "dependent_span_recall": round(dependent_file_recall, 6),
        "test_hit": float(scored.get("test_hit_rate", 0.0)),
        "validation_cmd_hit": float(scored.get("validation_cmd_hit_rate", 0.0)),
        "false_positive_file_count": len(scored.get("false_positive_files", [])),
        "context_token_count": int(record.get("context_token_count", 0)),
        "wall_clock_seconds": float(record.get("wall_clock_seconds", 0.0)),
        "deterministic_repeat_match": bool(record.get("deterministic_repeat_match", False)),
        "notes": str(record.get("notes", "")),
        "edit_accuracy_score": round(edit_accuracy_score, 6),
        "validation_quality_score": round(validation_quality_score, 6),
        "context_efficiency_score": round(context_efficiency_score, 6),
        "overall_score": round(overall_score, 6),
    }


def normalize_competitor_eval(
    payload: dict[str, Any],
    *,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    scenario_packs = [str(item) for item in list(payload.get("scenario_packs", []))]
    records = list(payload.get("records", []))
    resolved_scenario_packs = [_resolve_against_base(pack, base_dir=base_dir) for pack in scenario_packs]
    lookup = _load_scenario_lookup(resolved_scenario_packs, base_dir=base_dir)
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        scenario_pack = _resolve_against_base(str(record.get("scenario_pack", "")), base_dir=base_dir)
        scenario_id = str(record.get("scenario_id", ""))
        scenario = lookup.get((scenario_pack, scenario_id))
        if scenario is None:
            raise ValueError(f"Unknown scenario reference: {scenario_pack}#{scenario_id}")
        rows.append(_score_row(record, scenario))
    systems = sorted({str(row["system"]) for row in rows})
    by_system: dict[str, dict[str, Any]] = {}
    for system in systems:
        current_rows = [row for row in rows if row["system"] == system]
        by_system[system] = {
            "scenario_count": len(current_rows),
            "mean_primary_file_hit": _mean(float(row["primary_file_hit"]) for row in current_rows),
            "mean_primary_span_hit": _mean(float(row["primary_span_hit"]) for row in current_rows),
            "mean_overall_score": _mean(float(row["overall_score"]) for row in current_rows),
            "mean_wall_clock_seconds": _mean(float(row["wall_clock_seconds"]) for row in current_rows),
        }
    return {
        "artifact": "competitor_eval_normalized",
        "suite": "normalize_competitor_eval",
        "generated_at_epoch_s": time.time(),
        "scenario_packs": resolved_scenario_packs,
        "records": rows,
        "by_system": by_system,
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    normalized = normalize_competitor_eval(payload, base_dir=input_path.parent)
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, normalized)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
