import pytest

from tensor_grep.perf_guard import (
    RUN_BENCHMARK_EXTRA_SCENARIO_NAMES,
    RUN_BENCHMARK_SCENARIO_NAMES,
    RUN_HOT_QUERY_SCENARIO_NAMES,
    benchmark_host_key,
    check_regressions,
    count_comparable_regression_metrics,
    detect_comparator_drift,
    detect_environment_mismatch,
)


def test_check_regressions_reports_slowdowns_over_threshold():
    baseline = {
        "rows": [
            {"name": "1. Regex Match", "tg_time_s": 1.0},
            {"name": "2. Fixed Strings", "tg_time_s": 2.0},
        ]
    }
    current = {
        "rows": [
            {"name": "1. Regex Match", "tg_time_s": 1.25},  # +25%
            {"name": "2. Fixed Strings", "tg_time_s": 2.05},  # +2.5%
        ]
    }

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=10.0)
    assert len(regressions) == 1
    assert "1. Regex Match" in regressions[0]


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), float("-inf"), -0.1])
def test_check_regressions_rejects_invalid_thresholds(threshold):
    with pytest.raises(ValueError, match="max_regression_pct must be a finite number >= 0"):
        check_regressions(
            baseline={"rows": [{"name": "slowdown", "tg_time_s": 1.0}]},
            current={"rows": [{"name": "slowdown", "tg_time_s": 2.0}]},
            max_regression_pct=threshold,
        )


def test_check_regressions_accepts_finite_nonnegative_thresholds():
    baseline = {"rows": [{"name": "slowdown", "tg_time_s": 1.0}]}
    current = {"rows": [{"name": "slowdown", "tg_time_s": 1.05}]}

    assert check_regressions(baseline, current, max_regression_pct=10.0) == []
    assert len(check_regressions(baseline, current, max_regression_pct=0.0)) == 1


def test_check_regressions_ignores_missing_or_non_numeric_rows():
    baseline = {"rows": [{"name": "a", "tg_time_s": 1.0}, {"name": "b", "tg_time_s": "bad"}]}
    current = {"rows": [{"name": "c", "tg_time_s": 5.0}, {"name": "b", "tg_time_s": 3.0}]}

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=0.0)
    assert regressions == []


def test_check_regressions_ignores_tiny_baselines_by_default():
    baseline = {"rows": [{"name": "tiny", "tg_time_s": 0.05}]}
    current = {"rows": [{"name": "tiny", "tg_time_s": 0.20}]}

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=10.0)
    assert regressions == []


def test_count_comparable_regression_metrics_applies_comparison_filters():
    baseline = {
        "suite": "custom",
        "rows": [
            {"name": "valid", "tg_time_s": 1.0},
            {"name": "non_numeric", "tg_time_s": 1.0},
            {"name": "short_baseline", "tg_time_s": 0.05},
            {"name": "not_current", "tg_time_s": 1.0},
        ],
    }
    current = {
        "suite": "custom",
        "rows": [
            {"name": "valid", "tg_time_s": 1.0},
            {"name": "non_numeric", "tg_time_s": "1.0"},
            {"name": "short_baseline", "tg_time_s": 0.2},
            {"name": "not_in_baseline", "tg_time_s": 1.0},
        ],
    }

    assert count_comparable_regression_metrics(baseline, current, min_baseline_time_s=0.1) == 1


def test_check_regressions_supports_hot_query_suite_metrics():
    baseline = {
        "suite": "run_hot_query_benchmarks",
        "rows": [
            {"name": "repeated_fixed_string", "first_s": 1.0, "second_s": 0.4},
            {"name": "repeated_regex_native", "first_s": 1.2, "second_s": 0.3},
        ],
    }
    current = {
        "suite": "run_hot_query_benchmarks",
        "rows": [
            {"name": "repeated_fixed_string", "first_s": 1.02, "second_s": 0.5},
            {"name": "repeated_regex_native", "first_s": 1.2, "second_s": 0.3},
        ],
    }

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=5.0)

    assert len(regressions) == 1
    assert "repeated_fixed_string" in regressions[0]
    assert "second_s" in regressions[0]


