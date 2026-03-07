import json
from pathlib import Path


def test_benchmark_baselines_should_exist_and_use_expected_schema():
    root = Path(__file__).resolve().parents[2]
    ubuntu = root / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    windows = root / "benchmarks" / "baselines" / "run_benchmarks.windows.json"

    for baseline_path in (ubuntu, windows):
        assert baseline_path.exists(), f"Missing benchmark baseline: {baseline_path}"
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert payload.get("suite") == "run_benchmarks"
        environment = payload.get("environment")
        assert isinstance(environment, dict)
        assert environment.get("platform")
        assert environment.get("machine")
        rows = payload.get("rows")
        assert isinstance(rows, list) and len(rows) >= 5
        for row in rows:
            assert "name" in row
            assert "tg_time_s" in row
            assert "rg_time_s" in row


def test_benchmark_baselines_should_not_be_identical_across_operating_systems():
    root = Path(__file__).resolve().parents[2]
    ubuntu = json.loads(
        (root / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json").read_text(
            encoding="utf-8"
        )
    )
    windows = json.loads(
        (root / "benchmarks" / "baselines" / "run_benchmarks.windows.json").read_text(
            encoding="utf-8"
        )
    )

    # OS runner characteristics differ materially; identical baselines indicate stale copy/paste drift.
    assert ubuntu["rows"] != windows["rows"]
