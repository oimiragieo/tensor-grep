import importlib.util
from pathlib import Path


def _load_script_module(name: str, rel_path: str):
    root = Path(__file__).resolve().parents[2]
    module_path = root / rel_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_gpu_benchmarks_should_parse_5gb_corpus_size():
    module = _load_script_module(
        "run_gpu_benchmarks_scale_parse", "benchmarks/run_gpu_benchmarks.py"
    )

    sizes = module.parse_corpus_sizes("1MB, 10MB,100MB,1GB,5GB")

    assert sizes == (
        module.MB,
        10 * module.MB,
        100 * module.MB,
        module.GB,
        5 * module.GB,
    )


def test_run_gpu_benchmarks_should_check_every_gb_scale_corpus(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_benchmarks_scale_correctness",
        "benchmarks/run_gpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")
    checked_sizes: list[str] = []

    monkeypatch.setattr(
        module,
        "probe_gpu_devices",
        lambda _sidecar_python: {
            "available": True,
            "torch_version": "2.6.0",
            "devices": [{"device_id": 0, "name": "RTX", "operational": True}],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        module,
        "generate_gpu_scale_corpus",
        lambda output_dir, target_bytes, shard_count: {
            "corpus_dir": output_dir,
            "actual_bytes": target_bytes,
            "total_lines": 10,
            "file_count": shard_count,
            "pattern_counts": {"gpu benchmark sentinel": 1},
        },
    )
    monkeypatch.setattr(
        module,
        "benchmark_search_command",
        lambda *_args, **_kwargs: {"status": "PASS", "median_s": 0.1, "samples_s": [0.1]},
    )

    def _fake_correctness_check(**kwargs):
        checked_sizes.append(Path(kwargs["corpus_dir"]).name)
        return {
            "device_id": kwargs["device_id"],
            "pattern": kwargs["pattern"],
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }

    monkeypatch.setattr(module, "run_correctness_check", _fake_correctness_check)

    payload = module.run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary="rg",
        bench_dir=module.ROOT_DIR / "artifacts" / "unit_gpu_bench_data",
        corpus_sizes=(module.MB, module.GB, 5 * module.GB),
        runs=1,
        warmup=0,
        sidecar_python=sidecar_python,
        benchmark_pattern="gpu benchmark sentinel",
        correctness_patterns=("gpu benchmark sentinel",),
        shard_count=2,
    )

    assert checked_sizes == ["1GB", "5GB"]
    assert [check["corpus_size_label"] for check in payload["correctness_checks"]] == [
        "1GB",
        "5GB",
    ]


def test_run_gpu_native_benchmarks_should_default_to_1gb_and_5gb_scale():
    module = _load_script_module(
        "run_gpu_native_benchmarks_scale_defaults",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    assert module.DEFAULT_CORPUS_SIZES[-2:] == (module.GB, 5 * module.GB)
