from tensor_grep.perf_guard import check_regressions


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
