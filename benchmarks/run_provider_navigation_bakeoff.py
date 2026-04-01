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


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_provider_navigation.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run provider-mode caller/navigation hard cases.")
    parser.add_argument(
        "--scenarios", required=True, help="Path to the provider hard-case scenarios JSON file."
    )
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument(
        "--providers", default="native,hybrid", help="Comma-separated provider list."
    )
    return parser.parse_args()


def _parse_providers(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        raise ValueError("expected at least one provider")
    allowed = {"native", "lsp", "hybrid"}
    invalid = [item for item in values if item not in allowed]
    if invalid:
        raise ValueError(f"unsupported providers: {', '.join(invalid)}")
    return values


def load_scenarios(path: str | Path) -> list[Scenario]:
    scenarios_path = Path(path).expanduser().resolve()
    payload = json.loads(scenarios_path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios", [])
    if not isinstance(scenarios, list):
        raise ValueError("scenarios must be a list")
    validated: list[Scenario] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise ValueError("scenario entries must be objects")
        current = dict(scenario)
        repo_fixture = current.get("repo_fixture")
        if not isinstance(repo_fixture, str):
            raise ValueError("repo_fixture must be a string")
        if not Path(repo_fixture).is_absolute():
            current["repo_fixture"] = str((scenarios_path.parent / repo_fixture).resolve())
        for field in ("query_or_symbol",):
            if not isinstance(current.get(field), str):
                raise ValueError(f"{field} must be a string")
        for field in ("expected_caller_files", "expected_test_files"):
            value = current.get(field)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{field} must be a list[str]")
        validated.append(current)
    return validated


def run_scenario(scenario: Scenario, *, provider: str) -> ResultRow:
    payload = repo_map.build_symbol_callers(
        str(scenario["query_or_symbol"]),
        Path(str(scenario["repo_fixture"])),
        semantic_provider=provider,
    )
    return {
        "actual_caller_files": _ordered_unique_strings(payload.get("files")),
        "actual_test_files": _ordered_unique_strings(payload.get("tests")),
        "semantic_provider": str(payload.get("semantic_provider", provider)),
    }


def score_scenario(scenario: Scenario, actual: ResultRow) -> ResultRow:
    repo_root = Path(str(scenario["repo_fixture"]))
    expected_caller_files = _ordered_unique_strings(list(scenario.get("expected_caller_files", [])))
    expected_test_files = _ordered_unique_strings(list(scenario.get("expected_test_files", [])))
    actual_caller_files = _ordered_unique_strings(list(actual.get("actual_caller_files", [])))
    actual_test_files = _ordered_unique_strings(list(actual.get("actual_test_files", [])))
    normalized_expected_callers = {
        _normalize_path(current, repo_root) for current in expected_caller_files
    }
    normalized_expected_tests = {
        _normalize_path(current, repo_root) for current in expected_test_files
    }
    normalized_actual_callers = {
        _normalize_path(current, repo_root) for current in actual_caller_files
    }
    normalized_actual_tests = {_normalize_path(current, repo_root) for current in actual_test_files}
    caller_hits = len(normalized_expected_callers & normalized_actual_callers)
    test_hits = len(normalized_expected_tests & normalized_actual_tests)
    return {
        "name": _scenario_name(scenario),
        "repo_fixture": str(repo_root),
        "query_or_symbol": str(scenario["query_or_symbol"]),
        "semantic_provider": str(actual.get("semantic_provider", "native")),
        "expected_caller_files": expected_caller_files,
        "expected_test_files": expected_test_files,
        "actual_caller_files": actual_caller_files,
        "actual_test_files": actual_test_files,
        "caller_hit_rate": _rate_hits(caller_hits, len(normalized_expected_callers)),
        "caller_precision": _rate_precision(caller_hits, len(normalized_actual_callers)),
        "test_hit_rate": _rate_hits(test_hits, len(normalized_expected_tests)),
        "false_positive_caller_files": [
            current
            for current in actual_caller_files
            if _normalize_path(current, repo_root) not in normalized_expected_callers
        ],
    }


def build_summary(rows: list[ResultRow]) -> dict[str, float | int]:
    if not rows:
        return {
            "scenario_count": 0,
            "mean_caller_hit_rate": 0.0,
            "mean_caller_precision": 0.0,
            "mean_test_hit_rate": 0.0,
            "mean_false_positive_caller_count": 0.0,
        }
    return {
        "scenario_count": len(rows),
        "mean_caller_hit_rate": _mean(float(row.get("caller_hit_rate", 0.0)) for row in rows),
        "mean_caller_precision": _mean(float(row.get("caller_precision", 0.0)) for row in rows),
        "mean_test_hit_rate": _mean(float(row.get("test_hit_rate", 0.0)) for row in rows),
        "mean_false_positive_caller_count": _mean(
            float(len(row.get("false_positive_caller_files", []))) for row in rows
        ),
    }


def build_payload(
    rows_by_provider: dict[str, list[ResultRow]],
    *,
    providers: list[str],
    scenarios_path: Path,
) -> dict[str, Any]:
    return {
        "artifact": "bench_provider_navigation",
        "suite": "run_provider_navigation_bakeoff",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "scenarios_path": str(scenarios_path),
        "providers": list(providers),
        "by_provider": {
            provider: {
                "rows": list(rows_by_provider.get(provider, [])),
                **build_summary(list(rows_by_provider.get(provider, []))),
            }
            for provider in providers
        },
    }


def main() -> int:
    args = parse_args()
    scenarios_path = Path(args.scenarios).expanduser().resolve()
    scenarios = load_scenarios(scenarios_path)
    providers = _parse_providers(args.providers)
    rows_by_provider: dict[str, list[ResultRow]] = {}
    for provider in providers:
        rows: list[ResultRow] = []
        for scenario in scenarios:
            rows.append(score_scenario(scenario, run_scenario(scenario, provider=provider)))
        rows_by_provider[provider] = rows
    output_path = Path(args.output).expanduser().resolve()
    write_json(
        output_path,
        build_payload(rows_by_provider, providers=providers, scenarios_path=scenarios_path),
    )
    print(f"Results written to {output_path}")
    return 0


def _scenario_name(scenario: Scenario) -> str:
    fixture_name = Path(str(scenario["repo_fixture"])).name
    return f"{fixture_name}:{scenario['query_or_symbol']}"


def _ordered_unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _normalize_path(path_value: str | None, repo_root: Path) -> str:
    if not path_value:
        return ""
    current_path = Path(path_value)
    if current_path.is_absolute():
        try:
            return current_path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return current_path.resolve().as_posix()
    return current_path.as_posix()


def _rate_hits(hits: int, expected_count: int) -> float:
    if expected_count <= 0:
        return 0.0
    return round(hits / float(expected_count), 6)


def _rate_precision(hits: int, actual_count: int) -> float:
    if actual_count <= 0:
        return 0.0
    return round(hits / float(actual_count), 6)


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(float(item) for item in items) / float(len(items)), 6)


if __name__ == "__main__":
    raise SystemExit(main())