def test_detect_environment_mismatch_reports_platform_difference():
    baseline = {"environment": {"platform": "linux", "machine": "x86_64"}}
    current = {"environment": {"platform": "windows", "machine": "amd64"}}

    mismatch = detect_environment_mismatch(baseline=baseline, current=current)

    assert mismatch == "platform mismatch: baseline=linux current=windows"


def test_detect_environment_mismatch_reports_python_version_difference():
    baseline = {
        "environment": {
            "platform": "windows",
            "machine": "amd64",
            "python_version": "3.13.1",
        }
    }
    current = {
        "environment": {
            "platform": "windows",
            "machine": "amd64",
            "python_version": "3.14.0",
        }
    }

    mismatch = detect_environment_mismatch(baseline=baseline, current=current)

    assert mismatch == "python_version mismatch: baseline=3.13.1 current=3.14.0"


def test_detect_environment_mismatch_ignores_python_patch_difference():
    baseline = {
        "environment": {
            "platform": "linux",
            "machine": "x86_64",
            "python_version": "3.12",
        }
    }
    current = {
        "environment": {
            "platform": "linux",
            "machine": "x86_64",
            "python_version": "3.12.12",
        }
    }

    mismatch = detect_environment_mismatch(baseline=baseline, current=current)

    assert mismatch is None


def test_detect_environment_mismatch_ignores_missing_metadata():
    baseline = {"rows": []}
    current = {"environment": {"platform": "linux"}}

    mismatch = detect_environment_mismatch(baseline=baseline, current=current)

    assert mismatch is None


def test_benchmark_host_key_is_stable_for_same_host_metadata():
    environment = {
        "platform": "windows",
        "machine": "amd64",
        "python_version": "3.13.1",
    }

    assert benchmark_host_key(environment) == "windows:amd64:py3.13"


def test_detect_comparator_drift_reports_rg_time_s_changes():
    baseline = {"rows": [{"name": "x", "rg_time_s": 1.0}]}
    current = {"rows": [{"name": "x", "rg_time_s": 1.3}]}

    drift = detect_comparator_drift(
        baseline=baseline,
        current=current,
        comparator_key="rg_time_s",
        max_regression_pct=10.0,
    )

    assert len(drift) == 1
    assert "rg_time_s" in drift[0]


@pytest.mark.parametrize(
    ("suite", "scenario_names", "metric_keys", "bad_metric"),
    [
        (
            "run_benchmarks",
            RUN_BENCHMARK_SCENARIO_NAMES,
            ("tg_time_s",),
            "tg_time_s",
        ),
        (
            "run_hot_query_benchmarks",
            RUN_HOT_QUERY_SCENARIO_NAMES,
            ("first_s", "second_s"),
            "first_s",
        ),
        (
            "run_hot_query_benchmarks",
            RUN_HOT_QUERY_SCENARIO_NAMES,
            ("first_s", "second_s"),
            "second_s",
        ),
    ],
)
@pytest.mark.parametrize(
    ("side", "timing"),
    [("current", None), ("current", "bad"), ("baseline", None), ("baseline", "bad")],
)
def test_known_suite_rejects_missing_or_non_numeric_required_metrics(
    suite, scenario_names, metric_keys, bad_metric, side, timing
):
    baseline_rows = [{"name": name, **dict.fromkeys(metric_keys, 1.0)} for name in scenario_names]
    current_rows = [{"name": name, **dict.fromkeys(metric_keys, 1.0)} for name in scenario_names]
    bad_name = scenario_names[-1]
    selected = current_rows if side == "current" else baseline_rows
    if timing is None:
        selected[-1].pop(bad_metric)
    else:
        selected[-1][bad_metric] = timing
    baseline = {"suite": suite, "rows": baseline_rows}
    current = {"suite": suite, "rows": current_rows}

    with pytest.raises(ValueError, match=f"{bad_name}/{bad_metric}"):
        count_comparable_regression_metrics(baseline, current)


