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
    monkeypatch.setattr(
        module,
        "probe_tg_gpu_runtime_backend",
        lambda **_kwargs: {
            "status": "PASS",
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
        },
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


def test_run_gpu_benchmarks_should_not_attach_unsupported_inventory_warning_to_gpu0(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_benchmarks_inventory_warning_scope",
        "benchmarks/run_gpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")

    inventory_warning = (
        "Inventory warning: GPU 1 NVIDIA GeForce RTX 5070 is unsupported by "
        "the current CUDA-enabled PyTorch build."
    )
    unsupported_error = (
        "GPU 1 NVIDIA GeForce RTX 5070 unsupported: no kernel image is available "
        "for execution on the device"
    )

    monkeypatch.setattr(
        module,
        "probe_gpu_devices",
        lambda _sidecar_python: {
            "available": True,
            "torch_version": "2.6.0",
            "devices": [
                {"device_id": 0, "name": "NVIDIA GeForce RTX 4070", "operational": True},
                {
                    "device_id": 1,
                    "name": "NVIDIA GeForce RTX 5070",
                    "operational": False,
                    "error": unsupported_error,
                },
            ],
            "warnings": [inventory_warning],
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

    def _fake_benchmark_search_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--gpu-device-ids" in command_text:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (
                    "GPU 0 timing failed after kernel launch\n"
                    f"{inventory_warning}\n"
                    f"{unsupported_error}"
                ),
                "command": command_text,
            }
        return {
            "status": "PASS",
            "median_s": 1.0,
            "samples_s": [1.0],
            "stderr": "",
            "command": command_text,
        }

    monkeypatch.setattr(module, "benchmark_search_command", _fake_benchmark_search_command)
    monkeypatch.setattr(
        module,
        "probe_tg_gpu_runtime_backend",
        lambda **_kwargs: {
            "status": "PASS",
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
        },
    )
    monkeypatch.setattr(
        module,
        "run_correctness_check",
        lambda **kwargs: {
            "device_id": kwargs["device_id"],
            "pattern": kwargs["pattern"],
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        },
    )

    payload = module.run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary="rg",
        bench_dir=module.ROOT_DIR / "artifacts" / "unit_gpu_bench_data",
        corpus_sizes=(module.MB,),
        runs=1,
        warmup=0,
        sidecar_python=sidecar_python,
        benchmark_pattern="gpu benchmark sentinel",
        correctness_patterns=("gpu benchmark sentinel",),
        shard_count=2,
    )

    gpu0, gpu1 = payload["rows"][0]["gpu"]
    assert inventory_warning in payload["warnings"]
    assert gpu0["status"] == "FAIL"
    assert "GPU 0 timing failed after kernel launch" in gpu0["stderr"]
    assert "GPU 1" not in gpu0["stderr"]
    assert "RTX 5070" not in gpu0["stderr"]
    assert "unsupported" not in gpu0["stderr"]
    assert gpu1["status"] == "UNSUPPORTED"
    assert gpu1["stderr"] == unsupported_error


def test_run_gpu_benchmarks_should_skip_sidecar_gpu_runtime_for_scale_gates(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_benchmarks_sidecar_runtime_skip",
        "benchmarks/run_gpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")
    correctness_called = False
    commands: list[str] = []

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
        "probe_tg_gpu_runtime_backend",
        lambda **_kwargs: {
            "status": "PASS",
            "routing_backend": "GpuSidecar",
            "routing_reason": "gpu-device-ids-explicit",
            "sidecar_used": True,
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

    def _fake_benchmark_search_command(command, **_kwargs):
        commands.append(" ".join(str(part) for part in command))
        return {"status": "PASS", "median_s": 1.0, "samples_s": [1.0], "stderr": ""}

    def _fake_correctness_check(**_kwargs):
        nonlocal correctness_called
        correctness_called = True
        return {"status": "PASS", "matches_equal": True, "files_equal": True}

    monkeypatch.setattr(module, "benchmark_search_command", _fake_benchmark_search_command)
    monkeypatch.setattr(module, "run_correctness_check", _fake_correctness_check)

    payload = module.run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary="rg",
        bench_dir=module.ROOT_DIR / "artifacts" / "unit_gpu_bench_data",
        corpus_sizes=(module.GB,),
        runs=1,
        warmup=0,
        sidecar_python=sidecar_python,
        benchmark_pattern="gpu benchmark sentinel",
        correctness_patterns=("gpu benchmark sentinel",),
        shard_count=2,
    )

    gpu0 = payload["rows"][0]["gpu"][0]
    assert gpu0["status"] == "UNSUPPORTED"
    assert gpu0["tg_runtime_backend"] == "GpuSidecar"
    assert "requires a CUDA-enabled native tg binary" in gpu0["stderr"]
    assert all("--gpu-device-ids" not in command for command in commands)
    assert correctness_called is False
    assert payload["correctness_checks"] == []


