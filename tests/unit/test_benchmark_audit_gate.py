from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "benchmark.yml"
WORKFLOW_STEP_NAME = "Check benchmark regressions against platform baseline"
BENCHMARK_SCENARIO_NAMES = (
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


def _workflow_checker_args() -> list[str]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["benchmark"]["steps"]
        if step.get("name") == WORKFLOW_STEP_NAME
    )
    assert step.get("shell") == "bash"
    command = shlex.split(re.sub(r"\\\r?\n[ \t]*", "", step["run"]))
    assert command[:4] == ["uv", "run", "python", "benchmarks/check_regression.py"]
    return command[4:]


def _benchmark(
    environment_platform: str,
    tg_time_s: float,
    scenario_name: str | None = None,
) -> dict[str, object]:
    rows = [
        {"name": name, "tg_time_s": tg_time_s, "rg_time_s": 0.5}
        for name in BENCHMARK_SCENARIO_NAMES
    ]
    if scenario_name is not None:
        rows[0]["name"] = scenario_name
    return {
        "suite": "run_benchmarks",
        "environment": {
            "platform": environment_platform,
            "machine": "test-x86_64",
            "python_version": "3.12",
        },
        "rows": rows,
    }


def _run_workflow_checker(
    tmp_path: Path,
    *,
    baseline_time_s: float | None,
    current_time_s: float,
    baseline_scenario: str | None = None,
    current_scenario: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = _workflow_checker_args()
    assert args[args.index("--baseline") + 1] == "auto"
    assert args[args.index("--max-regression-pct") + 1] == "20.0"
    assert args[args.index("--min-baseline-time-s") + 1] == "0.2"

    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    current_path.parent.mkdir(parents=True)
    current_path.write_text(
        json.dumps(_benchmark("Linux", current_time_s, current_scenario)), encoding="utf-8"
    )
    if baseline_time_s is not None:
        baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
        baseline_path.parent.mkdir(parents=True)
        baseline_path.write_text(
            json.dumps(_benchmark("Linux", baseline_time_s, baseline_scenario)), encoding="utf-8"
        )

    # Keep the parsed workflow checker arguments and replace only its `uv run python` launcher.
    return subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *args,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_workflow_linux_auto_baseline_resolves_ubuntu_and_accepts_threshold_control(
    tmp_path: Path,
) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=1.0,
        current_time_s=1.2,
    )

    assert (tmp_path / "benchmarks/baselines/run_benchmarks.ubuntu.json").is_file()
    assert not (tmp_path / "benchmarks/baselines/run_benchmarks.linux.json").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No benchmark regressions detected." in result.stdout


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "-0.1"])
def test_workflow_checker_rejects_invalid_regression_threshold_cli(
    tmp_path: Path, threshold: str
) -> None:
    current_path = tmp_path / "current.json"
    baseline_path = tmp_path / "baseline.json"
    current_path.write_text(json.dumps(_benchmark("Linux", 1.0)), encoding="utf-8")
    baseline_path.write_text(json.dumps(_benchmark("Linux", 1.0)), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            f"--max-regression-pct={threshold}",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "max_regression_pct must be a finite number >= 0" in result.stderr


def test_workflow_checker_fails_when_auto_baseline_is_missing(tmp_path: Path) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=None,
        current_time_s=1.0,
    )

    assert result.returncode == 2
    assert "Baseline not found" in result.stderr
    assert "run_benchmarks.ubuntu.json" in result.stderr


def test_workflow_checker_fails_over_twenty_percent_regression(tmp_path: Path) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=1.0,
        current_time_s=1.21,
    )

    assert result.returncode == 1
    assert "regressed by 21.00%" in result.stdout


def test_workflow_checker_fails_when_no_timing_metrics_are_comparable(tmp_path: Path) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=1.0,
        current_time_s=1.0,
        baseline_scenario="baseline-only",
        current_scenario="current-only",
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr
    assert "scenario set" in result.stderr
    assert "No benchmark regressions detected." not in result.stdout


@pytest.mark.parametrize("invalid_time", [True, 0.0, -0.1, float("nan"), float("inf")])
def test_workflow_checker_rejects_invalid_current_timing(
    tmp_path: Path, invalid_time: object
) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=1.0,
        current_time_s=invalid_time,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr


@pytest.mark.parametrize("invalid_time", [True, 0.0, -0.1, float("nan"), float("inf")])
def test_workflow_checker_rejects_invalid_baseline_timing(
    tmp_path: Path, invalid_time: object
) -> None:
    result = _run_workflow_checker(
        tmp_path,
        baseline_time_s=invalid_time,
        current_time_s=1.0,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr


def test_workflow_checker_rejects_invalid_comparator_timing(tmp_path: Path) -> None:
    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    current_path.parent.mkdir(parents=True)
    baseline_path.parent.mkdir(parents=True)
    baseline = _benchmark("Linux", 1.0)
    current = _benchmark("Linux", 1.0)
    current["rows"][0]["rg_time_s"] = float("nan")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *_workflow_checker_args(),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr
    assert "No benchmark regressions detected." not in result.stdout


def test_workflow_checker_rejects_invalid_metric_even_when_another_is_comparable(
    tmp_path: Path,
) -> None:
    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    current_path.parent.mkdir(parents=True)
    baseline_path.parent.mkdir(parents=True)
    baseline = _benchmark("Linux", 1.0)
    current = _benchmark("Linux", 1.0)
    current["rows"][1]["tg_time_s"] = float("nan")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    args = _workflow_checker_args()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *args,
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr


def test_workflow_checker_rejects_missing_metric_even_when_another_is_comparable(
    tmp_path: Path,
) -> None:
    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    current_path.parent.mkdir(parents=True)
    baseline_path.parent.mkdir(parents=True)
    baseline = _benchmark("Linux", 1.0)
    current = _benchmark("Linux", 1.0)
    current["rows"][1].pop("tg_time_s")
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *_workflow_checker_args(),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr
    assert "No benchmark regressions detected." not in result.stdout


def test_workflow_checker_rejects_matching_incomplete_known_suite_scenarios(
    tmp_path: Path,
) -> None:
    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    current_path.parent.mkdir(parents=True)
    baseline_path.parent.mkdir(parents=True)
    baseline = _benchmark("Linux", 1.0)
    current = _benchmark("Linux", 1.0)
    baseline["rows"] = baseline["rows"][:1]
    current["rows"] = current["rows"][:1]
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *_workflow_checker_args(),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr
    assert "scenario set" in result.stderr
    assert "No benchmark regressions detected." not in result.stdout


def test_workflow_checker_rejects_duplicate_rows_that_mask_a_regression(tmp_path: Path) -> None:
    current_path = tmp_path / "artifacts" / "bench_run_benchmarks.json"
    baseline_path = tmp_path / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    current_path.parent.mkdir(parents=True)
    baseline_path.parent.mkdir(parents=True)
    baseline = _benchmark("Linux", 1.0)
    current = _benchmark("Linux", 1.0)
    current["rows"][0]["tg_time_s"] = 2.0
    current["rows"].append({
        "name": BENCHMARK_SCENARIO_NAMES[0],
        "tg_time_s": 1.0,
        "rg_time_s": 0.5,
    })
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current_path.write_text(json.dumps(current), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT_DIR / "benchmarks" / "check_regression.py"),
            *_workflow_checker_args(),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Invalid benchmark timing data" in result.stderr
    assert "duplicate" in result.stderr
    assert "No benchmark regressions detected." not in result.stdout
