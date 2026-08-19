"""Promotion gates, crossover/throughput analysis, the argv parser, and the
advanced-corpus generators extracted from run_gpu_native_benchmarks.py
(file-size wave 3).

These functions analyze already-collected result dicts (or build synthetic
corpora on disk); none of them call the native benchmark's I/O boundary
(`_run_command`) or any other name the test suite monkeypatches on the
top-level facade module, so moving their definitions here is
behavior-neutral. run_gpu_native_benchmarks.py imports and re-exports every
name below so existing module-attribute access (module.build_parser,
module.build_native_scale_gate_summary, ...) keeps working unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from gpu_native_bench_support import (
    DEFAULT_COMMAND_TIMEOUT_S,
    DEFAULT_CORPUS_SIZES,
    DEFAULT_GPU_DEVICE_ID,
    DEFAULT_RUNS,
    DEFAULT_TIMEOUT_SIMULATION_MS,
    DEFAULT_WARMUP,
    GPU_TIMEOUT_OPTIMIZATIONS,
    MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG,
    NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
    NATIVE_SCALE_WORKLOAD_CLASS,
    _as_int,
    _format_size_label,
    default_output_path,
)
from run_gpu_benchmarks import (
    DEFAULT_SHARD_COUNT,
    FAIR_RG_MULTI_PATTERN_BASELINE,
    GB,
    GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
    MB,
    build_gpu_workload_taxonomy,
    extract_gpu_pipeline_breakdown,
    parse_corpus_sizes,
)


def analyze_crossover(rows: list[dict[str, object]]) -> dict[str, object]:
    winners = []
    best_gap = None

    for row in rows:
        rg = row.get("rg", {})
        tg_gpu = row.get("tg_gpu", {})
        rg_median = rg.get("median_s") if isinstance(rg, dict) else None
        gpu_median = tg_gpu.get("median_s") if isinstance(tg_gpu, dict) else None
        if not isinstance(rg_median, (float, int)) or not isinstance(gpu_median, (float, int)):
            continue
        ratio = round(gpu_median / rg_median, 4) if rg_median > 0 else None
        if ratio is None:
            continue
        if ratio < 1.0:
            winners.append({
                "size_label": row["size_label"],
                "gpu_rg_ratio": ratio,
            })
        if best_gap is None or ratio < best_gap["gpu_rg_ratio"]:
            best_gap = {
                "size_label": row["size_label"],
                "gpu_rg_ratio": ratio,
            }

    if winners:
        first = winners[0]
        return {
            "exists": True,
            "first_gpu_faster_than_rg": first["size_label"],
            "winning_rows": winners,
            "summary": (
                f"GPU first beats rg at {first['size_label']} with a gpu/rg ratio of "
                f"{first['gpu_rg_ratio']:.4f}."
            ),
            "recommended_optimizations": [],
        }

    if best_gap is None:
        return {
            "exists": False,
            "first_gpu_faster_than_rg": None,
            "winning_rows": [],
            "summary": "No successful GPU benchmark rows were produced.",
            "recommended_optimizations": GPU_TIMEOUT_OPTIMIZATIONS,
        }

    slower_pct = round((best_gap["gpu_rg_ratio"] - 1.0) * 100.0, 2)
    return {
        "exists": False,
        "first_gpu_faster_than_rg": None,
        "winning_rows": [],
        "best_attempt": best_gap,
        "summary": (
            f"No crossover was found. The best GPU result was at {best_gap['size_label']} with a "
            f"gpu/rg ratio of {best_gap['gpu_rg_ratio']:.4f}, leaving GPU {slower_pct:.2f}% slower than rg."
        ),
        "recommended_optimizations": GPU_TIMEOUT_OPTIMIZATIONS,
    }


def _required_size_labels(required_corpus_sizes: tuple[int, ...]) -> list[str]:
    return [_format_size_label(size_bytes) for size_bytes in required_corpus_sizes]


def _promotion_evidence_contract(required_labels: list[str]) -> dict[str, object]:
    return {
        "promotion_scope": "declared_workload_class_only",
        "required_runtime_backend": "NativeGpuBackend",
        "required_sidecar_used": False,
        "required_workload_class": NATIVE_SCALE_WORKLOAD_CLASS,
        "required_correctness_sizes": required_labels,
        "required_speed_baselines": ["rg", "tg_cpu"],
        "fair_many_pattern_baseline": FAIR_RG_MULTI_PATTERN_BASELINE,
        "candidate_workload_classes": [
            NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
            GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
        ],
        "sidecar_routing_counts_as_promotion": False,
        "fallback_or_sidecar_counts_as_gpu_proof": False,
        "public_managed_rows_must_not_be_sidecar": True,
        "many_pattern_claim_requires_fair_rg_multi_pattern_baseline": True,
        # Wave-2 hardening (2026-06-29): an independent CPU oracle that verifies
        # correctness against `rg -F -e ... -e ...` without mirroring the GPU kernel
        # is required before promotion.  The C1 agent wires oracle_status into
        # correctness_gate; this field makes the requirement machine-readable in the
        # contract so audit tooling can assert it before the oracle ships.
        "requires_independent_oracle": True,
    }


def build_many_pattern_proof_gate(
    *,
    multi_pattern: dict[str, object],
    correctness_check: dict[str, object] | None,
) -> dict[str, object]:
    patterns = multi_pattern.get("patterns")
    pattern_count = len(patterns) if isinstance(patterns, list) else 0
    gpu_stats = multi_pattern.get("gpu_stats")
    pipeline = gpu_stats.get("pipeline") if isinstance(gpu_stats, dict) else None
    if not isinstance(pipeline, dict):
        pipeline = {}
    contract = {
        "promotion_scope": "declared_workload_class_only",
        "required_workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "required_runtime_backend": "NativeGpuBackend",
        "required_sidecar_used": False,
        "required_fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
        "required_single_dispatch": True,
        "required_pattern_count": pattern_count,
        "required_speed_baselines": ["tg_cpu_sequential", "rg_multi_pattern"],
        "required_direct_rg_match_identity": True,
        "public_gpu_proof": False,
    }
    blockers: list[str] = []
    if multi_pattern.get("status") != "PASS":
        blockers.append("many_pattern_speed_or_dispatch_gate_failed")
    if multi_pattern.get("workload_class") != NATIVE_MANY_PATTERN_WORKLOAD_CLASS:
        blockers.append("many_pattern_workload_class_missing")
    if multi_pattern.get("fair_rg_baseline") != contract["required_fair_rg_baseline"]:
        blockers.append("many_pattern_fair_rg_baseline_missing")
    if pattern_count <= 1:
        blockers.append("many_pattern_pattern_count_too_low")
    pipeline_pattern_count = _as_int(pipeline.get("pattern_count"))
    if pipeline_pattern_count != pattern_count:
        blockers.append("many_pattern_pipeline_pattern_count_mismatch")
    if pipeline.get("single_dispatch") is not True:
        blockers.append("many_pattern_single_dispatch_missing")
    speedup_vs_cpu = multi_pattern.get("speedup_vs_cpu")
    if not isinstance(speedup_vs_cpu, (float, int)) or float(speedup_vs_cpu) <= 1.0:
        blockers.append("many_pattern_gpu_not_faster_than_cpu")
    speedup_vs_rg = multi_pattern.get("speedup_vs_rg_multi_pattern")
    if not isinstance(speedup_vs_rg, (float, int)) or float(speedup_vs_rg) <= 1.0:
        blockers.append("many_pattern_gpu_not_faster_than_fair_rg")
    if not isinstance(correctness_check, dict):
        blockers.append("many_pattern_correctness_missing")
    else:
        if correctness_check.get("status") != "PASS":
            blockers.append("many_pattern_correctness_not_passed")
        if correctness_check.get("matches_equal") is not True:
            blockers.append("many_pattern_cpu_gpu_match_identity_missing")
        if correctness_check.get("files_equal") is not True:
            blockers.append("many_pattern_cpu_gpu_file_identity_missing")
        if correctness_check.get("rg_matches_equal") is not True:
            blockers.append("many_pattern_rg_match_identity_missing")
        if correctness_check.get("rg_files_equal") is not True:
            blockers.append("many_pattern_rg_file_identity_missing")
        if correctness_check.get("rg_match_identity_equal") is not True:
            blockers.append("many_pattern_rg_match_identity_missing")

    blockers = list(dict.fromkeys(blockers))
    passed = not blockers
    return {
        "status": "PASS" if passed else "FAIL",
        "workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "many_pattern_gpu_proof": passed,
        "promotion_evidence": passed,
        "public_gpu_proof": False,
        "blockers": blockers,
        "contract": contract,
        "observed": {
            "status": multi_pattern.get("status"),
            "workload_class": multi_pattern.get("workload_class"),
            "fair_rg_baseline": multi_pattern.get("fair_rg_baseline"),
            "pattern_count": pattern_count,
            "pipeline_pattern_count": pipeline_pattern_count,
            "single_dispatch": pipeline.get("single_dispatch"),
            "speedup_vs_cpu": speedup_vs_cpu,
            "speedup_vs_rg_multi_pattern": speedup_vs_rg,
            "correctness_status": (
                correctness_check.get("status") if isinstance(correctness_check, dict) else None
            ),
            "rg_match_identity_equal": (
                correctness_check.get("rg_match_identity_equal")
                if isinstance(correctness_check, dict)
                else None
            ),
        },
        "summary": (
            "Many-pattern GPU proof passed for the declared workload class."
            if passed
            else "Many-pattern GPU proof is blocked; keep this workload experimental."
        ),
    }


def _passing_correctness_size_labels(
    correctness_checks: list[dict[str, object]],
    *,
    required_corpus_sizes: tuple[int, ...],
) -> list[str]:
    required_labels = set(_required_size_labels(required_corpus_sizes))
    passing = {
        str(check.get("size_label"))
        for check in correctness_checks
        if str(check.get("size_label")) in required_labels
        and check.get("status") == "PASS"
        and check.get("matches_equal") is True
        and check.get("files_equal") is True
        and check.get("rg_matches_equal") is True
        and check.get("rg_files_equal") is True
        and check.get("rg_match_identity_equal") is True
    }
    return sorted(passing, key=_required_size_labels(required_corpus_sizes).index)


def _native_speed_gate(
    rows: list[dict[str, object]],
    *,
    required_corpus_sizes: tuple[int, ...],
) -> dict[str, object]:
    required_labels = set(_required_size_labels(required_corpus_sizes))
    winning_sizes: list[str] = []
    best_attempt: dict[str, object] | None = None

    for row in rows:
        size_label = row.get("size_label")
        if not isinstance(size_label, str) or size_label not in required_labels:
            continue
        rg = row.get("rg", {})
        tg_cpu = row.get("tg_cpu", {})
        tg_gpu = row.get("tg_gpu", {})
        if not isinstance(rg, dict) or not isinstance(tg_cpu, dict) or not isinstance(tg_gpu, dict):
            continue
        rg_median = rg.get("median_s")
        tg_cpu_median = tg_cpu.get("median_s")
        gpu_median = tg_gpu.get("median_s")
        if not (
            isinstance(rg_median, (float, int))
            and isinstance(tg_cpu_median, (float, int))
            and isinstance(gpu_median, (float, int))
            and rg_median > 0
            and tg_cpu_median > 0
        ):
            continue

        attempt = {
            "size_label": size_label,
            "gpu_rg_ratio": round(float(gpu_median) / float(rg_median), 4),
            "gpu_tg_cpu_ratio": round(float(gpu_median) / float(tg_cpu_median), 4),
        }
        if attempt["gpu_rg_ratio"] < 1.0 and attempt["gpu_tg_cpu_ratio"] < 1.0:
            winning_sizes.append(size_label)
        if best_attempt is None or max(
            float(attempt["gpu_rg_ratio"]),
            float(attempt["gpu_tg_cpu_ratio"]),
        ) < max(
            float(best_attempt["gpu_rg_ratio"]),
            float(best_attempt["gpu_tg_cpu_ratio"]),
        ):
            best_attempt = attempt

    status = "PASS" if required_labels.issubset(set(winning_sizes)) else "FAIL"
    return {
        "status": status,
        "required_baselines": ["rg", "tg_cpu"],
        "winning_sizes": winning_sizes,
        "best_attempt": best_attempt,
        "reason": (
            "Native CUDA beat both rg and tg_cpu at every required scale."
            if status == "PASS"
            else "Native CUDA did not beat both rg and tg_cpu at every required scale."
        ),
    }


def _native_runtime_gate(rows: list[dict[str, object]]) -> dict[str, object]:
    observed_backends: set[str] = set()
    observed_sidecar = False
    observed_unsupported = False
    observed_native_pass = False

    for row in rows:
        tg_gpu = row.get("tg_gpu")
        if not isinstance(tg_gpu, dict):
            continue
        backend = tg_gpu.get("routing_backend")
        if backend:
            observed_backends.add(str(backend))
        sidecar_used = bool(tg_gpu.get("sidecar_used", False))
        observed_sidecar = observed_sidecar or sidecar_used
        observed_unsupported = observed_unsupported or tg_gpu.get("status") == "UNSUPPORTED"
        observed_native_pass = observed_native_pass or (
            tg_gpu.get("status") == "PASS" and backend == "NativeGpuBackend" and not sidecar_used
        )

    if observed_native_pass:
        status = "PASS"
        reason = "Native CUDA runtime route was observed."
    elif (
        observed_unsupported
        or observed_sidecar
        or (observed_backends and observed_backends != {"NativeGpuBackend"})
    ):
        status = "UNSUPPORTED"
        reason = (
            "GPU rows routed outside the native CUDA backend; sidecar-routed rows are not "
            "native CUDA speed proof."
        )
    else:
        status = "NOT_RUN"
        reason = "Native CUDA runtime route was not observed."

    return {
        "status": status,
        "required_backend": "NativeGpuBackend",
        "observed_backends": sorted(observed_backends),
        "sidecar_observed": observed_sidecar,
        "reason": reason,
    }


def _promotion_blockers(
    *,
    runtime_gate: dict[str, object],
    correctness_gate: dict[str, object],
    speed_gate: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    if runtime_gate.get("status") != "PASS":
        blockers.append("native_cuda_runtime_unsupported")
    if runtime_gate.get("sidecar_observed") is True:
        blockers.append("sidecar_routing_observed")
    correctness_status = correctness_gate.get("status")
    if correctness_status == "UNSUPPORTED":
        blockers.append("correctness_not_run")
    elif correctness_status != "PASS":
        blockers.append("correctness_gate_failed")
    speed_status = speed_gate.get("status")
    if speed_status == "NOT_RUN":
        blockers.append("speed_not_run")
    elif speed_status != "PASS":
        blockers.append("speed_gate_failed")
    return blockers


def _workload_evidence_status(
    *,
    runtime_gate: dict[str, object],
    correctness_gate: dict[str, object],
    speed_gate: dict[str, object],
    promotion_ready: bool,
) -> str:
    if promotion_ready:
        return "promotion_ready"
    if runtime_gate.get("status") != "PASS":
        return "native_cuda_runtime_unsupported"
    if correctness_gate.get("status") != "PASS":
        return "correctness_gate_failed"
    if speed_gate.get("status") != "PASS":
        return "speed_gate_failed"
    return "experimental"


def collect_gpu_native_pipeline_samples(
    rows: list[dict[str, object]],
    advanced_payload: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for row in rows:
        size_label = row.get("size_label") if isinstance(row.get("size_label"), str) else None
        tg_gpu = row.get("tg_gpu")
        if not isinstance(tg_gpu, dict):
            continue
        native_stats = tg_gpu.get("native_stats")
        native_stats_pipeline = tg_gpu.get("native_stats_pipeline")
        if isinstance(native_stats_pipeline, dict):
            sample = extract_gpu_pipeline_breakdown(
                {"pipeline": native_stats_pipeline},
                source="scale_native_stats",
                source_label=f"{size_label or 'unknown'} native GPU stats",
                size_label=size_label,
                process_median_s=(
                    native_stats.get("process_median_s") if isinstance(native_stats, dict) else None
                ),
            )
            if sample:
                samples.append(sample)
            continue
        runtime_probe_pipeline = tg_gpu.get("runtime_probe_pipeline")
        if isinstance(runtime_probe_pipeline, dict):
            sample = extract_gpu_pipeline_breakdown(
                {"pipeline": runtime_probe_pipeline},
                source="runtime_probe",
                source_label=f"{size_label or 'unknown'} runtime probe",
                size_label=size_label,
            )
            if sample:
                samples.append(sample)

    if not isinstance(advanced_payload, dict) or not advanced_payload.get("enabled", False):
        return samples

    throughput_rows = advanced_payload.get("throughput_rows")
    if isinstance(throughput_rows, list):
        for row in throughput_rows:
            if not isinstance(row, dict):
                continue
            gpu_stats = row.get("gpu_stats")
            tg_gpu = row.get("tg_gpu")
            pipeline = gpu_stats.get("pipeline") if isinstance(gpu_stats, dict) else None
            if not isinstance(pipeline, dict):
                continue
            sample = extract_gpu_pipeline_breakdown(
                {"pipeline": pipeline},
                source="throughput",
                source_label=f"{row.get('size_label') or 'unknown'} throughput",
                size_label=row.get("size_label")
                if isinstance(row.get("size_label"), str)
                else None,
                process_median_s=(
                    tg_gpu.get("process_median_s") if isinstance(tg_gpu, dict) else None
                ),
            )
            if sample:
                samples.append(sample)

    stream_overlap = advanced_payload.get("stream_overlap")
    if isinstance(stream_overlap, dict):
        gpu_stats = stream_overlap.get("gpu_stats")
        pipeline = gpu_stats.get("pipeline") if isinstance(gpu_stats, dict) else None
        if isinstance(pipeline, dict):
            sample = extract_gpu_pipeline_breakdown(
                {"pipeline": pipeline},
                source="stream_overlap",
                source_label=f"{stream_overlap.get('size_label') or 'unknown'} stream overlap",
                size_label=(
                    stream_overlap.get("size_label")
                    if isinstance(stream_overlap.get("size_label"), str)
                    else None
                ),
            )
            if sample:
                samples.append(sample)

    multi_pattern = advanced_payload.get("multi_pattern")
    if isinstance(multi_pattern, dict):
        gpu_stats = multi_pattern.get("gpu_stats")
        gpu_benchmark = multi_pattern.get("gpu")
        pipeline = gpu_stats.get("pipeline") if isinstance(gpu_stats, dict) else None
        if isinstance(pipeline, dict):
            sample = extract_gpu_pipeline_breakdown(
                {"pipeline": pipeline},
                source="multi_pattern",
                source_label="multi-pattern native GPU stats",
                process_median_s=(
                    gpu_benchmark.get("process_median_s")
                    if isinstance(gpu_benchmark, dict)
                    else None
                ),
            )
            if sample:
                samples.append(sample)

    return samples


def build_native_scale_gate_summary(
    rows: list[dict[str, object]],
    *,
    correctness_checks: list[dict[str, object]],
    required_corpus_sizes: tuple[int, ...] = (1 * GB, 5 * GB),
) -> dict[str, object]:
    required_labels = _required_size_labels(required_corpus_sizes)
    runtime_gate = _native_runtime_gate(rows)
    passing_sizes = _passing_correctness_size_labels(
        correctness_checks,
        required_corpus_sizes=required_corpus_sizes,
    )
    runtime_unsupported = runtime_gate["status"] in {"UNSUPPORTED", "NOT_RUN"}
    correctness_status = (
        "UNSUPPORTED"
        if runtime_unsupported
        else "PASS"
        if passing_sizes == required_labels
        else "FAIL"
    )
    correctness_gate = {
        "status": correctness_status,
        "required_sizes": required_labels,
        "passing_sizes": passing_sizes,
        "rg_passing_sizes": passing_sizes,
        "requires_direct_rg_match_identity": True,
        "reason": (
            "Native CUDA correctness passed at every required scale."
            if correctness_status == "PASS"
            else "Native CUDA correctness did not run on a native CUDA backend."
            if correctness_status == "UNSUPPORTED"
            else "Native CUDA correctness did not pass every required scale."
        ),
    }
    speed_gate = (
        {
            "status": "NOT_RUN",
            "required_baselines": ["rg", "tg_cpu"],
            "winning_sizes": [],
            "best_attempt": None,
            "reason": (
                "Native CUDA speed gate did not run because the runtime route was unsupported."
            ),
        }
        if runtime_unsupported
        else _native_speed_gate(rows, required_corpus_sizes=required_corpus_sizes)
    )
    promotion_ready = correctness_status == "PASS" and speed_gate["status"] == "PASS"
    if promotion_ready:
        summary = (
            "Native CUDA correctness and speed gates passed; GPU promotion evidence is present."
        )
    elif correctness_status == "PASS":
        summary = (
            "Native CUDA correctness passed, but speed/promotion failed; keep GPU experimental."
        )
    elif runtime_unsupported:
        summary = (
            "Native CUDA runtime route is unsupported; sidecar rows are not GPU promotion evidence."
        )
    else:
        summary = "Native CUDA promotion is blocked by correctness and speed gate evidence."

    return {
        "benchmark_surface": "native-cuda-scale",
        "workload_class": NATIVE_SCALE_WORKLOAD_CLASS,
        "workload_taxonomy": build_gpu_workload_taxonomy(),
        "promotion_evidence_contract": _promotion_evidence_contract(required_labels),
        "native_cuda_runtime_gate": runtime_gate,
        "correctness_gate": correctness_gate,
        "speed_gate": speed_gate,
        "promotion_blockers": _promotion_blockers(
            runtime_gate=runtime_gate,
            correctness_gate=correctness_gate,
            speed_gate=speed_gate,
        ),
        "workload_evidence_status": _workload_evidence_status(
            runtime_gate=runtime_gate,
            correctness_gate=correctness_gate,
            speed_gate=speed_gate,
            promotion_ready=promotion_ready,
        ),
        "promotion_ready": promotion_ready,
        "summary": summary,
    }


def _gpu_proof_status_from_native_summary(summary: dict[str, object]) -> dict[str, object]:
    runtime_gate = summary.get("native_cuda_runtime_gate")
    runtime_status = runtime_gate.get("status") if isinstance(runtime_gate, dict) else "UNSUPPORTED"
    promotion_ready = bool(summary.get("promotion_ready", False))
    if promotion_ready:
        return {
            "gpu_evidence_status": "promotion_ready",
            "gpu_proof": True,
            "native_gpu_unavailable": False,
            "not_gpu_proof_reason": None,
        }
    if runtime_status in {"UNSUPPORTED", "NOT_RUN"}:
        reason = (
            str(runtime_gate.get("reason") or summary.get("summary") or "")
            if isinstance(runtime_gate, dict)
            else str(summary.get("summary") or "")
        )
        return {
            "gpu_evidence_status": "unsupported",
            "gpu_proof": False,
            "native_gpu_unavailable": True,
            "not_gpu_proof_reason": reason,
        }
    return {
        "gpu_evidence_status": "experimental",
        "gpu_proof": False,
        "native_gpu_unavailable": False,
        "not_gpu_proof_reason": str(summary.get("summary") or ""),
    }


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def build_gpu_proof_summary(
    *,
    scale_gate_summary: dict[str, object],
    public_managed_gpu_proof_gate: dict[str, object],
) -> dict[str, object]:
    proof_status = _gpu_proof_status_from_native_summary(scale_gate_summary)
    runtime_gate = scale_gate_summary.get("native_cuda_runtime_gate")
    correctness_gate = scale_gate_summary.get("correctness_gate")
    speed_gate = scale_gate_summary.get("speed_gate")
    runtime_gate = runtime_gate if isinstance(runtime_gate, dict) else {}
    correctness_gate = correctness_gate if isinstance(correctness_gate, dict) else {}
    speed_gate = speed_gate if isinstance(speed_gate, dict) else {}

    public_status = str(public_managed_gpu_proof_gate.get("status") or "NOT_REQUESTED")
    public_requested = public_status != "NOT_REQUESTED"
    local_gpu_proof = bool(proof_status.get("gpu_proof", False))
    public_gpu_proof = bool(public_managed_gpu_proof_gate.get("public_gpu_proof", False))
    public_managed_ready = bool(
        public_managed_gpu_proof_gate.get("public_managed_promotion_ready", False)
    )
    scale_blockers = _string_list(scale_gate_summary.get("promotion_blockers"))
    public_blockers = _string_list(public_managed_gpu_proof_gate.get("blockers"))
    blockers = (
        public_blockers
        if public_requested
        else list(dict.fromkeys([*scale_blockers, *public_blockers]))
    )

    if public_gpu_proof and public_managed_ready:
        status = "public_promotion_ready"
        summary = "Public managed NVIDIA GPU proof passed for the declared workload class."
        next_action = "promotion-ready"
    elif public_requested:
        status = "public_promotion_blocked"
        summary = (
            "Public managed GPU proof is blocked; inspect blocker codes before making "
            "public GPU promotion claims."
        )
        next_action = "fix-public-managed-nvidia-proof-blockers"
    elif local_gpu_proof:
        status = "local_promotion_ready"
        summary = (
            "Local native CUDA proof passed, but public managed release proof was not requested."
        )
        next_action = "run-public-managed-proof-before-public-promotion"
    elif proof_status.get("gpu_evidence_status") == "unsupported":
        status = "unsupported"
        summary = "Native CUDA route is unsupported; CPU or sidecar fallback is not GPU proof."
        next_action = "fix-native-cuda-routing-before-benchmarking-speed"
    else:
        status = "experimental"
        summary = (
            "Native CUDA route produced evidence, but correctness or speed gates still block "
            "promotion."
        )
        next_action = "fix-correctness-or-speed-gates"

    public_reason = None
    if not public_gpu_proof:
        public_reason = str(public_managed_gpu_proof_gate.get("summary") or "")
    effective_gpu_evidence_status = proof_status.get("gpu_evidence_status")
    effective_native_gpu_unavailable = proof_status.get("native_gpu_unavailable")
    effective_not_gpu_proof_reason = proof_status.get("not_gpu_proof_reason")
    if public_gpu_proof and public_managed_ready:
        effective_gpu_evidence_status = "promotion_ready"
        effective_native_gpu_unavailable = False
        effective_not_gpu_proof_reason = None

    return {
        "status": status,
        "summary": summary,
        "gpu_evidence_status": effective_gpu_evidence_status,
        "local_native_gpu_proof": local_gpu_proof,
        "public_gpu_proof": public_gpu_proof,
        "public_managed_promotion_ready": public_managed_ready,
        "native_gpu_unavailable": effective_native_gpu_unavailable,
        "not_gpu_proof_reason": effective_not_gpu_proof_reason,
        "not_public_gpu_proof_reason": public_reason,
        "workload_class": scale_gate_summary.get("workload_class"),
        "public_workload_class": (
            public_managed_gpu_proof_gate.get("observed", {}).get("many_pattern_workload_class")
            if isinstance(public_managed_gpu_proof_gate.get("observed"), dict)
            else None
        ),
        "scale_gate_promotion_ready": bool(scale_gate_summary.get("promotion_ready", False)),
        "public_managed_proof_gate_status": public_status,
        "blockers": blockers,
        "scale_gate_blockers": scale_blockers,
        "public_managed_blockers": public_blockers,
        "next_action": next_action,
        "observed": {
            "runtime_gate_status": runtime_gate.get("status"),
            "correctness_gate_status": correctness_gate.get("status"),
            "speed_gate_status": speed_gate.get("status"),
            "runtime_observed_backends": runtime_gate.get("observed_backends"),
            "runtime_sidecar_observed": runtime_gate.get("sidecar_observed"),
            "public_managed_gpu_proof_gate_status": public_status,
        },
    }


def _many_pattern_proof_gate_from_advanced(
    advanced_payload: dict[str, object] | None,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    if not isinstance(advanced_payload, dict):
        return None, None
    multi_pattern = advanced_payload.get("multi_pattern")
    if not isinstance(multi_pattern, dict):
        return None, None
    proof_gate = multi_pattern.get("proof_gate")
    if not isinstance(proof_gate, dict):
        return multi_pattern, None
    return multi_pattern, proof_gate


def build_public_managed_gpu_proof_gate(
    *,
    tg_binary_metadata: dict[str, object],
    scale_gate_summary: dict[str, object],
    advanced_payload: dict[str, object] | None = None,
    requested: bool = True,
) -> dict[str, object]:
    required_sizes = ["1GB", "5GB"]
    contract = {
        "required_binary_kind": "managed-native",
        "required_native_frontdoor_flavor": "nvidia",
        "required_native_frontdoor_requested_flavor": "nvidia",
        "required_version_status": "matches",
        "required_metadata_version": "matches_expected_version",
        "required_native_frontdoor_asset_name": "nonempty_nvidia_release_asset",
        "required_benchmark_surface": "native-cuda-scale",
        "required_scale_route_workload_class": NATIVE_SCALE_WORKLOAD_CLASS,
        "required_public_workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "required_runtime_gate_status": "PASS",
        "required_correctness_gate_status": "PASS",
        "required_scale_correctness_sizes": required_sizes,
        "required_direct_rg_match_identity": True,
        "required_many_pattern_proof_gate_status": "PASS",
        "required_many_pattern_fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
        "required_many_pattern_speed_baselines": ["tg_cpu_sequential", "rg_multi_pattern"],
    }
    if not requested:
        return {
            "status": "NOT_REQUESTED",
            "public_managed_promotion_ready": False,
            "public_gpu_proof": False,
            "blockers": [],
            "contract": contract,
            "summary": (
                "Public managed GPU proof was not requested; local native CUDA evidence, if "
                "present, must not be used as public release promotion proof."
            ),
        }

    blockers: list[str] = []
    if scale_gate_summary.get("benchmark_surface") != "native-cuda-scale":
        blockers.append("native_cuda_scale_surface_missing")
    if scale_gate_summary.get("workload_class") != NATIVE_SCALE_WORKLOAD_CLASS:
        blockers.append("native_cuda_scale_workload_class_missing")
    runtime_gate = scale_gate_summary.get("native_cuda_runtime_gate")
    correctness_gate = scale_gate_summary.get("correctness_gate")
    speed_gate = scale_gate_summary.get("speed_gate")
    if not isinstance(runtime_gate, dict) or runtime_gate.get("status") != "PASS":
        blockers.append("native_cuda_runtime_gate_not_passed")
    else:
        if runtime_gate.get("sidecar_observed") is True:
            blockers.append("native_cuda_runtime_sidecar_observed")
        observed_backends = runtime_gate.get("observed_backends")
        if observed_backends != ["NativeGpuBackend"]:
            blockers.append("native_cuda_runtime_backend_not_exclusive")
    if not isinstance(correctness_gate, dict) or correctness_gate.get("status") != "PASS":
        blockers.append("native_cuda_correctness_gate_not_passed")
    else:
        if correctness_gate.get("required_sizes") != required_sizes:
            blockers.append("native_cuda_correctness_required_sizes_missing")
        if correctness_gate.get("passing_sizes") != required_sizes:
            blockers.append("native_cuda_correctness_passing_sizes_missing")
        if correctness_gate.get("rg_passing_sizes") != required_sizes:
            blockers.append("native_cuda_rg_identity_sizes_missing")
        if correctness_gate.get("requires_direct_rg_match_identity") is not True:
            blockers.append("native_cuda_direct_rg_identity_not_required")
    promotion_blockers = scale_gate_summary.get("promotion_blockers")
    multi_pattern, many_pattern_gate = _many_pattern_proof_gate_from_advanced(advanced_payload)
    if not isinstance(many_pattern_gate, dict):
        blockers.append("many_pattern_proof_gate_missing")
    else:
        if many_pattern_gate.get("status") != "PASS":
            blockers.append("many_pattern_proof_gate_not_passed")
        if many_pattern_gate.get("workload_class") != NATIVE_MANY_PATTERN_WORKLOAD_CLASS:
            blockers.append("many_pattern_workload_class_missing")
        if many_pattern_gate.get("many_pattern_gpu_proof") is not True:
            blockers.append("many_pattern_gpu_proof_missing")
        if many_pattern_gate.get("promotion_evidence") is not True:
            blockers.append("many_pattern_promotion_evidence_missing")
    if tg_binary_metadata.get("kind") != "managed-native":
        blockers.append("not_managed_native_frontdoor")
    if tg_binary_metadata.get("version_status") != "matches":
        blockers.append("managed_native_version_not_current")
    if tg_binary_metadata.get("native_frontdoor_flavor") != "nvidia":
        blockers.append("installed_frontdoor_not_nvidia")
    if tg_binary_metadata.get("native_frontdoor_requested_flavor") != "nvidia":
        blockers.append("nvidia_frontdoor_not_requested")
    metadata_status = tg_binary_metadata.get("native_frontdoor_metadata_status")
    if metadata_status != "present":
        blockers.append("managed_native_metadata_missing")
    expected_version = tg_binary_metadata.get("expected_version")
    metadata_version = tg_binary_metadata.get("native_frontdoor_metadata_version")
    if not isinstance(metadata_version, str) or not metadata_version:
        blockers.append("managed_native_metadata_version_missing")
    elif not isinstance(expected_version, str) or not expected_version:
        blockers.append("managed_native_expected_version_missing")
    elif metadata_version != expected_version:
        blockers.append("managed_native_metadata_version_mismatch")
    asset_name = tg_binary_metadata.get("native_frontdoor_asset_name")
    if not isinstance(asset_name, str) or not asset_name:
        blockers.append("managed_native_asset_name_missing")
    elif "nvidia" not in asset_name.lower():
        blockers.append("managed_native_asset_name_not_nvidia")

    passed = not blockers
    return {
        "status": "PASS" if passed else "FAIL",
        "public_managed_promotion_ready": passed,
        "public_gpu_proof": passed,
        "blockers": blockers,
        "contract": contract,
        "observed": {
            "binary_kind": tg_binary_metadata.get("kind"),
            "version_status": tg_binary_metadata.get("version_status"),
            "native_frontdoor_flavor": tg_binary_metadata.get("native_frontdoor_flavor"),
            "native_frontdoor_requested_flavor": tg_binary_metadata.get(
                "native_frontdoor_requested_flavor"
            ),
            "native_frontdoor_asset_name": tg_binary_metadata.get("native_frontdoor_asset_name"),
            "native_frontdoor_metadata_status": metadata_status,
            "native_frontdoor_metadata_version": metadata_version,
            "expected_version": expected_version,
            "scale_gate_promotion_ready": scale_gate_summary.get("promotion_ready"),
            "scale_gate_benchmark_surface": scale_gate_summary.get("benchmark_surface"),
            "scale_gate_workload_class": scale_gate_summary.get("workload_class"),
            "scale_gate_runtime_status": (
                runtime_gate.get("status") if isinstance(runtime_gate, dict) else None
            ),
            "scale_gate_correctness_status": (
                correctness_gate.get("status") if isinstance(correctness_gate, dict) else None
            ),
            "scale_gate_speed_status": (
                speed_gate.get("status") if isinstance(speed_gate, dict) else None
            ),
            "scale_gate_speed_winning_sizes": (
                speed_gate.get("winning_sizes") if isinstance(speed_gate, dict) else None
            ),
            "scale_gate_rg_passing_sizes": (
                correctness_gate.get("rg_passing_sizes")
                if isinstance(correctness_gate, dict)
                else None
            ),
            "scale_gate_promotion_blockers": promotion_blockers,
            "many_pattern_proof_gate_status": (
                many_pattern_gate.get("status") if isinstance(many_pattern_gate, dict) else None
            ),
            "many_pattern_workload_class": (
                many_pattern_gate.get("workload_class")
                if isinstance(many_pattern_gate, dict)
                else None
            ),
            "many_pattern_fair_rg_baseline": (
                multi_pattern.get("fair_rg_baseline") if isinstance(multi_pattern, dict) else None
            ),
            "many_pattern_speedup_vs_cpu": (
                multi_pattern.get("speedup_vs_cpu") if isinstance(multi_pattern, dict) else None
            ),
            "many_pattern_speedup_vs_rg_multi_pattern": (
                multi_pattern.get("speedup_vs_rg_multi_pattern")
                if isinstance(multi_pattern, dict)
                else None
            ),
        },
        "summary": (
            "Public managed NVIDIA native front door and many-pattern native CUDA proof passed."
            if passed
            else "Public managed GPU proof is blocked; do not promote public GPU acceleration."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the native GPU search path against rg and tg --cpu across corpus sizes.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="Machine-readable output artifact path.",
    )
    parser.add_argument(
        "--binary",
        help="Path to tg binary to benchmark. Defaults to rust_core/target/release/tg(.exe).",
    )
    parser.add_argument(
        "--corpus-sizes",
        type=parse_corpus_sizes,
        default=DEFAULT_CORPUS_SIZES,
        help="Comma-separated corpus sizes such as 10MB,100MB,500MB,1GB,5GB.",
    )
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS, help="Benchmark samples per command."
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP,
        help="Warmup executions before recording timings.",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=DEFAULT_GPU_DEVICE_ID,
        help="GPU device id to benchmark with --gpu-device-ids.",
    )
    parser.add_argument(
        "--command-timeout-s",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT_S,
        help="Per-command timeout for benchmark and validation subprocesses.",
    )
    parser.add_argument(
        "--shards",
        type=int,
        default=DEFAULT_SHARD_COUNT,
        help="Number of log shard files per generated corpus.",
    )
    parser.add_argument(
        "--timeout-simulation-ms",
        type=int,
        default=DEFAULT_TIMEOUT_SIMULATION_MS,
        help="Synthetic timeout duration used for the timeout error-handling probe.",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Run advanced GPU-only measurements for overlap, transfer, multi-pattern, multi-GPU, long-line, graphs, and OOM handling.",
    )
    parser.add_argument(
        "--public-managed-proof",
        action="store_true",
        help=(
            "Require public managed NVIDIA native-front-door provenance in addition to "
            "native CUDA 1GB/5GB route/correctness and advanced many-pattern proof gates."
        ),
    )
    return parser


def analyze_throughput_target(rows: list[dict[str, object]]) -> dict[str, object]:
    winning_rows = []
    best_attempt = None

    for row in rows:
        size_bytes = int(row.get("size_bytes", 0))
        if size_bytes < 100 * MB:
            continue
        rg = row.get("rg", {})
        tg_gpu = row.get("tg_gpu", {})
        rg_median = rg.get("median_s") if isinstance(rg, dict) else None
        gpu_median = tg_gpu.get("median_s") if isinstance(tg_gpu, dict) else None
        if not isinstance(rg_median, (float, int)) or not isinstance(gpu_median, (float, int)):
            continue
        if float(gpu_median) <= 0:
            continue

        speedup_vs_rg = round(float(rg_median) / float(gpu_median), 4)
        row.setdefault("tg_gpu", {})["speedup_vs_rg"] = speedup_vs_rg
        candidate = {"size_label": row["size_label"], "speedup_vs_rg": speedup_vs_rg}
        if best_attempt is None or speedup_vs_rg > best_attempt["speedup_vs_rg"]:
            best_attempt = candidate
        if speedup_vs_rg >= MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG:
            winning_rows.append(candidate)

    if winning_rows:
        first = winning_rows[0]
        return {
            "met": True,
            "winning_rows": winning_rows,
            "summary": (
                f"GPU reached at least {MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG:.0f}x rg throughput at "
                f"{first['size_label']} (speedup {first['speedup_vs_rg']:.4f}x)."
            ),
        }

    if best_attempt is None:
        return {
            "met": False,
            "winning_rows": [],
            "best_attempt": None,
            "summary": "No qualifying GPU throughput rows were produced for sizes >=100MB.",
        }

    return {
        "met": False,
        "winning_rows": [],
        "best_attempt": best_attempt,
        "summary": (
            f"GPU did not reach {MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG:.0f}x rg throughput; best result was "
            f"{best_attempt['size_label']} at {best_attempt['speedup_vs_rg']:.4f}x."
        ),
    }


def _get_row_for_size(rows: list[dict[str, object]], size_label: str) -> dict[str, object]:
    for row in rows:
        if row.get("size_label") == size_label:
            return row
    raise KeyError(f"benchmark row not found for {size_label}")


def _build_long_line(target_len: int, pattern: str, seed: int) -> str:
    prefix = f"line-{seed:06d} "
    suffix = f" {pattern} tail-{seed:06d}"
    filler_len = max(1, target_len - len(prefix) - len(suffix))
    return f"{prefix}{'x' * filler_len}{suffix}\n"


def create_long_line_corpus(
    output_dir: Path, *, target_bytes: int, pattern: str
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / "long_lines.log"
    total_bytes = 0
    total_lines = 0
    line_sizes = (512, 10 * 1024, 100 * 1024)
    with file_path.open("w", encoding="utf-8") as handle:
        while total_bytes < target_bytes:
            line = _build_long_line(line_sizes[total_lines % len(line_sizes)], pattern, total_lines)
            encoded = line.encode("utf-8")
            handle.write(line)
            total_bytes += len(encoded)
            total_lines += 1
    return {
        "corpus_dir": output_dir,
        "actual_bytes": total_bytes,
        "file_count": 1,
        "total_lines": total_lines,
        "pattern": pattern,
    }


def create_cuda_graph_corpus(
    output_dir: Path, *, file_count: int, pattern: str
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    repeated = "padding block for cuda graph batches " * 96
    body = f"INFO graph capture bootstrap\n{repeated}\n{pattern}\nWARN graph replay footer\n"
    total_bytes = 0
    for index in range(file_count):
        file_path = output_dir / f"batch-{index:03}.log"
        file_path.write_text(body, encoding="utf-8")
        total_bytes += file_path.stat().st_size
    return {
        "corpus_dir": output_dir,
        "actual_bytes": total_bytes,
        "file_count": file_count,
        "pattern": pattern,
    }


def create_advanced_throughput_corpus(
    output_dir: Path,
    *,
    target_bytes: int,
    patterns: list[str],
    shard_count: int,
    line_bytes: int,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_paths = [output_dir / f"shard_{index:02d}.log" for index in range(shard_count)]
    handles = [file_path.open("w", encoding="utf-8") for file_path in file_paths]
    total_bytes = 0
    total_lines = 0
    pattern_counts = dict.fromkeys(patterns, 0)
    filler_pattern = "INFO advanced throughput filler"
    estimated_lines = max(1, target_bytes // max(1, line_bytes))
    match_interval = max(1, estimated_lines // max(len(patterns) * 2, 1))
    next_pattern_index = 0

    try:
        while total_bytes < target_bytes:
            shard_id = total_lines % shard_count
            use_pattern = next_pattern_index < len(patterns) and total_lines % match_interval == 0
            pattern = patterns[next_pattern_index] if use_pattern else filler_pattern
            line = _build_long_line(line_bytes, pattern, total_lines)
            encoded = line.encode("utf-8")
            handles[shard_id].write(line)
            total_bytes += len(encoded)
            total_lines += 1
            if use_pattern:
                pattern_counts[pattern] += 1
                next_pattern_index += 1

        while next_pattern_index < len(patterns):
            shard_id = total_lines % shard_count
            pattern = patterns[next_pattern_index]
            line = _build_long_line(line_bytes, pattern, total_lines)
            encoded = line.encode("utf-8")
            handles[shard_id].write(line)
            total_bytes += len(encoded)
            total_lines += 1
            pattern_counts[pattern] += 1
            next_pattern_index += 1
    finally:
        for handle in handles:
            handle.close()

    return {
        "corpus_dir": output_dir,
        "actual_bytes": total_bytes,
        "file_count": shard_count,
        "total_lines": total_lines,
        "line_bytes": line_bytes,
        "pattern_counts": pattern_counts,
    }
