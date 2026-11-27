from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_gpu_native_benchmarks_impl(
    facade: Any,
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    corpus_sizes: tuple[int, ...],
    runs: int,
    warmup: int,
    device_id: int,
    command_timeout_s: int,
    shard_count: int,
    benchmark_pattern: str,
    timeout_simulation_ms: int,
    advanced: bool,
) -> dict[str, object]:
    env = facade._build_command_env()
    rows: list[dict[str, object]] = []
    correctness_checks: list[dict[str, object]] = []
    warnings: list[str] = [f"Timeout validation is {facade.DEFAULT_TIMEOUT_DESCRIPTION}."]
    errors: list[str] = []
    runtime_probe_dir = facade.create_runtime_probe_fixture(bench_dir / "runtime_probe")
    runtime_probe = facade.probe_native_gpu_runtime_backend(
        tg_binary=tg_binary,
        corpus_dir=runtime_probe_dir,
        pattern=benchmark_pattern,
        device_id=device_id,
        env=env,
        timeout_s=command_timeout_s,
    )
    if runtime_probe.get("status") != "PASS":
        diagnostic = str(runtime_probe.get("error") or "native GPU runtime probe failed")
        warnings.append(f"GPU native runtime unsupported before timing: {diagnostic}")

    for size_bytes in corpus_sizes:
        size_label = facade._format_size_label(size_bytes)
        corpus_dir = bench_dir / size_label
        corpus_info = facade.generate_gpu_scale_corpus(
            corpus_dir,
            target_bytes=size_bytes,
            shard_count=shard_count,
        )
        actual_bytes = int(corpus_info["actual_bytes"])
        pattern_counts = corpus_info.get("pattern_counts")
        expected_matches = (
            int(pattern_counts.get(benchmark_pattern, 0)) > 0
            if isinstance(pattern_counts, dict)
            else True
        )
        allow_no_match = not expected_matches

        rg_result = facade.benchmark_search_command(
            facade.build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
            allow_no_match=allow_no_match,
        )
        tg_cpu_result = facade.benchmark_search_command(
            facade.build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
            allow_no_match=allow_no_match,
        )
        if runtime_probe.get("status") == "PASS":
            tg_gpu_result = facade.benchmark_search_command(
                facade.build_tg_gpu_search_command(
                    tg_binary, benchmark_pattern, corpus_dir, device_id
                ),
                env=env,
                runs=runs,
                warmup=warmup,
                timeout_s=command_timeout_s,
                corpus_bytes=actual_bytes,
                allow_no_match=allow_no_match,
            )
            tg_gpu_result["routing_backend"] = runtime_probe.get("routing_backend")
            tg_gpu_result["routing_reason"] = runtime_probe.get("routing_reason")
            tg_gpu_result["sidecar_used"] = runtime_probe.get("sidecar_used")
            if isinstance(runtime_probe.get("pipeline"), dict):
                tg_gpu_result["runtime_probe_pipeline"] = runtime_probe["pipeline"]
            native_stats_result = facade.benchmark_json_metric_command(
                facade.build_tg_gpu_native_stats_command(
                    tg_binary,
                    [benchmark_pattern],
                    corpus_dir,
                    [device_id],
                    summary_only=True,
                ),
                env=env,
                runs=1,
                warmup=0,
                timeout_s=command_timeout_s,
                corpus_bytes=actual_bytes,
                metric_path=("pipeline", "wall_time_ms"),
                metric_scale=0.001,
            )
            native_stats_payload = (
                native_stats_result.pop("payload", {})
                if isinstance(native_stats_result.get("payload"), dict)
                else {}
            )
            tg_gpu_result["native_stats"] = native_stats_result
            if isinstance(native_stats_payload.get("pipeline"), dict):
                tg_gpu_result["native_stats_pipeline"] = native_stats_payload["pipeline"]
        else:
            diagnostic = str(runtime_probe.get("error") or "native GPU runtime probe failed")
            warnings.append(f"GPU native runtime unsupported at {size_label}: {diagnostic}")
            tg_gpu_result = {
                "status": runtime_probe.get("status", "FAIL"),
                "median_s": None,
                "samples_s": [],
                "stderr": diagnostic,
                "command": runtime_probe.get("command"),
                "throughput_bytes_s": None,
                "routing_backend": runtime_probe.get("routing_backend"),
                "routing_reason": runtime_probe.get("routing_reason"),
                "sidecar_used": runtime_probe.get("sidecar_used"),
                "promotion_evidence": False,
                "not_gpu_proof_reason": (
                    str(runtime_probe.get("not_gpu_proof_reason"))
                    if runtime_probe.get("not_gpu_proof_reason")
                    else (
                        "Requested GPU execution did not produce NativeGpuBackend "
                        "with sidecar_used=false; this is CPU/sidecar compatibility "
                        "output, not GPU acceleration proof."
                    )
                ),
            }
            if isinstance(runtime_probe.get("pipeline"), dict):
                tg_gpu_result["runtime_probe_pipeline"] = runtime_probe["pipeline"]
        if (
            isinstance(rg_result.get("median_s"), (float, int))
            and isinstance(tg_gpu_result.get("median_s"), (float, int))
            and float(rg_result["median_s"]) > 0
        ):
            tg_gpu_result["ratio_vs_rg"] = round(
                float(tg_gpu_result["median_s"]) / float(rg_result["median_s"]),
                4,
            )
            tg_gpu_result["speedup_vs_rg"] = round(
                float(rg_result["median_s"]) / float(tg_gpu_result["median_s"]),
                4,
            )
        else:
            tg_gpu_result["ratio_vs_rg"] = None
            tg_gpu_result["speedup_vs_rg"] = None

        row = {
            "size_label": size_label,
            "size_bytes": size_bytes,
            "actual_bytes": actual_bytes,
            "file_count": corpus_info["file_count"],
            "total_lines": corpus_info["total_lines"],
            "pattern_counts": corpus_info["pattern_counts"],
            "expected_match": expected_matches,
            "rg": rg_result,
            "tg_cpu": tg_cpu_result,
            "tg_gpu": tg_gpu_result,
        }
        rows.append(row)

        if runtime_probe.get("status") == "PASS":
            correctness = facade.run_correctness_check(
                tg_binary=tg_binary,
                rg_binary=rg_binary,
                corpus_dir=corpus_dir,
                pattern=benchmark_pattern,
                device_id=device_id,
                env=env,
                timeout_s=command_timeout_s,
            )
        else:
            correctness = {
                "status": runtime_probe.get("status", "FAIL"),
                "error": str(runtime_probe.get("error") or "native GPU runtime probe failed"),
                "matches_equal": False,
                "files_equal": False,
                "routing_backend": runtime_probe.get("routing_backend"),
                "routing_reason": runtime_probe.get("routing_reason"),
                "sidecar_used": runtime_probe.get("sidecar_used"),
            }
        correctness["size_label"] = size_label
        correctness["size_bytes"] = size_bytes
        if correctness.get("status") == "UNSUPPORTED":
            errors.append(
                f"GPU correctness unsupported at {size_label}: {correctness.get('error', '')}"
            )
        elif not correctness.get("matches_equal"):
            errors.append(f"GPU correctness mismatch at {size_label}.")
        correctness_checks.append(correctness)

        for candidate, name in (
            (rg_result, "rg"),
            (tg_cpu_result, "tg_cpu"),
            (tg_gpu_result, "tg_gpu"),
        ):
            if candidate.get("status") != "PASS":
                errors.append(
                    f"{name} benchmark failed at {size_label}: {candidate.get('stderr', '')}"
                )

    if runtime_probe.get("status") == "PASS":
        error_tests = facade.run_gpu_error_tests(
            tg_binary=tg_binary,
            corpus_dir=bench_dir,
            device_id=device_id,
            timeout_s=command_timeout_s,
            timeout_simulation_ms=timeout_simulation_ms,
        )
    else:
        diagnostic = str(runtime_probe.get("error") or "native GPU runtime route unsupported")
        warnings.append(f"GPU native error diagnostics unsupported before timing: {diagnostic}")
        error_tests = facade.build_unsupported_native_gpu_error_tests(
            runtime_probe,
            timeout_simulation_ms=timeout_simulation_ms,
        )
    for name, payload in error_tests.items():
        if payload.get("status") == "UNSUPPORTED":
            continue
        if payload.get("status") != "PASS":
            diagnostic = payload.get("stderr") or payload.get("error") or "no diagnostic"
            errors.append(f"GPU error test {name} failed: {diagnostic}")

    advanced_payload: dict[str, object] = {"enabled": False}
    if advanced:
        advanced_payload, advanced_warnings, advanced_errors = (
            facade.run_advanced_gpu_native_benchmarks(
                tg_binary=tg_binary,
                rg_binary=rg_binary,
                bench_dir=bench_dir,
                rows=rows,
                runs=runs,
                warmup=warmup,
                device_id=device_id,
                command_timeout_s=command_timeout_s,
            )
        )
        warnings.extend(advanced_warnings)
        errors.extend(advanced_errors)

    crossover = facade.analyze_crossover(rows)
    throughput_rows = advanced_payload.get("throughput_rows") if advanced else None
    if not isinstance(throughput_rows, list):
        throughput_rows = rows
    throughput_target = facade.analyze_throughput_target(throughput_rows)
    if not throughput_target.get("met"):
        errors.append(str(throughput_target.get("summary", "GPU throughput target was not met.")))
    gpu_pipeline_samples = facade.collect_gpu_native_pipeline_samples(rows, advanced_payload)
    gpu_bottleneck_summary = facade.summarize_gpu_pipeline_bottlenecks(gpu_pipeline_samples)

    scale_gate_summary = facade.build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
    )

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": facade._format_size_label(size_bytes), "bytes": size_bytes}
            for size_bytes in corpus_sizes
        ],
        "rows": rows,
        "correctness_checks": correctness_checks,
        "error_tests": error_tests,
        "crossover": crossover,
        "throughput_target": throughput_target,
        "scale_gate_summary": scale_gate_summary,
        **facade._gpu_proof_status_from_native_summary(scale_gate_summary),
        "gpu_bottleneck_summary": gpu_bottleneck_summary,
        "gpu_readiness_next_steps": facade.build_gpu_readiness_next_steps(gpu_bottleneck_summary),
        "advanced": advanced_payload,
        "warnings": warnings,
        "errors": errors,
        "benchmark_pattern": benchmark_pattern,
        "gpu_device_id": device_id,
        "command_timeout_s": command_timeout_s,
    }


