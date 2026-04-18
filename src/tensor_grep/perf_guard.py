from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SUITE_TIME_KEYS: dict[str, tuple[str, ...]] = {
    "run_benchmarks": ("tg_time_s",),
    "run_hot_query_benchmarks": ("first_s", "second_s"),
}


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
        return tuple(key for key in SUITE_TIME_KEYS[suite] if key in row)
    return tuple(
        key
        for key, value in row.items()
        if isinstance(value, (float, int)) and (key.endswith("_time_s") or key.endswith("_s"))
    )


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
    regressions: list[str] = []
    suite = current.get("suite") or baseline.get("suite")
    baseline_rows = {row["name"]: row for row in baseline.get("rows", [])}
    current_rows = {row["name"]: row for row in current.get("rows", [])}

    for name, cur in current_rows.items():
        base = baseline_rows.get(name)
        if not base:
            continue
        for metric_key in iter_regression_time_keys(suite if isinstance(suite, str) else None, cur):
            cur_time = cur.get(metric_key)
            base_time = base.get(metric_key)
            if not isinstance(cur_time, (float, int)) or not isinstance(base_time, (float, int)):
                continue
            if base_time <= 0:
                continue
            # Tiny baseline durations are noisy on shared CI runners and can
            # trigger false positives from scheduler jitter.
            if float(base_time) < float(min_baseline_time_s):
                continue
            pct_delta = ((float(cur_time) - float(base_time)) / float(base_time)) * 100.0
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
    drift: list[str] = []
    baseline_rows = {row["name"]: row for row in baseline.get("rows", [])}
    current_rows = {row["name"]: row for row in current.get("rows", [])}

    for name, cur in current_rows.items():
        base = baseline_rows.get(name)
        if not base:
            continue
        cur_time = cur.get(comparator_key)
        base_time = base.get(comparator_key)
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
