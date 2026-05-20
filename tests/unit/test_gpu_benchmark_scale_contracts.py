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


def _passing_native_scale_summary(module):
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.0},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.5,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.4},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.6,
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
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    return module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )


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


def test_gpu_promotion_contract_should_name_single_pattern_workload_class():
    module = _load_script_module(
        "run_gpu_benchmarks_workload_contract", "benchmarks/run_gpu_benchmarks.py"
    )

    contract = module._promotion_evidence_contract(["1GB", "5GB"])

    assert contract["required_workload_class"] == "single_pattern_cold_grep"
    assert contract["promotion_scope"] == "declared_workload_class_only"
    assert contract["fair_many_pattern_baseline"] == "rg -F -e ... -e ..."
    assert contract["many_pattern_claim_requires_fair_rg_multi_pattern_baseline"] is True


def test_gpu_native_promotion_contract_should_name_single_pattern_workload_class():
    module = _load_script_module(
        "run_gpu_native_benchmarks_workload_contract",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    contract = module._promotion_evidence_contract(["1GB", "5GB"])

    assert contract["required_workload_class"] == "single_pattern_cold_grep"
    assert contract["promotion_scope"] == "declared_workload_class_only"
    assert contract["fair_many_pattern_baseline"] == "rg -F -e ... -e ..."
    assert contract["many_pattern_claim_requires_fair_rg_multi_pattern_baseline"] is True


def test_gpu_workload_taxonomy_should_name_candidate_classes():
    modules = [
        _load_script_module(
            "run_gpu_benchmarks_workload_taxonomy", "benchmarks/run_gpu_benchmarks.py"
        ),
        _load_script_module(
            "run_gpu_native_benchmarks_workload_taxonomy",
            "benchmarks/run_gpu_native_benchmarks.py",
        ),
    ]

    for module in modules:
        taxonomy = module.build_gpu_workload_taxonomy()

        assert taxonomy["promotion_scope"] == "declared_workload_class_only"
        assert taxonomy["measured_scale_gate"]["workload_class"] == "single_pattern_cold_grep"
        assert taxonomy["measured_scale_gate"]["promotion_eligible"] is True
        assert taxonomy["candidate_workload_classes"] == [
            {
                "workload_class": "many_fixed_patterns_single_dispatch",
                "status": "candidate_until_required_scale_correctness_and_fair_rg_speed_proof",
                "fair_rg_baseline": "rg -F -e ... -e ...",
            },
            {
                "workload_class": "resident_repeated_query",
                "status": "candidate_not_measured",
                "fair_rg_baseline": "not_applicable_until_benchmark_exists",
            },
        ]


def test_gpu_native_summary_should_report_workload_scoped_speed_blocker():
    module = _load_script_module(
        "run_gpu_native_benchmarks_losing_speed_scope",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    rows = [
        {
            "size_label": label,
            "rg": {"median_s": 1.0},
            "tg_cpu": {"median_s": 1.5},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 2.0,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        }
        for label in ("1GB", "5GB")
    ]
    correctness_checks = [
        {
            "size_label": label,
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for label in ("1GB", "5GB")
    ]

    summary = module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )
    proof = module._gpu_proof_status_from_native_summary(summary)

    assert summary["workload_taxonomy"]["promotion_scope"] == "declared_workload_class_only"
    assert summary["workload_evidence_status"] == "speed_gate_failed"
    assert summary["promotion_ready"] is False
    assert proof["gpu_proof"] is False
    assert proof["gpu_evidence_status"] == "experimental"


def test_public_managed_gpu_proof_gate_requires_managed_nvidia_metadata():
    module = _load_script_module(
        "run_gpu_native_benchmarks_public_managed_proof",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    scale_gate_summary = _passing_native_scale_summary(module)

    gate = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=scale_gate_summary,
    )

    assert gate["status"] == "PASS"
    assert gate["public_managed_promotion_ready"] is True
    assert gate["public_gpu_proof"] is True
    assert gate["blockers"] == []


def test_public_managed_gpu_proof_gate_rejects_stale_or_incomplete_metadata():
    module = _load_script_module(
        "run_gpu_native_benchmarks_public_managed_proof_reject_stale_metadata",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    scale_gate_summary = _passing_native_scale_summary(module)

    stale = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.33",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=scale_gate_summary,
    )

    incomplete = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=scale_gate_summary,
    )

    assert stale["status"] == "FAIL"
    assert stale["public_gpu_proof"] is False
    assert "managed_native_metadata_version_mismatch" in stale["blockers"]
    assert incomplete["status"] == "FAIL"
    assert incomplete["public_managed_promotion_ready"] is False
    assert "managed_native_asset_name_missing" in incomplete["blockers"]


def test_public_managed_gpu_proof_gate_rejects_in_tree_cuda_proof():
    module = _load_script_module(
        "run_gpu_native_benchmarks_public_managed_proof_reject_in_tree",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    gate = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "in-tree-release",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=_passing_native_scale_summary(module),
    )

    assert gate["status"] == "FAIL"
    assert gate["public_managed_promotion_ready"] is False
    assert gate["public_gpu_proof"] is False
    assert "not_managed_native_frontdoor" in gate["blockers"]


def test_public_managed_gpu_proof_gate_rejects_cpu_frontdoor_fallback():
    module = _load_script_module(
        "run_gpu_native_benchmarks_public_managed_proof_reject_cpu",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    gate = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "cpu",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-cpu.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary=_passing_native_scale_summary(module),
    )

    assert gate["status"] == "FAIL"
    assert gate["public_managed_promotion_ready"] is False
    assert "installed_frontdoor_not_nvidia" in gate["blockers"]


def test_public_managed_gpu_proof_gate_rejects_weak_scale_summary():
    module = _load_script_module(
        "run_gpu_native_benchmarks_public_managed_proof_reject_weak_summary",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    gate = module.build_public_managed_gpu_proof_gate(
        tg_binary_metadata={
            "kind": "managed-native",
            "native_frontdoor_flavor": "nvidia",
            "native_frontdoor_requested_flavor": "nvidia",
            "native_frontdoor_asset_name": "tg-windows-amd64-nvidia.exe",
            "native_frontdoor_metadata_status": "present",
            "native_frontdoor_metadata_version": "1.12.34",
            "expected_version": "1.12.34",
            "version_status": "matches",
        },
        scale_gate_summary={"promotion_ready": True},
    )

    assert gate["status"] == "FAIL"
    assert gate["public_gpu_proof"] is False
    assert "native_cuda_scale_surface_missing" in gate["blockers"]
    assert "native_cuda_correctness_gate_not_passed" in gate["blockers"]
    assert "native_cuda_speed_gate_not_passed" in gate["blockers"]


def test_native_scale_gate_requires_rg_identity_correctness():
    module = _load_script_module(
        "run_gpu_native_benchmarks_rg_identity_gate",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 1.2},
            "tg_cpu": {"status": "PASS", "median_s": 1.0},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.5,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 1.4},
            "tg_cpu": {"status": "PASS", "median_s": 1.1},
            "tg_gpu": {
                "status": "PASS",
                "median_s": 0.6,
                "routing_backend": "NativeGpuBackend",
                "sidecar_used": False,
            },
        },
    ]
    correctness_checks = [
        {
            "size_label": "1GB",
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        },
        {
            "size_label": "5GB",
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
            "rg_matches_equal": False,
            "rg_files_equal": True,
            "rg_match_identity_equal": False,
        },
    ]

    summary = module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )

    assert summary["promotion_ready"] is False
    assert summary["correctness_gate"]["status"] == "FAIL"
    assert summary["correctness_gate"]["rg_passing_sizes"] == ["1GB"]
    assert "correctness_gate_failed" in summary["promotion_blockers"]