def run_advanced_gpu_native_benchmarks_impl(
    facade: Any,
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    rows: list[dict[str, object]],
    runs: int,
    warmup: int,
    device_id: int,
    command_timeout_s: int,
) -> tuple[dict[str, object], list[str], list[str]]:
    env = facade._build_command_env()
    warnings: list[str] = []
    errors: list[str] = []
    advanced: dict[str, object] = {"enabled": True}

    one_gib_row = facade._get_row_for_size(rows, "1GB")
    one_gib_corpus = bench_dir / "1GB"
    one_gib_actual_bytes = int(one_gib_row.get("actual_bytes", 0))
    multi_gpu_device_ids = [device_id, facade.DEFAULT_MULTI_GPU_DEVICE_ID]

    throughput_patterns = [
        f"ERROR advanced throughput sentinel {index:02d}"
        for index in range(facade.DEFAULT_ADVANCED_THROUGHPUT_PATTERN_COUNT)
    ]
    throughput_rows = []
    for size_bytes in (100 * facade.MB, 500 * facade.MB, 1 * facade.GB):
        size_label = facade._format_size_label(size_bytes)
        throughput_info = facade.create_advanced_throughput_corpus(
            bench_dir / f"advanced_throughput_{size_label}",
            target_bytes=size_bytes,
            patterns=throughput_patterns,
            shard_count=facade.DEFAULT_SHARD_COUNT,
            line_bytes=facade.DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
        )
        throughput_corpus = Path(throughput_info["corpus_dir"])
        actual_bytes = int(throughput_info["actual_bytes"])
        rg_group = facade.benchmark_command_group(
            [
                facade.build_rg_search_command(rg_binary, pattern, throughput_corpus)
                for pattern in throughput_patterns
            ],
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            workload_bytes=actual_bytes * len(throughput_patterns),
        )
        gpu_group = facade.benchmark_json_metric_command(
            facade.build_tg_gpu_native_stats_command(
                tg_binary,
                throughput_patterns,
                throughput_corpus,
                multi_gpu_device_ids,
                max_batch_bytes=facade.DEFAULT_ADVANCED_THROUGHPUT_MAX_BATCH_BYTES,
                summary_only=True,
            ),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes * len(throughput_patterns),
            metric_path=("pipeline", "wall_time_ms"),
            metric_scale=0.001,
        )
        gpu_stats = (
            gpu_group.pop("payload", {}) if isinstance(gpu_group.get("payload"), dict) else {}
        )
        if (
            isinstance(rg_group.get("median_s"), (float, int))
            and isinstance(gpu_group.get("median_s"), (float, int))
            and float(rg_group["median_s"]) > 0
        ):
            gpu_group["ratio_vs_rg"] = round(
                float(gpu_group["median_s"]) / float(rg_group["median_s"]),
                4,
            )
            gpu_group["speedup_vs_rg"] = round(
                float(rg_group["median_s"]) / float(gpu_group["median_s"]),
                4,
            )
        else:
            gpu_group["ratio_vs_rg"] = None
            gpu_group["speedup_vs_rg"] = None
        throughput_rows.append({
            "size_label": size_label,
            "size_bytes": size_bytes,
            "actual_bytes": actual_bytes,
            "pattern_count": len(throughput_patterns),
            "file_count": throughput_info["file_count"],
            "total_lines": throughput_info["total_lines"],
            "line_bytes": throughput_info["line_bytes"],
            "rg": rg_group,
            "tg_gpu": gpu_group,
            "gpu_stats": gpu_stats,
        })

    advanced["throughput_rows"] = throughput_rows
    advanced["throughput_workload"] = {
        "pattern_count": len(throughput_patterns),
        "line_bytes": facade.DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
        "device_ids": multi_gpu_device_ids,
        "mode": "multi-pattern sparse-match long-line native GPU summary benchmark",
    }

    stream_stats = facade._run_json_command(
        facade.build_tg_gpu_native_stats_command(
            tg_binary, [facade.DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    stream_pipeline = stream_stats.get("pipeline", {})
    stream_transfer_ms = float(stream_pipeline.get("transfer_time_ms", 0.0))
    stream_kernel_ms = float(stream_pipeline.get("kernel_time_ms", 0.0))
    stream_wall_ms = float(stream_pipeline.get("wall_time_ms", 0.0))
    stream_serial_ms = stream_transfer_ms + stream_kernel_ms
    stream_benefit_pct = (
        round(((stream_serial_ms - stream_wall_ms) / stream_serial_ms) * 100.0, 2)
        if stream_serial_ms > 0
        else None
    )
    stream_status = (
        "PASS"
        if int(stream_pipeline.get("stream_count", 0)) >= 2
        and int(stream_pipeline.get("overlapped_batches", 0)) >= 1
        and stream_wall_ms > 0
        and stream_wall_ms < stream_serial_ms
        else "FAIL"
    )
    advanced["stream_overlap"] = {
        "status": stream_status,
        "size_label": "1GB",
        "device_id": device_id,
        "benefit_pct": stream_benefit_pct,
        "serial_device_time_ms": round(stream_serial_ms, 3),
        "wall_time_ms": round(stream_wall_ms, 3),
        "gpu_stats": stream_stats,
    }
    if stream_status != "PASS":
        errors.append("CUDA stream overlap benchmark did not demonstrate overlapped execution.")

    pinned_transfer = facade._run_json_command(
        facade.build_tg_gpu_transfer_benchmark_command(
            tg_binary,
            device_id=device_id,
            total_bytes=facade.DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES,
            batch_bytes=facade.DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES,
            memory_kind="pinned",
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    pageable_transfer = facade._run_json_command(
        facade.build_tg_gpu_transfer_benchmark_command(
            tg_binary,
            device_id=device_id,
            total_bytes=facade.DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES,
            batch_bytes=facade.DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES,
            memory_kind="pageable",
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    pinned_tp = float(pinned_transfer.get("throughput_bytes_per_s", 0.0))
    pageable_tp = float(pageable_transfer.get("throughput_bytes_per_s", 0.0))
    transfer_status = "PASS" if pinned_tp > pageable_tp > 0 else "FAIL"
    advanced["transfer_throughput"] = {
        "status": transfer_status,
        "device_id": device_id,
        "pinned": pinned_transfer,
        "pageable": pageable_transfer,
        "pinned_vs_pageable_ratio": round(pinned_tp / pageable_tp, 4) if pageable_tp > 0 else None,
    }
    if transfer_status != "PASS":
        errors.append("Pinned-memory transfer benchmark did not outperform pageable transfers.")

    multi_patterns = list(facade.DEFAULT_CORRECTNESS_PATTERNS)
    multi_pattern_gpu_benchmark = facade.benchmark_json_metric_command(
        facade.build_tg_gpu_native_stats_command(
            tg_binary, multi_patterns, one_gib_corpus, [device_id]
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes * len(multi_patterns),
        metric_path=("pipeline", "wall_time_ms"),
        metric_scale=0.001,
    )
    multi_pattern_cpu_benchmark = facade.benchmark_command_group(
        [
            facade.build_tg_cpu_search_command(tg_binary, pattern, one_gib_corpus)
            for pattern in multi_patterns
        ],
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        workload_bytes=one_gib_actual_bytes * len(multi_patterns),
    )
    multi_pattern_rg_benchmark = facade.benchmark_search_command(
        facade.build_rg_multi_pattern_search_command(rg_binary, multi_patterns, one_gib_corpus),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes,
    )
    multi_pattern_correctness = facade.run_many_pattern_correctness_check(
        tg_binary=tg_binary,
        rg_binary=rg_binary,
        corpus_dir=one_gib_corpus,
        patterns=multi_patterns,
        device_id=device_id,
        env=env,
        timeout_s=command_timeout_s,
    )
    multi_pattern_gpu_stats = (
        multi_pattern_gpu_benchmark.pop("payload", {})
        if isinstance(multi_pattern_gpu_benchmark.get("payload"), dict)
        else {}
    )
    multi_pattern_gpu_median = multi_pattern_gpu_benchmark.get("median_s")
    multi_pattern_cpu_median = multi_pattern_cpu_benchmark.get("median_s")
    multi_pattern_rg_median = multi_pattern_rg_benchmark.get("median_s")
    multi_pattern_speedup = (
        round(float(multi_pattern_cpu_median) / float(multi_pattern_gpu_median), 4)
        if isinstance(multi_pattern_gpu_median, (float, int))
        and isinstance(multi_pattern_cpu_median, (float, int))
        and float(multi_pattern_gpu_median) > 0
        else None
    )
    multi_pattern_rg_speedup = (
        round(float(multi_pattern_rg_median) / float(multi_pattern_gpu_median), 4)
        if isinstance(multi_pattern_gpu_median, (float, int))
        and isinstance(multi_pattern_rg_median, (float, int))
        and float(multi_pattern_gpu_median) > 0
        else None
    )
    multi_pattern_pipeline = multi_pattern_gpu_stats.get("pipeline", {})
    multi_pattern_status = (
        "PASS"
        if multi_pattern_gpu_benchmark.get("status") == "PASS"
        and multi_pattern_cpu_benchmark.get("status") == "PASS"
        and multi_pattern_rg_benchmark.get("status") == "PASS"
        and int(multi_pattern_pipeline.get("pattern_count", 0)) == len(multi_patterns)
        and bool(multi_pattern_pipeline.get("single_dispatch"))
        and multi_pattern_speedup is not None
        and multi_pattern_speedup > 1.0
        and multi_pattern_rg_speedup is not None
        and multi_pattern_rg_speedup > 1.0
        else "FAIL"
    )
    multi_pattern_payload = {
        "status": multi_pattern_status,
        "workload_class": facade.NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
        "patterns": multi_patterns,
        "gpu": multi_pattern_gpu_benchmark,
        "cpu_sequential": multi_pattern_cpu_benchmark,
        "rg_multi_pattern": multi_pattern_rg_benchmark,
        "correctness_check": multi_pattern_correctness,
        "speedup_vs_cpu": multi_pattern_speedup,
        "speedup_vs_rg_multi_pattern": multi_pattern_rg_speedup,
        "gpu_stats": multi_pattern_gpu_stats,
    }
    multi_pattern_payload["proof_gate"] = facade.build_many_pattern_proof_gate(
        multi_pattern=multi_pattern_payload,
        correctness_check=multi_pattern_correctness,
    )
    advanced["multi_pattern"] = multi_pattern_payload
    if multi_pattern_status != "PASS":
        errors.append(
            "Multi-pattern GPU benchmark did not beat both sequential CPU and fair rg "
            "multi-pattern execution."
        )
    proof_gate = multi_pattern_payload["proof_gate"]
    if isinstance(proof_gate, dict) and proof_gate.get("status") != "PASS":
        errors.append("Many-pattern GPU proof gate did not pass direct rg identity evidence.")

    single_gpu_benchmark = facade.benchmark_json_metric_command(
        facade.build_tg_gpu_native_stats_command(
            tg_binary, [facade.DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes,
        metric_path=("pipeline", "wall_time_ms"),
        metric_scale=0.001,
    )
    multi_gpu_benchmark = facade.benchmark_json_metric_command(
        facade.build_tg_gpu_native_stats_command(
            tg_binary,
            [facade.DEFAULT_BENCHMARK_PATTERN],
            one_gib_corpus,
            multi_gpu_device_ids,
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes,
        metric_path=("pipeline", "wall_time_ms"),
        metric_scale=0.001,
    )
    multi_gpu_single_stats = (
        single_gpu_benchmark.pop("payload", {})
        if isinstance(single_gpu_benchmark.get("payload"), dict)
        else {}
    )
    multi_gpu_stats = (
        multi_gpu_benchmark.pop("payload", {})
        if isinstance(multi_gpu_benchmark.get("payload"), dict)
        else {}
    )
    single_gpu_median = single_gpu_benchmark.get("median_s")
    multi_gpu_median = multi_gpu_benchmark.get("median_s")
    multi_gpu_improvement_pct = (
        round(
            ((float(single_gpu_median) - float(multi_gpu_median)) / float(single_gpu_median))
            * 100.0,
            2,
        )
        if isinstance(single_gpu_median, (float, int))
        and isinstance(multi_gpu_median, (float, int))
        and float(single_gpu_median) > 0
        else None
    )
    multi_gpu_device_stats = multi_gpu_stats.get("device_stats", [])
    multi_gpu_total_files = int(multi_gpu_stats.get("searched_files", 0))
    distribution_balanced = bool(multi_gpu_total_files) and all(
        int(device_stats.get("searched_files", 0)) * 10 >= multi_gpu_total_files
        for device_stats in multi_gpu_device_stats
        if isinstance(device_stats, dict)
    )
    multi_gpu_status = (
        "PASS"
        if single_gpu_benchmark.get("status") == "PASS"
        and multi_gpu_benchmark.get("status") == "PASS"
        and int(multi_gpu_stats.get("total_matches", -1))
        == int(multi_gpu_single_stats.get("total_matches", -2))
        and len(multi_gpu_device_stats) >= 2
        and distribution_balanced
        and multi_gpu_improvement_pct is not None
        and multi_gpu_improvement_pct >= facade.MIN_MULTI_GPU_IMPROVEMENT_PCT
        else "FAIL"
    )
    advanced["multi_gpu"] = {
        "status": multi_gpu_status,
        "device_ids": multi_gpu_device_ids,
        "single_gpu": single_gpu_benchmark,
        "multi_gpu": multi_gpu_benchmark,
        "single_gpu_stats": multi_gpu_single_stats,
        "multi_gpu_stats": multi_gpu_stats,
        "improvement_pct": multi_gpu_improvement_pct,
        "distribution_balanced": distribution_balanced,
    }
    if multi_gpu_status != "PASS":
        errors.append(
            f"Multi-GPU benchmark did not achieve the required {facade.MIN_MULTI_GPU_IMPROVEMENT_PCT:.0f}% improvement."
        )

    long_line_info = facade.create_long_line_corpus(
        bench_dir / "advanced_long_lines",
        target_bytes=facade.DEFAULT_ADVANCED_LONG_LINE_TARGET_BYTES,
        pattern=facade.DEFAULT_ADVANCED_LONG_LINE_PATTERN,
    )
    long_line_corpus = Path(long_line_info["corpus_dir"])
    long_line_actual_bytes = int(long_line_info["actual_bytes"])
    long_line_gpu_benchmark = facade.benchmark_search_command(
        facade.build_tg_gpu_search_command(
            tg_binary, facade.DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus, device_id
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_cpu_benchmark = facade.benchmark_search_command(
        facade.build_tg_cpu_search_command(
            tg_binary, facade.DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_stats = facade._run_json_command(
        facade.build_tg_gpu_native_stats_command(
            tg_binary, [facade.DEFAULT_ADVANCED_LONG_LINE_PATTERN], long_line_corpus, [device_id]
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    long_line_gpu_median = long_line_gpu_benchmark.get("median_s")
    long_line_cpu_median = long_line_cpu_benchmark.get("median_s")
    long_line_speedup = (
        round(float(long_line_cpu_median) / float(long_line_gpu_median), 4)
        if isinstance(long_line_gpu_median, (float, int))
        and isinstance(long_line_cpu_median, (float, int))
        and float(long_line_gpu_median) > 0
        else None
    )
    long_line_pipeline = long_line_stats.get("pipeline", {})
    long_line_status = (
        "PASS"
        if long_line_gpu_benchmark.get("status") == "PASS"
        and long_line_cpu_benchmark.get("status") == "PASS"
        and int(long_line_pipeline.get("long_line_count", 0)) > 0
        and int(long_line_pipeline.get("warp_dispatch_count", 0)) >= 1
        and int(long_line_pipeline.get("block_dispatch_count", 0)) >= 1
        else "FAIL"
    )
    advanced["long_lines"] = {
        "status": long_line_status,
        "gpu": long_line_gpu_benchmark,
        "cpu": long_line_cpu_benchmark,
        "gpu_speedup_vs_cpu": long_line_speedup,
        "gpu_stats": long_line_stats,
    }
    if long_line_status != "PASS":
        errors.append("Long-line GPU benchmark did not exercise warp/block dispatch as expected.")

    cuda_graph_info = facade.create_cuda_graph_corpus(
        bench_dir / "advanced_cuda_graphs",
        file_count=facade.DEFAULT_ADVANCED_GRAPH_FILE_COUNT,
        pattern=facade.DEFAULT_ADVANCED_GRAPH_PATTERN,
    )
    cuda_graph_corpus = Path(cuda_graph_info["corpus_dir"])
    cuda_graph_benchmark = facade._run_json_command(
        facade.build_tg_gpu_cuda_graph_benchmark_command(
            tg_binary,
            pattern=facade.DEFAULT_ADVANCED_GRAPH_PATTERN,
            corpus_dir=cuda_graph_corpus,
            device_id=device_id,
            max_batch_bytes=facade.DEFAULT_ADVANCED_GRAPH_BATCH_BYTES,
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    cuda_graph_status = (
        "PASS"
        if bool(cuda_graph_benchmark.get("results_identical"))
        and float(cuda_graph_benchmark.get("wall_time_reduction_pct", 0.0)) >= 10.0
        else "FAIL"
    )
    advanced["cuda_graphs"] = {"status": cuda_graph_status, **cuda_graph_benchmark}
    if cuda_graph_status != "PASS":
        errors.append("CUDA graph benchmark did not show the required >=10% wall-time reduction.")

    oom_result = facade._run_command(
        facade.build_tg_gpu_oom_probe_command(
            tg_binary,
            device_id=device_id,
            bytes_to_allocate=facade.DEFAULT_ADVANCED_OOM_BYTES,
        ),
        env=env,
        capture_output=True,
        timeout_s=command_timeout_s,
    )
    oom_status = "FAIL"
    oom_exit_code = None
    oom_stderr = "command timed out"
    if isinstance(oom_result, subprocess.TimeoutExpired):
        oom_stderr = f"command timed out after {command_timeout_s}s"
    else:
        oom_exit_code = oom_result.returncode
        oom_stderr = (oom_result.stderr or "").strip()
        if oom_result.returncode == 2 and "out of memory" in oom_stderr.lower():
            oom_status = "PASS"
    advanced["oom_validation"] = {
        "status": oom_status,
        "device_id": device_id,
        "requested_bytes": facade.DEFAULT_ADVANCED_OOM_BYTES,
        "exit_code": oom_exit_code,
        "stderr": oom_stderr,
        "simulated": False,
    }
    if oom_status != "PASS":
        errors.append("GPU OOM validation did not return a clear out-of-memory error message.")

    return advanced, warnings, errors
