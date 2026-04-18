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


def test_milestone_one_baseline_should_exist_and_use_expected_schema():
    root = Path(__file__).resolve().parents[2]
    baseline_path = root / "benchmarks" / "baseline_m1.json"

    assert baseline_path.exists(), f"Missing benchmark baseline: {baseline_path}"

    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload.get("suite") == "run_benchmarks"
    assert payload.get("milestone") == "m1"
    environment = payload.get("environment")
    assert isinstance(environment, dict)
    assert environment.get("platform")
    assert environment.get("machine")
    rows = payload.get("rows")
    assert isinstance(rows, list) and len(rows) >= 8


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


def test_windows_benchmark_baseline_should_record_host_provenance():
    root = Path(__file__).resolve().parents[2]
    baseline = json.loads(
        (root / "benchmarks" / "baselines" / "run_benchmarks.windows.json").read_text(
            encoding="utf-8"
        )
    )

    assert baseline.get("benchmark_host_key") == "windows:amd64:py3.12"
    host_provenance = baseline.get("host_provenance")
    assert isinstance(host_provenance, dict)
    assert host_provenance.get("benchmark_host_key") == "windows:amd64:py3.12"
    assert host_provenance.get("platform") == "windows"
    assert host_provenance.get("machine") == "amd64"
    assert host_provenance.get("python_version") == "3.12.12"
    assert host_provenance.get("tg_binary_source") == "default_binary_path"
    assert host_provenance.get("tg_launcher_mode") == "explicit_binary"