def test_gpu_python_summary_should_mark_native_cpu_fallback_as_unsupported_not_proof():
    module = _load_script_module(
        "run_gpu_benchmarks_native_cpu_fallback_scope",
        "benchmarks/run_gpu_benchmarks.py",
    )

    devices = [
        {
            "device_id": 0,
            "name": "RTX",
            "operational": True,
            "tg_runtime_backend": "NativeCpuBackend",
            "tg_runtime_sidecar_used": False,
        }
    ]

    summary = module.build_scale_gate_summary(
        devices=devices,
        correctness_checks=[],
        gpu_auto_recommendation={"should_add_flag": False},
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )
    proof = module._gpu_proof_status_from_summary(summary)

    assert summary["workload_taxonomy"]["promotion_scope"] == "declared_workload_class_only"
    assert summary["native_cuda_scale_gate"]["status"] == "UNSUPPORTED"
    assert summary["promotion_ready"] is False
    assert "native_cuda_runtime_unsupported" in summary["promotion_blockers"]
    assert proof["gpu_evidence_status"] == "unsupported"
    assert proof["gpu_proof"] is False
    assert proof["native_gpu_unavailable"] is True


def test_gpu_pipeline_helpers_should_summarize_bottleneck_stage():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_summary", "benchmarks/run_gpu_benchmarks.py"
    )

    sample = module.extract_gpu_pipeline_breakdown(
        {
            "pipeline": {
                "host_file_read_time_ms": 1000.0,
                "host_preprocess_time_ms": 50.0,
                "host_to_pinned_copy_time_ms": 100.0,
                "transfer_time_ms": 200.0,
                "kernel_time_ms": 180.0,
                "cpu_staging_time_ms": 90.0,
                "wall_time_ms": 1500.0,
            }
        },
        source="scale_native_stats",
        source_label="1GB GPU 0 native stats",
        size_label="1GB",
        process_median_s=3.0,
    )

    assert sample["source"] == "scale_native_stats"
    assert sample["size_label"] == "1GB"
    assert sample["stage_times_ms"]["host_file_read"] == 1000.0
    assert sample["stage_times_ms"]["host_preprocess"] == 50.0
    assert sample["stage_times_ms"]["unattributed_process_or_host_tail"] == 350.0

    summary = module.summarize_gpu_pipeline_bottlenecks([sample])

    assert summary["status"] == "ADVISORY"
    assert summary["sample_count"] == 1
    assert summary["pipeline_sample_sources"] == ["scale_native_stats"]
    assert summary["dominant_stage"] == "host_file_read"
    assert summary["dominant_stage_share_pct"] > 45.0
    assert summary["stage_totals_ms"]["unattributed_process_or_host_tail"] == 350.0
    next_steps = module.build_gpu_readiness_next_steps(summary)
    assert next_steps[0]["target"] == "host_file_read"
    assert "before changing CUDA kernels" in next_steps[0]["action"]


