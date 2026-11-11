from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

SUITE_TIME_KEYS: dict[str, tuple[str, ...]] = {
    "run_benchmarks": ("tg_time_s",),
    "run_hot_query_benchmarks": ("first_s", "second_s"),
}
RUN_BENCHMARK_SCENARIO_NAMES = (
    "1. Simple String Match",
    "2. Case-Insensitive Match",
    "3. Regex Match",
    "4. Invert Match",
    "5. Count Matches",
    "6. Context Lines (Before & After)",
    "7. Max Count Limit",
    "8. File Glob Filtering",
    "9. Word Boundary",
    "10. Fixed Strings",
)
RUN_BENCHMARK_EXTRA_SCENARIO_NAMES = (
    "11. Native Large File Search",
    "12. Native Many-File Search",
)
RUN_HOT_QUERY_SCENARIO_NAMES = ("repeated_fixed_string", "repeated_regex_native")


def ensure_artifacts_dir(root_dir: Path) -> Path:
    artifacts_dir = root_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def benchmark_host_key(environment: Mapping[str, Any] | None) -> str:
    if not isinstance(environment, Mapping):
        environment = {}

    platform_name = str(environment.get("platform") or "unknown").lower()
    machine_name = str(environment.get("machine") or "unknown").lower()
    python_version = str(environment.get("python_version") or "unknown")
    if python_version != "unknown":
        normalized_python_version = _normalize_python_version(python_version)
        python_version = ".".join(str(part) for part in normalized_python_version)
    return f"{platform_name}:{machine_name}:py{python_version}"


def iter_regression_time_keys(suite: str | None, row: dict[str, Any]) -> tuple[str, ...]:
    if suite and suite in SUITE_TIME_KEYS:
        return SUITE_TIME_KEYS[suite]
    return tuple(
        key
        for key, value in row.items()
        if isinstance(value, (float, int)) and (key.endswith("_time_s") or key.endswith("_s"))
    )


def _validate_timing_value(
    value: Any, name: str, metric_key: str, side: str, *, required: bool = False
) -> None:
    if isinstance(value, bool) or (required and not isinstance(value, (float, int))):
        raise ValueError(f"{side} timing {name}/{metric_key} must be a finite number > 0")
    if isinstance(value, (float, int)):
        try:
            numeric = float(value)
        except OverflowError:
            numeric = math.inf
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{side} timing {name}/{metric_key} must be a finite number > 0")


