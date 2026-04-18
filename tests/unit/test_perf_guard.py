from tensor_grep.perf_guard import (
    benchmark_host_key,
    check_regressions,
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


def test_check_regressions_ignores_missing_or_non_numeric_rows():
    baseline = {"rows": [{"name": "a", "tg_time_s": 1.0}, {"name": "b", "tg_time_s": 0.0}]}
    current = {"rows": [{"name": "c", "tg_time_s": 5.0}, {"name": "b", "tg_time_s": 3.0}]}

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=0.0)
    assert regressions == []


def test_check_regressions_ignores_tiny_baselines_by_default():
    baseline = {"rows": [{"name": "tiny", "tg_time_s": 0.05}]}
    current = {"rows": [{"name": "tiny", "tg_time_s": 0.20}]}

    regressions = check_regressions(baseline=baseline, current=current, max_regression_pct=10.0)
    assert regressions == []


def test_check_regressions_supports_hot_query_suite_metrics():
    baseline = {
        "suite": "run_hot_query_benchmarks",
        "rows": [{"name": "repeated_fixed_string", "first_s": 1.0, "second_s": 0.4}],
    }
    current = {
        "suite": "run_hot_query_benchmarks",
        "rows": [{"name": "repeated_fixed_string", "first_s": 1.02, "second_s": 0.5}],
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