def test_gpu_pipeline_helpers_should_report_not_available_without_samples():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_missing", "benchmarks/run_gpu_benchmarks.py"
    )

    summary = module.summarize_gpu_pipeline_bottlenecks([])

    assert summary == {
        "status": "NOT_AVAILABLE",
        "sample_count": 0,
        "pipeline_sample_sources": [],
        "dominant_stage": None,
        "dominant_stage_share_pct": None,
        "stage_totals_ms": {},
        "samples": [],
        "reason": "No native GPU pipeline samples were available.",
    }
    assert module.build_gpu_readiness_next_steps(summary) == []


def test_gpu_pipeline_next_steps_should_not_optimize_from_runtime_probe_only():
    module = _load_script_module(
        "run_gpu_benchmarks_pipeline_runtime_only", "benchmarks/run_gpu_benchmarks.py"
    )
    sample = module.extract_gpu_pipeline_breakdown(
        {
            "pipeline": {
                "host_file_read_time_ms": 1.0,
                "transfer_time_ms": 2.0,
                "kernel_time_ms": 10.0,
                "wall_time_ms": 13.0,
            }
        },
        source="runtime_probe",
        source_label="small runtime probe",
    )

    summary = module.summarize_gpu_pipeline_bottlenecks([sample])
    next_steps = module.build_gpu_readiness_next_steps(summary)

    assert summary["status"] == "ADVISORY"
    assert summary["pipeline_sample_sources"] == ["runtime_probe"]
    assert next_steps[0]["target"] == "scale_native_stats"
    assert "actual-scale" in next_steps[0]["action"]


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


