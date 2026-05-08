import subprocess

from tests.unit.test_benchmark_scripts import _load_script_module


def test_run_gpu_benchmarks_should_treat_no_match_correctness_as_success(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_benchmarks_script_no_match_correctness", "benchmarks/run_gpu_benchmarks.py"
    )
    corpus_dir = module.ROOT_DIR / "artifacts" / "gpu_bench_data" / "1MB"

    def _fake_run_command(command, *, env, capture_output):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    check = module.run_correctness_check(
        rg_binary="rg",
        tg_binary=tmp_path / "tg.exe",
        corpus_dir=corpus_dir,
        pattern="Database connection timeout",
        device_id=0,
        env={},
    )

    assert check["status"] == "PASS"
    assert check["rg_matches"] == 0
    assert check["gpu_matches"] == 0
    assert check["matches_equal"] is True
    assert check["files_equal"] is True
