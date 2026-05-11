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
    assert payload["scale_gate_summary"] == {
        "benchmark_surface": "python-gpu-scale",
        "native_cuda_scale_gate": {
            "status": "UNSUPPORTED",
            "required_backend": "NativeGpuBackend",
            "observed_backends": ["GpuSidecar"],
            "reason": (
                "Operational GPU devices routed outside the native CUDA backend; "
                "Python/Torch sidecar rows are not native CUDA scale proof."
            ),
        },
        "correctness_gate": {
            "status": "NOT_RUN",
            "required_sizes": ["1GB", "5GB"],
            "passing_device_ids": [],
            "reason": "Native CUDA correctness checks did not run.",
        },
        "speed_gate": {
            "status": "NOT_RUN",
            "required_baselines": ["rg", "tg_cpu"],
            "reason": "Native CUDA speed gate did not run because the native CUDA scale gate is unsupported.",
        },
        "promotion_ready": False,
        "summary": (
            "Python GPU scale rows are unsupported for native CUDA promotion; run "
            "benchmarks/run_gpu_native_benchmarks.py with a CUDA-enabled native tg binary "
            "to evaluate correctness and speed separately."
        ),
    }
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


def test_run_gpu_native_correctness_should_reject_sidecar_routed_gpu_json(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_sidecar_correctness",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--cpu" in command_text:
            stdout = (
                '{"routing_backend":"CpuBackend","routing_reason":"cpu-native",'
                '"sidecar_used":false,"total_matches":2,"total_files":1,'
                '"matches":[{"file":"sample.log"}]}'
            )
        else:
            stdout = (
                '{"routing_backend":"GpuSidecar","routing_reason":"python-sidecar",'
                '"sidecar_used":true,"total_matches":2,"total_files":1,'
                '"matches":[{"file":"sample.log"}]}'
            )
        return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    result = module.run_correctness_check(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        pattern="gpu benchmark sentinel",
        device_id=0,
        env={},
        timeout_s=5,
    )

    assert result["status"] == "UNSUPPORTED"
    assert result["routing_backend"] == "GpuSidecar"
    assert result["sidecar_used"] is True
    assert "not native CUDA scale proof" in result["error"]


def test_run_gpu_native_benchmarks_should_not_time_sidecar_routed_gpu_rows(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_sidecar_rows",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    gpu_timing_commands: list[str] = []

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
        "run_gpu_error_tests",
        lambda **_kwargs: {
            "invalid_device": {"status": "PASS", "exit_code": 2},
            "nvrtc_failure": {"status": "PASS", "exit_code": 2},
            "timeout": {"status": "PASS", "exit_code": 2, "simulated": True},
            "malformed_inputs": {"status": "PASS", "exit_code": 0},
        },
    )

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--json" not in command_text:
            return module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "--cpu" in command_text:
            stdout = (
                '{"routing_backend":"CpuBackend","routing_reason":"cpu-native",'
                '"sidecar_used":false,"total_matches":2,"total_files":1,'
                '"matches":[{"file":"sample.log"}]}'
            )
        else:
            stdout = (
                '{"routing_backend":"GpuSidecar","routing_reason":"python-sidecar",'
                '"sidecar_used":true,"total_matches":2,"total_files":1,'
                '"matches":[{"file":"sample.log"}]}'
            )
        return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    def _fake_benchmark_search_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--gpu-device-ids" in command_text:
            gpu_timing_commands.append(command_text)
        return {
            "status": "PASS",
            "median_s": 1.0,
            "samples_s": [1.0],
            "stderr": "",
            "command": command_text,
            "throughput_bytes_s": 1.0,
        }

    monkeypatch.setattr(module, "_run_command", _fake_run_command)
    monkeypatch.setattr(module, "benchmark_search_command", _fake_benchmark_search_command)

    payload = module.run_gpu_native_benchmarks(
        tg_binary=tg_binary,
        rg_binary="rg",
        bench_dir=tmp_path / "gpu_native_bench_data",
        corpus_sizes=(module.GB,),
        runs=1,
        warmup=0,
        device_id=0,
        command_timeout_s=5,
        shard_count=2,
        benchmark_pattern="gpu benchmark sentinel",
        timeout_simulation_ms=300,
        advanced=False,
    )

    tg_gpu = payload["rows"][0]["tg_gpu"]
    assert gpu_timing_commands == []
    assert tg_gpu["status"] == "UNSUPPORTED"
    assert tg_gpu["routing_backend"] == "GpuSidecar"
    assert tg_gpu["sidecar_used"] is True
    assert "not native CUDA scale proof" in tg_gpu["stderr"]
    assert payload["scale_gate_summary"]["native_cuda_runtime_gate"] == {
        "status": "UNSUPPORTED",
        "required_backend": "NativeGpuBackend",
        "observed_backends": ["GpuSidecar"],
        "sidecar_observed": True,
        "reason": (
            "GPU rows routed outside the native CUDA backend; sidecar-routed rows are not "
            "native CUDA speed proof."
        ),
    }
    assert payload["scale_gate_summary"]["correctness_gate"]["status"] == "UNSUPPORTED"
    assert payload["scale_gate_summary"]["speed_gate"]["status"] == "NOT_RUN"


def test_run_gpu_native_runtime_probe_should_preserve_pipeline_metrics(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_probe_pipeline",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()

    def _fake_run_command(command, **_kwargs):
        stdout = (
            '{"routing_backend":"NativeGpuBackend",'
            '"routing_reason":"gpu-device-ids-explicit-native",'
            '"sidecar_used":false,'
            '"pipeline":{'
            '"cpu_staging_bytes":1234,'
            '"pageable_host_staging_bytes":0,'
            '"host_file_read_time_ms":1.25,'
            '"host_preprocess_time_ms":0.5,'
            '"host_to_pinned_copy_time_ms":0.0,'
            '"transfer_time_ms":2.0,'
            '"kernel_time_ms":3.0'
            "}}"
        )
        return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    result = module.probe_native_gpu_runtime_backend(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        pattern="gpu benchmark sentinel",
        device_id=0,
        env={},
        timeout_s=5,
    )

    assert result["status"] == "PASS"
    assert result["routing_backend"] == "NativeGpuBackend"
    assert result["sidecar_used"] is False
    assert result["pipeline"]["cpu_staging_bytes"] == 1234
    assert result["pipeline"]["host_to_pinned_copy_time_ms"] == 0.0


def test_run_gpu_native_error_tests_should_report_malformed_timeout(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_error_timeout",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()

    def _fake_run_command(command, **kwargs):
        command_text = " ".join(str(part) for part in command)
        env = kwargs.get("env", {})
        behavior = env.get("TG_TEST_CUDA_BEHAVIOR", "") if isinstance(env, dict) else ""
        if "--json" in command_text:
            return module.subprocess.TimeoutExpired(command, timeout=7)
        if "99" in command_text:
            return module.subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="GPU device 99 unavailable; available CUDA devices: 0",
            )
        if behavior.startswith("nvrtc-failure:"):
            return module.subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="CUDA kernel compilation failed: simulated NVRTC compile error",
            )
        if behavior.startswith("timeout:"):
            return module.subprocess.CompletedProcess(
                command,
                2,
                stdout="",
                stderr="GPU search timed out after simulated delay",
            )
        return module.subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    payload = module.run_gpu_error_tests(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        device_id=0,
        timeout_s=7,
        timeout_simulation_ms=300,
    )

    malformed = payload["malformed_inputs"]
    assert malformed["status"] == "FAIL"
    assert malformed["exit_code"] is None
    assert malformed["stderr"] == "command timed out after 7s"
    assert malformed["simulated"] is False


def test_run_gpu_native_benchmarks_should_separate_correctness_pass_from_speed_failure():
    module = _load_script_module(
        "run_gpu_native_benchmarks_scale_gate_summary",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 0.25},
            "tg_cpu": {"status": "PASS", "median_s": 0.24},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 9.2,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 0.28},
            "tg_cpu": {"status": "PASS", "median_s": 0.23},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 9.4,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": size_label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]

    summary = module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )

    assert summary["benchmark_surface"] == "native-cuda-scale"
    assert summary["native_cuda_runtime_gate"] == {
        "status": "PASS",
        "required_backend": "NativeGpuBackend",
        "observed_backends": ["NativeGpuBackend"],
        "sidecar_observed": False,
        "reason": "Native CUDA runtime route was observed.",
    }
    assert summary["correctness_gate"] == {
        "status": "PASS",
        "required_sizes": ["1GB", "5GB"],
        "passing_sizes": ["1GB", "5GB"],
        "reason": "Native CUDA correctness passed at every required scale.",
    }
    assert summary["speed_gate"] == {
        "status": "FAIL",
        "required_baselines": ["rg", "tg_cpu"],
        "winning_sizes": [],
        "best_attempt": {
            "size_label": "1GB",
            "gpu_rg_ratio": 36.8,
            "gpu_tg_cpu_ratio": 38.3333,
        },
        "reason": "Native CUDA did not beat both rg and tg_cpu at the required scale.",
    }
    assert summary["promotion_ready"] is False
    assert (
        summary["summary"]
        == "Native CUDA correctness passed, but speed/promotion failed; keep GPU experimental."
    )