def _known_suite_rows(payload: dict[str, Any], suite: str, side: str) -> dict[str, dict[str, Any]]:
    rows = payload.get("rows", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{side} rows for {suite} must be an array of objects")
    names = [row.get("name") for row in rows]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError(f"{side} rows for {suite} must have non-empty scenario names")
    if len(names) != len(set(names)):
        raise ValueError(f"{side} rows for {suite} contain duplicate scenario names")

    expected_names = set(
        RUN_BENCHMARK_SCENARIO_NAMES if suite == "run_benchmarks" else RUN_HOT_QUERY_SCENARIO_NAMES
    )
    if suite == "run_benchmarks" and payload.get("native_cpu_forced") is True:
        expected_names.update(RUN_BENCHMARK_EXTRA_SCENARIO_NAMES)
    actual_names = set(names)
    if actual_names != expected_names:
        raise ValueError(
            f"{side} scenario set does not match {suite} "
            f"(missing={sorted(expected_names - actual_names)}, "
            f"unexpected={sorted(actual_names - expected_names)})"
        )
    return {row["name"]: row for row in rows}


def _iter_comparable_regression_timings(
    baseline: dict[str, Any],
    current: dict[str, Any],
    min_baseline_time_s: float,
) -> Iterator[tuple[str, str, float, float]]:
    suite = current.get("suite") or baseline.get("suite")
    suite_name = suite if isinstance(suite, str) else None
    required_keys = SUITE_TIME_KEYS.get(suite_name or "")

    if suite_name is not None and required_keys is not None:
        baseline_rows = _known_suite_rows(baseline, suite_name, "baseline")
        current_rows = _known_suite_rows(current, suite_name, "current")
        if baseline_rows.keys() != current_rows.keys():
            missing_baseline = sorted(current_rows.keys() - baseline_rows.keys())
            missing_current = sorted(baseline_rows.keys() - current_rows.keys())
            raise ValueError(
                "baseline/current scenario sets differ "
                f"(missing baseline={missing_baseline}, missing current={missing_current})"
            )
        for name, cur in current_rows.items():
            base_row = baseline_rows[name]
            for metric_key in required_keys:
                cur_time = cur.get(metric_key)
                base_time = base_row.get(metric_key)
                _validate_timing_value(cur_time, name, metric_key, "current", required=True)
                _validate_timing_value(base_time, name, metric_key, "baseline", required=True)
                if not isinstance(cur_time, (float, int)) or not isinstance(
                    base_time, (float, int)
                ):
                    raise ValueError(f"timing {name}/{metric_key} must be numeric")
                if float(base_time) < float(min_baseline_time_s):
                    continue
                yield name, metric_key, float(base_time), float(cur_time)
        return

    legacy_baseline_rows = {row["name"]: row for row in baseline.get("rows", [])}
    legacy_current_rows = {row["name"]: row for row in current.get("rows", [])}
    for name, cur in legacy_current_rows.items():
        optional_base_row = legacy_baseline_rows.get(name)
        if not optional_base_row:
            continue
        for metric_key in iter_regression_time_keys(suite_name, cur):
            cur_time = cur.get(metric_key)
            base_time = optional_base_row.get(metric_key)
            _validate_timing_value(cur_time, name, metric_key, "current")
            _validate_timing_value(base_time, name, metric_key, "baseline")
            if not isinstance(cur_time, (float, int)) or not isinstance(base_time, (float, int)):
                continue
            if base_time <= 0:
                continue
            # Tiny baseline durations are noisy on shared CI runners.
            if float(base_time) < float(min_baseline_time_s):
                continue
            yield name, metric_key, float(base_time), float(cur_time)


def count_comparable_regression_metrics(
    baseline: dict[str, Any], current: dict[str, Any], min_baseline_time_s: float = 0.2
) -> int:
    return sum(
        1 for _ in _iter_comparable_regression_timings(baseline, current, min_baseline_time_s)
    )


def _validate_regression_threshold(max_regression_pct: float) -> float:
    if isinstance(max_regression_pct, bool) or not isinstance(max_regression_pct, (int, float)):
        raise ValueError("max_regression_pct must be a finite number >= 0")
    try:
        threshold = float(max_regression_pct)
    except OverflowError as exc:
        raise ValueError("max_regression_pct must be a finite number >= 0") from exc
    if not math.isfinite(threshold) or threshold < 0:
        raise ValueError("max_regression_pct must be a finite number >= 0")
    return threshold


def check_regressions(
    baseline: dict[str, Any],
    current: dict[str, Any],
    max_regression_pct: float = 10.0,
    min_baseline_time_s: float = 0.2,
) -> list[str]:
    """
    Compare scenario timings. Returns a list of regression messages.
    Lower times are better.
    """
    max_regression_pct = _validate_regression_threshold(max_regression_pct)
    regressions: list[str] = []
    for name, metric_key, base_time, cur_time in _iter_comparable_regression_timings(
        baseline, current, min_baseline_time_s
    ):
        pct_delta = ((cur_time - base_time) / base_time) * 100.0
        if pct_delta > max_regression_pct:
            regressions.append(
                f"{name}: {metric_key} regressed by {pct_delta:.2f}% "
                f"(baseline={base_time:.3f}s current={cur_time:.3f}s)"
            )
    return regressions


def detect_comparator_drift(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    comparator_key: str = "rg_time_s",
    max_regression_pct: float = 10.0,
    min_baseline_time_s: float = 0.2,
) -> list[str]:
    _validate_regression_threshold(max_regression_pct)
    drift: list[str] = []
    baseline_rows = {row["name"]: row for row in baseline.get("rows", [])}
    current_rows = {row["name"]: row for row in current.get("rows", [])}

    for name, cur in current_rows.items():
        base = baseline_rows.get(name)
        if not base:
            continue
        cur_time = cur.get(comparator_key)
        base_time = base.get(comparator_key)
        _validate_timing_value(cur_time, name, comparator_key, "current")
        _validate_timing_value(base_time, name, comparator_key, "baseline")
        if not isinstance(cur_time, (float, int)) or not isinstance(base_time, (float, int)):
            continue
        if base_time <= 0:
            continue
        if float(base_time) < float(min_baseline_time_s):
            continue
        pct_delta = ((float(cur_time) - float(base_time)) / float(base_time)) * 100.0
        if pct_delta == 0.0:
            continue
        direction = "slower" if pct_delta > 0 else "faster"
        drift.append(
            f"{name}: {comparator_key} comparator drift {direction} by {abs(pct_delta):.2f}% "
            f"(baseline={base_time:.3f}s current={cur_time:.3f}s)"
        )
    return drift


def detect_environment_mismatch(baseline: dict[str, Any], current: dict[str, Any]) -> str | None:
    """
    Return a mismatch description when benchmark environments are both known but incompatible.
    Missing metadata is treated as unknown and does not trigger a mismatch.
    """
    baseline_env = baseline.get("environment")
    current_env = current.get("environment")
    if not isinstance(baseline_env, dict) or not isinstance(current_env, dict):
        return None

    baseline_platform = baseline_env.get("platform")
    current_platform = current_env.get("platform")
    if baseline_platform and current_platform and baseline_platform != current_platform:
        return f"platform mismatch: baseline={baseline_platform} current={current_platform}"

    baseline_machine = baseline_env.get("machine")
    current_machine = current_env.get("machine")
    if baseline_machine and current_machine and baseline_machine != current_machine:
        return f"machine mismatch: baseline={baseline_machine} current={current_machine}"

    baseline_python_version = baseline_env.get("python_version")
    current_python_version = current_env.get("python_version")
    if (
        baseline_python_version
        and current_python_version
        and _normalize_python_version(str(baseline_python_version))
        != _normalize_python_version(str(current_python_version))
    ):
        return (
            "python_version mismatch: "
            f"baseline={baseline_python_version} current={current_python_version}"
        )

    return None


def _normalize_python_version(version: str) -> tuple[int | str, ...]:
    parts = version.split(".")
    normalized: list[int | str] = []
    for part in parts[:2]:
        try:
            normalized.append(int(part))
        except ValueError:
            normalized.append(part)
    return tuple(normalized)