def test_known_suite_rejects_matching_incomplete_scenario_subsets():
    baseline = {
        "suite": "run_benchmarks",
        "rows": [{"name": RUN_BENCHMARK_SCENARIO_NAMES[0], "tg_time_s": 1.0}],
    }
    current = {
        "suite": "run_benchmarks",
        "rows": [{"name": RUN_BENCHMARK_SCENARIO_NAMES[0], "tg_time_s": 0.5}],
    }

    with pytest.raises(ValueError, match="scenario set"):
        count_comparable_regression_metrics(baseline, current)


def test_known_suite_rejects_duplicate_scenario_names():
    baseline_rows = [{"name": name, "tg_time_s": 1.0} for name in RUN_BENCHMARK_SCENARIO_NAMES]
    current_rows = [{"name": name, "tg_time_s": 1.0} for name in RUN_BENCHMARK_SCENARIO_NAMES]
    current_rows.append({"name": RUN_BENCHMARK_SCENARIO_NAMES[0], "tg_time_s": 1.0})

    with pytest.raises(ValueError, match="duplicate"):
        count_comparable_regression_metrics(
            {"suite": "run_benchmarks", "rows": baseline_rows},
            {"suite": "run_benchmarks", "rows": current_rows},
        )


def test_benchmark_scenario_contract_matches_runner_definitions():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    benchmark_module = ast.parse(
        (root / "benchmarks/run_benchmarks.py").read_text(encoding="utf-8")
    )
    scenarios_node = next(
        node.value
        for node in benchmark_module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCENARIOS" for target in node.targets
        )
    )
    scenarios = ast.literal_eval(scenarios_node)
    assert tuple(scenario["name"] for scenario in scenarios) == RUN_BENCHMARK_SCENARIO_NAMES

    extra_function = next(
        node
        for node in benchmark_module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_extra_native_cpu_scenarios"
    )
    extra_names = []
    for node in ast.walk(extra_function):
        if isinstance(node, ast.Dict):
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values, strict=True)
                if isinstance(key, ast.Constant)
            }
            name = values.get("name")
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                extra_names.append(name.value)
    assert tuple(extra_names) == RUN_BENCHMARK_EXTRA_SCENARIO_NAMES
    hot_query_source = (root / "benchmarks/run_hot_query_benchmarks.py").read_text(encoding="utf-8")
    assert all(f'"name": "{name}"' in hot_query_source for name in RUN_HOT_QUERY_SCENARIO_NAMES)


def test_known_suite_rejects_scenario_set_mismatch():
    baseline = {
        "suite": "run_benchmarks",
        "rows": [{"name": RUN_BENCHMARK_SCENARIO_NAMES[0], "tg_time_s": 1.0}],
    }
    current = {
        "suite": "run_benchmarks",
        "rows": [{"name": "unexpected", "tg_time_s": 1.0}],
    }

    with pytest.raises(ValueError, match="scenario set"):
        count_comparable_regression_metrics(baseline, current)


def test_known_native_benchmark_accepts_canonical_extra_scenarios():
    names = (*RUN_BENCHMARK_SCENARIO_NAMES, *RUN_BENCHMARK_EXTRA_SCENARIO_NAMES)
    baseline = {
        "suite": "run_benchmarks",
        "native_cpu_forced": True,
        "rows": [{"name": name, "tg_time_s": 1.0} for name in names],
    }
    current = {
        "suite": "run_benchmarks",
        "native_cpu_forced": True,
        "rows": [{"name": name, "tg_time_s": 1.0} for name in names],
    }

    assert count_comparable_regression_metrics(baseline, current) == len(names)