def test_run_gpu_benchmarks_should_sanitize_correctness_error_for_selected_gpu(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_benchmarks_correctness_warning_scope",
        "benchmarks/run_gpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")

    inventory_warning = (
        "Inventory warning: GPU 1 NVIDIA GeForce RTX 5070 is unsupported by "
        "the current CUDA-enabled PyTorch build."
    )
    unsupported_error = (
        "GPU 1 NVIDIA GeForce RTX 5070 unsupported: no kernel image is available "
        "for execution on the device"
    )

    monkeypatch.setattr(
        module,
        "probe_gpu_devices",
        lambda _sidecar_python: {
            "available": True,
            "torch_version": "2.6.0",
            "devices": [
                {"device_id": 0, "name": "NVIDIA GeForce RTX 4070", "operational": True},
                {
                    "device_id": 1,
                    "name": "NVIDIA GeForce RTX 5070",
                    "operational": False,
                    "error": unsupported_error,
                },
            ],
            "warnings": [inventory_warning],
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
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "median_s": 1.0,
            "samples_s": [1.0],
            "stderr": "",
        },
    )
    monkeypatch.setattr(
        module,
        "probe_tg_gpu_runtime_backend",
        lambda **_kwargs: {
            "status": "PASS",
            "routing_backend": "NativeGpuBackend",
            "routing_reason": "gpu-device-ids-explicit-native",
            "sidecar_used": False,
        },
    )

    monkeypatch.setattr(
        module,
        "run_correctness_check",
        lambda **kwargs: {
            "device_id": kwargs["device_id"],
            "pattern": kwargs["pattern"],
            "status": "FAIL",
            "error": (
                "GPU 0 correctness mismatch after compare\n"
                f"{inventory_warning}\n"
                f"{unsupported_error}"
            ),
            "matches_equal": False,
            "files_equal": False,
        },
    )

    payload = module.run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary="rg",
        bench_dir=module.ROOT_DIR / "artifacts" / "unit_gpu_bench_data",
        corpus_sizes=(module.GB,),
        runs=1,
        warmup=0,
        sidecar_python=sidecar_python,
        benchmark_pattern="gpu benchmark sentinel",
        correctness_patterns=("gpu benchmark sentinel",),
        shard_count=2,
    )

    check = payload["correctness_checks"][0]
    assert "GPU 0 correctness mismatch after compare" in check["error"]
    assert "GPU 1" not in check["error"]
    assert "RTX 5070" not in check["error"]
    assert "unsupported" not in check["error"]


def test_gpu_auto_recommendation_should_not_recommend_for_small_winning_row_only():
    module = _load_script_module(
        "run_gpu_benchmarks_small_recommendation",
        "benchmarks/run_gpu_benchmarks.py",
    )

    recommendation = module.analyze_gpu_auto_recommendation(
        [
            {
                "size_label": "100MB",
                "size_bytes": 100 * module.MB,
                "rg": {"status": "PASS", "median_s": 10.0},
                "tg_cpu": {"status": "PASS", "median_s": 9.0},
                "gpu": [{"device_id": 0, "status": "PASS", "median_s": 1.0}],
            }
        ],
        correctness_checks=[],
        correctness_patterns=("gpu benchmark sentinel",),
    )

    assert recommendation["should_add_flag"] is False
    assert recommendation["winning_rows"] == []
    assert "1GB/5GB correctness" in recommendation["reason"]


def test_gpu_auto_recommendation_should_recommend_required_scale_win_with_correctness():
    module = _load_script_module(
        "run_gpu_benchmarks_required_recommendation",
        "benchmarks/run_gpu_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 10.0},
            "tg_cpu": {"status": "PASS", "median_s": 12.0},
            "gpu": [{"device_id": 0, "status": "PASS", "median_s": 5.0}],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 60.0},
            "tg_cpu": {"status": "PASS", "median_s": 70.0},
            "gpu": [{"device_id": 0, "status": "PASS", "median_s": 55.0}],
        },
    ]
    correctness_checks = [
        {
            "device_id": 0,
            "corpus_size_label": size_label,
            "pattern": pattern,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }
        for size_label in ("1GB", "5GB")
        for pattern in ("gpu benchmark sentinel", "WARN retry budget exhausted")
    ]

    recommendation = module.analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=("gpu benchmark sentinel", "WARN retry budget exhausted"),
    )

    assert recommendation["should_add_flag"] is True
    assert recommendation["winning_rows"] == [
        {
            "device_id": 0,
            "size_label": "1GB",
            "size_bytes": module.GB,
            "speedup_vs_rg_pct": 50.0,
            "speedup_vs_tg_cpu_pct": 58.33,
        }
    ]


def test_run_gpu_native_benchmarks_should_default_to_1gb_and_5gb_scale():
    module = _load_script_module(
        "run_gpu_native_benchmarks_scale_defaults",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    assert module.DEFAULT_CORPUS_SIZES[-2:] == (module.GB, 5 * module.GB)