def test_run_gpu_benchmarks_should_use_native_inventory_for_pytorch_unsupported_device(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_benchmarks_native_inventory",
        "benchmarks/run_gpu_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    sidecar_python = tmp_path / "python.exe"
    tg_binary.write_text("binary", encoding="utf-8")
    sidecar_python.write_text("python", encoding="utf-8")
    commands: list[str] = []
    correctness_devices: list[int] = []

    monkeypatch.setattr(
        module,
        "probe_gpu_devices",
        lambda _sidecar_python: {
            "available": True,
            "torch_version": "2.6.0",
            "devices": [
                {
                    "device_id": 1,
                    "name": "NVIDIA GeForce RTX 5070",
                    "capability": [12, 0],
                    "vram_capacity_mb": 12226,
                    "operational": False,
                    "error": "no kernel image is available for execution on the device",
                }
            ],
            "warnings": ["PyTorch does not support sm_120"],
        },
    )
    monkeypatch.setattr(
        module,
        "probe_native_gpu_devices",
        lambda *, tg_binary, env: {
            "available": True,
            "devices": [
                {
                    "device_id": 1,
                    "vram_capacity_mb": 12227,
                    "native_operational": True,
                    "operational": True,
                }
            ],
            "warnings": [],
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

    def _fake_correctness_check(**kwargs):
        correctness_devices.append(kwargs["device_id"])
        return {
            "device_id": kwargs["device_id"],
            "pattern": kwargs["pattern"],
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }

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

    device = payload["devices"][0]
    gpu_row = payload["rows"][0]["gpu"][0]
    assert device["device_id"] == 1
    assert device["name"] == "NVIDIA GeForce RTX 5070"
    assert device["operational"] is True
    assert device["torch_operational"] is False
    assert device["native_operational"] is True
    assert gpu_row["status"] == "PASS"
    assert gpu_row["tg_runtime_backend"] == "NativeGpuBackend"
    assert any("--gpu-device-ids 1" in command for command in commands)
    assert correctness_devices == [1]


def test_run_gpu_benchmarks_should_skip_sidecar_contaminated_native_runtime_for_scale_gates(
    monkeypatch, tmp_path
):
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
            "routing_backend": "NativeGpuBackend",
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
    assert gpu0["tg_runtime_backend"] == "NativeGpuBackend"
    assert gpu0["tg_runtime_sidecar_used"] is True
    assert "requires a CUDA-enabled native tg binary" in gpu0["stderr"]
    assert gpu0["promotion_evidence"] is False
    assert "not GPU acceleration proof" in gpu0["not_gpu_proof_reason"]
    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["native_gpu_unavailable"] is True
    assert "sidecar" in payload["not_gpu_proof_reason"]
    scale_summary = payload["scale_gate_summary"]
    assert scale_summary["workload_taxonomy"]["promotion_scope"] == ("declared_workload_class_only")
    assert scale_summary["workload_evidence_status"] == "native_cuda_runtime_unsupported"
    assert payload["scale_gate_summary"] == {
        "benchmark_surface": "python-gpu-scale",
        "workload_class": "single_pattern_cold_grep",
        "workload_taxonomy": module.build_gpu_workload_taxonomy(),
        "promotion_evidence_contract": {
            "promotion_scope": "declared_workload_class_only",
            "required_runtime_backend": "NativeGpuBackend",
            "required_sidecar_used": False,
            "required_workload_class": "single_pattern_cold_grep",
            "required_correctness_sizes": ["1GB", "5GB"],
            "required_speed_baselines": ["rg", "tg_cpu"],
            "fair_many_pattern_baseline": "rg -F -e ... -e ...",
            "candidate_workload_classes": [
                "many_fixed_patterns_single_dispatch",
                "resident_repeated_query",
            ],
            "sidecar_routing_counts_as_promotion": False,
            "fallback_or_sidecar_counts_as_gpu_proof": False,
            "public_managed_rows_must_not_be_sidecar": True,
            "many_pattern_claim_requires_fair_rg_multi_pattern_baseline": True,
        },
        "native_cuda_scale_gate": {
            "status": "UNSUPPORTED",
            "required_backend": "NativeGpuBackend",
            "observed_backends": ["NativeGpuBackend"],
            "sidecar_observed": True,
            "reason": (
                "Operational GPU devices used sidecar-contaminated routing; "
                "NativeGpuBackend is only promotion evidence when sidecar_used is false."
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
        "promotion_blockers": [
            "native_cuda_runtime_unsupported",
            "sidecar_routing_observed",
            "correctness_not_run",
            "speed_not_run",
        ],
        "workload_evidence_status": "native_cuda_runtime_unsupported",
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
                "gpu": [
                    {
                        "device_id": 0,
                        "status": "PASS",
                        "median_s": 1.0,
                        "tg_runtime_backend": "NativeGpuBackend",
                        "tg_runtime_sidecar_used": False,
                    }
                ],
            }
        ],
        correctness_checks=[],
        correctness_patterns=("gpu benchmark sentinel",),
    )

    assert recommendation["should_add_flag"] is False
    assert recommendation["winning_rows"] == []
    assert "1GB/5GB correctness" in recommendation["reason"]


def test_gpu_auto_recommendation_should_reject_sidecar_contaminated_native_rows():
    module = _load_script_module(
        "run_gpu_benchmarks_sidecar_recommendation",
        "benchmarks/run_gpu_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 10.0},
            "tg_cpu": {"status": "PASS", "median_s": 12.0},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 1.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": True,
                }
            ],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 60.0},
            "tg_cpu": {"status": "PASS", "median_s": 70.0},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 1.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": True,
                }
            ],
        },
    ]
    correctness_checks = [
        {
            "device_id": 0,
            "corpus_size_label": size_label,
            "pattern": "gpu benchmark sentinel",
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]

    recommendation = module.analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=("gpu benchmark sentinel",),
    )

    assert recommendation["should_add_flag"] is False
    assert recommendation["winning_rows"] == []
    assert "NativeGpuBackend with sidecar_used=false" in recommendation["reason"]


def test_gpu_auto_recommendation_should_require_every_required_scale_to_win():
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
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 5.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 60.0},
            "tg_cpu": {"status": "PASS", "median_s": 70.0},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 55.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
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

    assert recommendation["should_add_flag"] is False
    assert recommendation["winning_rows"] == []
    assert "every required 1GB/5GB scale" in recommendation["reason"]


def test_gpu_auto_recommendation_should_recommend_when_all_required_scales_win():
    module = _load_script_module(
        "run_gpu_benchmarks_all_required_recommendation",
        "benchmarks/run_gpu_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 10.0},
            "tg_cpu": {"status": "PASS", "median_s": 12.0},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 5.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 60.0},
            "tg_cpu": {"status": "PASS", "median_s": 70.0},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 40.0,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
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
        },
        {
            "device_id": 0,
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "speedup_vs_rg_pct": 33.33,
            "speedup_vs_tg_cpu_pct": 42.86,
        },
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


def test_run_gpu_native_correctness_should_compare_direct_rg_identity(monkeypatch, tmp_path):
    module = _load_script_module(
        "run_gpu_native_benchmarks_rg_identity_correctness",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    corpus_dir = tmp_path / "corpus"
    tg_binary.write_text("binary", encoding="utf-8")
    corpus_dir.mkdir()

    def _fake_run_command(command, **_kwargs):
        command_text = " ".join(str(part) for part in command)
        if "--json" in command_text and "--cpu" in command_text:
            stdout = (
                '{"routing_backend":"NativeCpuBackend","sidecar_used":false,'
                '"total_matches":1,"total_files":1,'
                '"matches":[{"file":"sample.log","line_number":2,"text":"ERROR sentinel"}]}'
            )
            return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if "--json" in command_text and "--gpu-device-ids" in command_text:
            stdout = (
                '{"routing_backend":"NativeGpuBackend","sidecar_used":false,'
                '"total_matches":1,"total_files":1,'
                '"matches":[{"file":"sample.log","line_number":2,"text":"ERROR sentinel"}]}'
            )
            return module.subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        rg_stdout = (
            '{"type":"begin","data":{"path":{"text":"sample.log"}}}\n'
            '{"type":"match","data":{"path":{"text":"sample.log"},"line_number":2,'
            '"lines":{"text":"ERROR sentinel\\n"},"submatches":[]}}\n'
            '{"type":"end","data":{"path":{"text":"sample.log"},"binary_offset":null,'
            '"stats":{"elapsed":{"secs":0,"nanos":1,"human":"0.000000001s"},'
            '"searches":1,"searches_with_match":1,"bytes_searched":15,'
            '"bytes_printed":15,"matched_lines":1,"matches":1}}}\n'
        )
        return module.subprocess.CompletedProcess(command, 0, stdout=rg_stdout, stderr="")

    monkeypatch.setattr(module, "_run_command", _fake_run_command)

    result = module.run_correctness_check(
        tg_binary=tg_binary,
        rg_binary="rg",
        corpus_dir=corpus_dir,
        pattern="ERROR",
        device_id=0,
        env={},
        timeout_s=5,
    )

    assert result["status"] == "PASS"
    assert result["matches_equal"] is True
    assert result["files_equal"] is True
    assert result["rg_matches_equal"] is True
    assert result["rg_files_equal"] is True
    assert result["rg_match_identity_equal"] is True
    assert result["rg_total_matches"] == 1
    assert result["gpu_total_matches"] == 1


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
    assert tg_gpu["promotion_evidence"] is False
    assert "not GPU acceleration proof" in tg_gpu["not_gpu_proof_reason"]
    assert payload["gpu_evidence_status"] == "unsupported"
    assert payload["gpu_proof"] is False
    assert payload["native_gpu_unavailable"] is True
    assert "sidecar" in payload["not_gpu_proof_reason"]
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


def test_run_gpu_native_benchmarks_should_skip_native_error_tests_when_route_unsupported(
    monkeypatch, tmp_path
):
    module = _load_script_module(
        "run_gpu_native_benchmarks_unsupported_error_tests",
        "benchmarks/run_gpu_native_benchmarks.py",
    )
    tg_binary = tmp_path / "tg.exe"
    tg_binary.write_text("binary", encoding="utf-8")

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

    def _unexpected_error_tests(**_kwargs):
        raise AssertionError("native error diagnostics require NativeGpuBackend")

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

    monkeypatch.setattr(module, "_run_command", _fake_run_command)
    monkeypatch.setattr(module, "run_gpu_error_tests", _unexpected_error_tests)

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

    assert set(payload["error_tests"]) == {
        "invalid_device",
        "nvrtc_failure",
        "timeout",
        "malformed_inputs",
    }
    assert all(entry["status"] == "UNSUPPORTED" for entry in payload["error_tests"].values())
    assert not any(error.startswith("GPU error test ") for error in payload["errors"])
    assert any(
        "GPU native error diagnostics unsupported before timing" in warning
        for warning in payload["warnings"]
    )


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


def test_run_gpu_native_pipeline_collector_should_prefer_actual_scale_stats():
    module = _load_script_module(
        "run_gpu_native_benchmarks_pipeline_collector",
        "benchmarks/run_gpu_native_benchmarks.py",
    )

    rows = [
        {
            "size_label": "1GB",
            "tg_gpu": {
                "native_stats": {"process_median_s": 3.0},
                "native_stats_pipeline": {
                    "host_file_read_time_ms": 1000.0,
                    "host_preprocess_time_ms": 50.0,
                    "transfer_time_ms": 100.0,
                    "kernel_time_ms": 90.0,
                    "wall_time_ms": 1500.0,
                },
                "runtime_probe_pipeline": {
                    "kernel_time_ms": 9999.0,
                    "wall_time_ms": 9999.0,
                },
            },
        }
    ]
    samples = module.collect_gpu_native_pipeline_samples(rows, {"enabled": False})
    summary = module.summarize_gpu_pipeline_bottlenecks(samples)

    assert len(samples) == 1
    assert samples[0]["source"] == "scale_native_stats"
    assert summary["pipeline_sample_sources"] == ["scale_native_stats"]
    assert summary["dominant_stage"] == "host_file_read"
    assert summary["stage_totals_ms"]["unattributed_process_or_host_tail"] == 450.0


def test_gpu_bottleneck_advisory_should_not_change_promotion_summary():
    module = _load_script_module(
        "run_gpu_benchmarks_advisory_not_promotion",
        "benchmarks/run_gpu_benchmarks.py",
    )
    rows = [
        {
            "size_label": "1GB",
            "size_bytes": module.GB,
            "rg": {"status": "PASS", "median_s": 0.25},
            "tg_cpu": {"status": "PASS", "median_s": 0.24},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 9.2,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
        {
            "size_label": "5GB",
            "size_bytes": 5 * module.GB,
            "rg": {"status": "PASS", "median_s": 0.28},
            "tg_cpu": {"status": "PASS", "median_s": 0.23},
            "gpu": [
                {
                    "device_id": 0,
                    "status": "PASS",
                    "median_s": 9.4,
                    "tg_runtime_backend": "NativeGpuBackend",
                    "tg_runtime_sidecar_used": False,
                }
            ],
        },
    ]
    correctness_checks = [
        {
            "device_id": 0,
            "corpus_size_label": size_label,
            "pattern": "gpu benchmark sentinel",
            "status": "PASS",
            "matches_equal": True,
            "files_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]
    advisory_summary = module.summarize_gpu_pipeline_bottlenecks([
        module.extract_gpu_pipeline_breakdown(
            {
                "pipeline": {
                    "host_file_read_time_ms": 1000.0,
                    "kernel_time_ms": 1.0,
                    "wall_time_ms": 1001.0,
                }
            },
            source="scale_native_stats",
        )
    ])

    recommendation = module.analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=("gpu benchmark sentinel",),
    )
    scale_summary = module.build_scale_gate_summary(
        devices=[
            {
                "device_id": 0,
                "operational": True,
                "tg_runtime_backend": "NativeGpuBackend",
                "tg_runtime_sidecar_used": False,
            }
        ],
        correctness_checks=correctness_checks,
        gpu_auto_recommendation=recommendation,
        correctness_patterns=("gpu benchmark sentinel",),
    )

    assert advisory_summary["status"] == "ADVISORY"
    assert recommendation["should_add_flag"] is False
    assert scale_summary["promotion_ready"] is False


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
            "rg_matches_equal": True,
            "rg_files_equal": True,
            "rg_match_identity_equal": True,
        }
        for size_label in ("1GB", "5GB")
    ]

    summary = module.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
        required_corpus_sizes=(module.GB, 5 * module.GB),
    )

    assert summary["benchmark_surface"] == "native-cuda-scale"
    assert summary["workload_class"] == "single_pattern_cold_grep"
    assert summary["workload_taxonomy"] == module.build_gpu_workload_taxonomy()
    assert summary["promotion_evidence_contract"] == {
        "promotion_scope": "declared_workload_class_only",
        "required_runtime_backend": "NativeGpuBackend",
        "required_sidecar_used": False,
        "required_workload_class": "single_pattern_cold_grep",
        "required_correctness_sizes": ["1GB", "5GB"],
        "required_speed_baselines": ["rg", "tg_cpu"],
        "fair_many_pattern_baseline": "rg -F -e ... -e ...",
        "candidate_workload_classes": [
            "many_fixed_patterns_single_dispatch",
            "resident_repeated_query",
        ],
        "sidecar_routing_counts_as_promotion": False,
        "fallback_or_sidecar_counts_as_gpu_proof": False,
        "public_managed_rows_must_not_be_sidecar": True,
        "many_pattern_claim_requires_fair_rg_multi_pattern_baseline": True,
    }
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
        "rg_passing_sizes": ["1GB", "5GB"],
        "requires_direct_rg_match_identity": True,
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
        "reason": "Native CUDA did not beat both rg and tg_cpu at every required scale.",
    }
    assert summary["promotion_blockers"] == ["speed_gate_failed"]
    assert summary["workload_evidence_status"] == "speed_gate_failed"
    assert summary["promotion_ready"] is False
    assert (
        summary["summary"]
        == "Native CUDA correctness passed, but speed/promotion failed; keep GPU experimental."
    )
