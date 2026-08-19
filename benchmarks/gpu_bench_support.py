"""Pure/support helpers extracted from run_gpu_benchmarks.py (file-size wave 3).

Constants, command builders, parsers, and pure analysis/gate functions that
never call an I/O boundary function and are never monkeypatched directly by
the test suite. run_gpu_benchmarks.py imports and re-exports these names so
existing module-attribute access (module.GB, module.build_scale_gate_summary,
...) keeps working unchanged.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

KB = 1024


MB = 1024 * KB


GB = 1024 * MB


DEFAULT_CORPUS_SIZES = (1 * MB, 10 * MB, 100 * MB, 1 * GB, 5 * GB)


DEFAULT_RUNS = 1


DEFAULT_WARMUP = 0


DEFAULT_SHARD_COUNT = 8


DEFAULT_SEED = 42


DEFAULT_BENCHMARK_PATTERN = "gpu benchmark sentinel"


DEFAULT_CORRECTNESS_PATTERNS = (
    "gpu benchmark sentinel",
    "WARN retry budget exhausted",
    "Database connection timeout",
)


GPU_SCALE_WORKLOAD_CLASS = "single_pattern_cold_grep"


GPU_MANY_PATTERN_WORKLOAD_CLASS = "many_fixed_patterns_single_dispatch"


GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS = "resident_repeated_query"


FAIR_RG_MULTI_PATTERN_BASELINE = "rg -F -e ... -e ..."


RECOMMENDATION_REQUIRED_CORPUS_SIZES = (1 * GB, 5 * GB)


GPU_RECOMMENDATION_MIN_SPEEDUP_PCT = 20.0


PAYLOAD_FILLER = "payload=" + ("0123456789abcdef" * 224)


GPU_PIPELINE_STAGE_FIELDS = {
    "host_file_read": ("host_file_read_time_ms",),
    "host_preprocess": ("host_preprocess_time_ms",),
    "host_to_pinned_copy": ("host_to_pinned_copy_time_ms",),
    "transfer": ("transfer_time_ms",),
    "kernel": ("kernel_time_ms",),
    "cpu_staging": ("cpu_staging_time_ms",),
}


def build_gpu_workload_taxonomy() -> dict[str, object]:
    return {
        "promotion_scope": "declared_workload_class_only",
        "measured_scale_gate": {
            "workload_class": GPU_SCALE_WORKLOAD_CLASS,
            "promotion_eligible": True,
            "required_proof": (
                "NativeGpuBackend with sidecar_used=false, required-scale correctness, "
                "and end-to-end speed wins over both rg and tg_cpu"
            ),
        },
        "candidate_workload_classes": [
            {
                "workload_class": GPU_MANY_PATTERN_WORKLOAD_CLASS,
                "status": "candidate_until_required_scale_correctness_and_fair_rg_speed_proof",
                "fair_rg_baseline": FAIR_RG_MULTI_PATTERN_BASELINE,
            },
            {
                "workload_class": GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
                "status": "candidate_not_measured",
                "fair_rg_baseline": "not_applicable_until_benchmark_exists",
            },
        ],
        "non_proof_routes": ["GpuSidecar", "NativeCpuBackend", "sidecar_used=true"],
    }


def _is_skippable_cybert_exception(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "connection refused",
            "actively refused",
            "failed to establish a new connection",
            "timed out",
        )
    )


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_run_gpu_benchmarks.json"


def parse_corpus_sizes(value: str) -> tuple[int, ...]:
    sizes: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        match = re.fullmatch(r"(?i)(\d+)([KMG]?B)?", token)
        if match is None:
            raise argparse.ArgumentTypeError(
                "corpus sizes must be a comma-separated list like 1MB,10MB,100MB,1GB"
            )
        value_int = int(match.group(1))
        unit = (match.group(2) or "B").upper()
        multiplier = {
            "B": 1,
            "KB": KB,
            "MB": MB,
            "GB": GB,
        }.get(unit)
        if multiplier is None:
            raise argparse.ArgumentTypeError(f"unsupported size unit: {unit}")
        size_bytes = value_int * multiplier
        if size_bytes <= 0:
            raise argparse.ArgumentTypeError("all corpus sizes must be positive")
        sizes.append(size_bytes)
    if not sizes:
        raise argparse.ArgumentTypeError("at least one corpus size is required")
    return tuple(sizes)


def _format_size_label(size_bytes: int) -> str:
    if size_bytes % GB == 0:
        return f"{size_bytes // GB}GB"
    if size_bytes % MB == 0:
        return f"{size_bytes // MB}MB"
    if size_bytes % KB == 0:
        return f"{size_bytes // KB}KB"
    return f"{size_bytes}B"


def _build_command_env(sidecar_python: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    if sidecar_python is not None:
        env["TG_SIDECAR_PYTHON"] = str(sidecar_python)
    return env


def _recreate_dir(output_dir: Path) -> None:
    if output_dir.exists():
        for child in output_dir.rglob("*"):
            if child.is_file() or child.is_symlink():
                child.unlink()
        for child in sorted(output_dir.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
    output_dir.mkdir(parents=True, exist_ok=True)


def _build_corpus_line(line_index: int, shard_id: int) -> tuple[str, str | None]:
    trace_id = f"{shard_id:02d}-{line_index:08d}"
    if line_index % 2048 == 0:
        return (
            "2026-03-16T12:00:00Z ERROR gpu benchmark sentinel "
            f"trace_id={trace_id} shard={shard_id} message=GPU crossover probe {PAYLOAD_FILLER}\n",
            DEFAULT_BENCHMARK_PATTERN,
        )
    if line_index % 173 == 0:
        return (
            "2026-03-16T12:00:00Z WARN retry budget exhausted "
            f"trace_id={trace_id} shard={shard_id} service=worker {PAYLOAD_FILLER}\n",
            "WARN retry budget exhausted",
        )
    if line_index % 347 == 0:
        return (
            "2026-03-16T12:00:00Z ERROR Database connection timeout "
            f"trace_id={trace_id} shard={shard_id} service=database {PAYLOAD_FILLER}\n",
            "Database connection timeout",
        )
    return (
        "2026-03-16T12:00:00Z INFO request completed "
        f"trace_id={trace_id} shard={shard_id} duration_ms={(line_index % 29) + 1} {PAYLOAD_FILLER}\n",
        None,
    )


def _command_display(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(command)


def build_rg_search_command(rg_binary: str, pattern: str, corpus_dir: Path) -> list[str]:
    return [rg_binary, "--no-ignore", pattern, str(corpus_dir.relative_to(ROOT_DIR))]


def build_tg_cpu_search_command(tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [str(tg_binary), "search", "--no-ignore", pattern, str(corpus_dir.relative_to(ROOT_DIR))]


def build_tg_gpu_search_command(
    tg_binary: Path, pattern: str, corpus_dir: Path, device_id: int
) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--gpu-device-ids",
        str(device_id),
        "--no-ignore",
        pattern,
        str(corpus_dir.relative_to(ROOT_DIR)),
    ]


def build_tg_gpu_native_stats_command(
    tg_binary: Path,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
    device_ids: list[int] | tuple[int, ...],
) -> list[str]:
    command = [str(tg_binary), "__gpu-native-stats"]
    for pattern in patterns:
        command.extend(["--pattern", pattern])
    command.extend(["--path", str(corpus_dir.relative_to(ROOT_DIR))])
    command.extend(["--gpu-device-ids", ",".join(str(device_id) for device_id in device_ids)])
    command.extend(["--no-ignore", "--summary-only"])
    return command


def _numeric_ms(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, (float, int)):
        return round(float(value), 3)
    return None


def extract_gpu_pipeline_breakdown(
    payload: dict[str, object],
    *,
    source: str | None = None,
    source_label: str | None = None,
    size_label: str | None = None,
    process_median_s: float | int | None = None,
) -> dict[str, object]:
    raw_pipeline = payload.get("pipeline")
    pipeline = raw_pipeline if isinstance(raw_pipeline, dict) else payload
    if not isinstance(pipeline, dict):
        return {}

    stage_times: dict[str, float] = {}
    for stage, fields in GPU_PIPELINE_STAGE_FIELDS.items():
        if stage == "cpu_staging":
            continue
        total_ms = 0.0
        for field in fields:
            value = _numeric_ms(pipeline, field)
            if value is not None:
                total_ms += value
        if total_ms > 0:
            stage_times[stage] = round(total_ms, 3)
    cpu_staging_total = _numeric_ms(pipeline, "cpu_staging_time_ms")
    detailed_host_total = sum(
        stage_times.get(stage, 0.0)
        for stage in ("host_file_read", "host_preprocess", "host_to_pinned_copy")
    )
    if cpu_staging_total is not None:
        cpu_staging_residual = round(max(0.0, cpu_staging_total - detailed_host_total), 3)
        if cpu_staging_residual > 0:
            stage_times["cpu_staging"] = cpu_staging_residual

    wall_time_ms = _numeric_ms(pipeline, "wall_time_ms")
    if isinstance(process_median_s, (float, int)):
        device_basis_ms = (
            wall_time_ms
            if wall_time_ms is not None
            else stage_times.get("transfer", 0.0) + stage_times.get("kernel", 0.0)
        )
        known_host_ms = sum(
            stage_times.get(stage, 0.0)
            for stage in (
                "host_file_read",
                "host_preprocess",
                "host_to_pinned_copy",
                "cpu_staging",
            )
        )
        basis_ms = known_host_ms + device_basis_ms
        tail_ms = round(max(0.0, float(process_median_s) * 1000.0 - basis_ms), 3)
        if tail_ms > 0:
            stage_times["unattributed_process_or_host_tail"] = tail_ms

    if not stage_times:
        return {}

    denominator = sum(stage_times.values())
    stage_shares = {
        stage: round(value / denominator * 100.0, 2) if denominator > 0 else 0.0
        for stage, value in stage_times.items()
    }
    breakdown: dict[str, object] = {
        "source": source or "unknown",
        "source_label": source_label,
        "size_label": size_label,
        "stage_times_ms": stage_times,
        "stage_shares_pct": stage_shares,
        "wall_time_ms": wall_time_ms,
    }
    if isinstance(process_median_s, (float, int)):
        breakdown["process_median_s"] = round(float(process_median_s), 6)
        breakdown["unattributed_process_or_host_tail_ms"] = stage_times.get(
            "unattributed_process_or_host_tail", 0.0
        )
    return breakdown


def summarize_gpu_pipeline_bottlenecks(
    samples: list[dict[str, object]],
) -> dict[str, object]:
    valid_samples = [
        sample
        for sample in samples
        if isinstance(sample.get("stage_times_ms"), dict) and sample["stage_times_ms"]
    ]
    if not valid_samples:
        return {
            "status": "NOT_AVAILABLE",
            "sample_count": 0,
            "pipeline_sample_sources": [],
            "dominant_stage": None,
            "dominant_stage_share_pct": None,
            "stage_totals_ms": {},
            "samples": [],
            "reason": "No native GPU pipeline samples were available.",
        }

    stage_totals: dict[str, float] = {}
    sources: list[str] = []
    for sample in valid_samples:
        source = str(sample.get("source") or "unknown")
        if source not in sources:
            sources.append(source)
        for stage, raw_value in sample["stage_times_ms"].items():
            if not isinstance(raw_value, (float, int)):
                continue
            stage_totals[stage] = round(stage_totals.get(stage, 0.0) + float(raw_value), 3)

    total_ms = sum(stage_totals.values())
    dominant_stage = max(stage_totals, key=stage_totals.get) if stage_totals else None
    dominant_share = (
        round(stage_totals[dominant_stage] / total_ms * 100.0, 2)
        if dominant_stage is not None and total_ms > 0
        else None
    )
    return {
        "status": "ADVISORY",
        "sample_count": len(valid_samples),
        "pipeline_sample_sources": sources,
        "dominant_stage": dominant_stage,
        "dominant_stage_share_pct": dominant_share,
        "stage_totals_ms": stage_totals,
        "samples": valid_samples,
        "reason": "GPU bottleneck summary is diagnostic only and is not promotion evidence.",
    }


def build_gpu_readiness_next_steps(summary: dict[str, object]) -> list[dict[str, object]]:
    if summary.get("status") != "ADVISORY":
        return []
    sources = summary.get("pipeline_sample_sources")
    if sources == ["runtime_probe"]:
        return [
            {
                "priority": 1,
                "target": "scale_native_stats",
                "action": (
                    "Collect actual-scale native GPU pipeline samples before choosing an "
                    "optimization target."
                ),
                "evidence_status": "runtime-probe-only",
            }
        ]

    dominant_stage = summary.get("dominant_stage")
    actions = {
        "host_file_read": "Reduce host-side file read and batching cost before changing CUDA kernels.",
        "host_preprocess": "Reduce host preprocessing and line-map preparation before changing CUDA kernels.",
        "host_to_pinned_copy": "Reuse pinned host buffers and tune batch sizes before changing CUDA kernels.",
        "transfer": "Improve transfer batching and stream overlap before changing CUDA kernels.",
        "cpu_staging": "Reduce result materialization and CPU staging before changing CUDA kernels.",
        "unattributed_process_or_host_tail": (
            "Instrument host-side tail work before changing CUDA kernels."
        ),
        "kernel": (
            "Investigate PFAC/Aho-Corasick or bit-parallel multi-pattern kernels only after "
            "transfer and staging costs are not dominant."
        ),
    }
    if not isinstance(dominant_stage, str) or dominant_stage not in actions:
        return []
    return [
        {
            "priority": 1,
            "target": dominant_stage,
            "action": actions[dominant_stage],
            "evidence_status": "advisory",
        }
    ]


def _parse_match_output(stdout: str) -> tuple[int, list[str]]:
    files: set[str] = set()
    match_count = 0
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match_count += 1
        file_path, _sep, _rest = line.partition(":")
        if file_path:
            files.add(file_path)
    return match_count, sorted(files)


def merge_gpu_device_inventory(
    torch_devices: list[dict[str, object]],
    native_devices: list[dict[str, object]],
) -> list[dict[str, object]]:
    merged: dict[int, dict[str, object]] = {}
    order: list[int] = []

    for device in torch_devices:
        raw_device_id = device.get("device_id")
        if not isinstance(raw_device_id, int) and not str(raw_device_id).isdigit():
            continue
        device_id = int(raw_device_id)
        merged_device = dict(device)
        merged_device["device_id"] = device_id
        merged_device["torch_operational"] = bool(device.get("operational", False))
        merged_device.setdefault("native_operational", False)
        merged[device_id] = merged_device
        order.append(device_id)

    for device in native_devices:
        raw_device_id = device.get("device_id")
        if not isinstance(raw_device_id, int) and not str(raw_device_id).isdigit():
            continue
        device_id = int(raw_device_id)
        native_operational = bool(
            device.get("native_operational", device.get("operational", False))
        )
        if device_id not in merged:
            merged_device = dict(device)
            merged_device["device_id"] = device_id
            merged_device.setdefault("torch_operational", False)
            merged_device["native_operational"] = native_operational
            merged_device["operational"] = native_operational
            merged[device_id] = merged_device
            order.append(device_id)
            continue

        merged_device = merged[device_id]
        if native_operational and merged_device.get("operational") is not True:
            if "error" in merged_device and "torch_error" not in merged_device:
                merged_device["torch_error"] = merged_device.pop("error")
            merged_device["operational"] = True
        merged_device["native_operational"] = native_operational
        for key, value in device.items():
            if key in {"device_id", "operational", "native_operational"}:
                continue
            if (
                key == "name"
                and str(value).startswith("CUDA device ")
                and merged_device.get("name")
            ):
                continue
            if value is not None:
                merged_device[key] = value

    return [merged[device_id] for device_id in order]


def _clean_selected_gpu_stderr(
    stderr: object,
    *,
    devices: list[dict[str, object]],
    selected_device_id: int,
    warnings: list[str],
) -> str:
    if not isinstance(stderr, str) or not stderr:
        return ""

    exact_inventory_lines = {warning.strip() for warning in warnings if warning.strip()}
    other_devices = [
        device
        for device in devices
        if str(device.get("device_id")) != str(selected_device_id)
        and not device.get("operational", False)
    ]
    cleaned_lines: list[str] = []
    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line in exact_inventory_lines:
            continue

        lower_line = line.lower()
        is_other_device_inventory = False
        for device in other_devices:
            other_id = str(device.get("device_id"))
            other_name = str(device.get("name") or "")
            other_error = str(device.get("error") or "")
            if other_name and other_name in line:
                is_other_device_inventory = True
            if other_error and other_error in line:
                is_other_device_inventory = True
            if f"gpu {other_id}" in lower_line and "unsupported" in lower_line:
                is_other_device_inventory = True
            if f"cuda:{other_id}" in lower_line and "unsupported" in lower_line:
                is_other_device_inventory = True
        if is_other_device_inventory:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _passing_required_correctness_device_ids(
    *,
    correctness_checks: list[dict[str, object]],
    correctness_patterns: tuple[str, ...],
    required_corpus_sizes: tuple[int, ...],
) -> set[str]:
    required_labels = {_format_size_label(size_bytes) for size_bytes in required_corpus_sizes}
    required_patterns = set(correctness_patterns)
    if not required_labels or not required_patterns:
        return set()

    required_cases = {
        (size_label, pattern) for size_label in required_labels for pattern in required_patterns
    }
    passed_cases_by_device: dict[str, set[tuple[str, str]]] = {}
    for check in correctness_checks:
        device_id = check.get("device_id")
        size_label = check.get("corpus_size_label")
        pattern = check.get("pattern")
        if device_id is None or not isinstance(size_label, str) or not isinstance(pattern, str):
            continue
        if size_label not in required_labels or pattern not in required_patterns:
            continue
        if not (
            check.get("status") == "PASS"
            and check.get("matches_equal") is True
            and check.get("files_equal") is True
        ):
            continue
        passed_cases_by_device.setdefault(str(device_id), set()).add((size_label, pattern))

    return {
        device_id
        for device_id, passed_cases in passed_cases_by_device.items()
        if required_cases.issubset(passed_cases)
    }


def analyze_gpu_auto_recommendation(
    rows: list[dict[str, object]],
    *,
    correctness_checks: list[dict[str, object]] | None = None,
    correctness_patterns: tuple[str, ...] = DEFAULT_CORRECTNESS_PATTERNS,
    required_corpus_sizes: tuple[int, ...] = RECOMMENDATION_REQUIRED_CORPUS_SIZES,
    min_speedup_pct: float = GPU_RECOMMENDATION_MIN_SPEEDUP_PCT,
) -> dict[str, object]:
    correctness_passing_device_ids = _passing_required_correctness_device_ids(
        correctness_checks=correctness_checks or [],
        correctness_patterns=correctness_patterns,
        required_corpus_sizes=required_corpus_sizes,
    )
    required_size_bytes = set(required_corpus_sizes)
    required_size_labels = "/".join(_format_size_label(size) for size in required_corpus_sizes)
    if not correctness_passing_device_ids:
        return {
            "should_add_flag": False,
            "reason": (
                "No GPU has passing "
                f"{required_size_labels} correctness checks for every required pattern."
            ),
            "winning_rows": [],
        }

    winners: list[dict[str, object]] = []
    winning_sizes_by_device: dict[str, set[int]] = {}
    skipped_non_native_route = False
    for row in rows:
        if row.get("size_bytes") not in required_size_bytes:
            continue
        rg_result = row.get("rg", {})
        tg_cpu_result = row.get("tg_cpu", {})
        rg_median = rg_result.get("median_s") if isinstance(rg_result, dict) else None
        tg_cpu_median = tg_cpu_result.get("median_s") if isinstance(tg_cpu_result, dict) else None
        if (
            not isinstance(rg_median, (int, float))
            or not isinstance(tg_cpu_median, (int, float))
            or rg_median <= 0
            or tg_cpu_median <= 0
        ):
            continue
        for gpu_result in row.get("gpu", []):
            device_id = gpu_result.get("device_id")
            if str(device_id) not in correctness_passing_device_ids:
                continue
            if not (
                gpu_result.get("tg_runtime_backend") == "NativeGpuBackend"
                and gpu_result.get("tg_runtime_sidecar_used") is False
            ):
                skipped_non_native_route = True
                continue
            gpu_median = gpu_result.get("median_s")
            if gpu_result.get("status") != "PASS" or not isinstance(gpu_median, (int, float)):
                continue
            speedup_vs_rg_pct = round((rg_median - gpu_median) / rg_median * 100.0, 2)
            speedup_vs_tg_cpu_pct = round(
                (tg_cpu_median - gpu_median) / tg_cpu_median * 100.0,
                2,
            )
            gpu_result["speedup_vs_rg_pct"] = speedup_vs_rg_pct
            gpu_result["speedup_vs_tg_cpu_pct"] = speedup_vs_tg_cpu_pct
            if speedup_vs_rg_pct >= min_speedup_pct and speedup_vs_tg_cpu_pct >= min_speedup_pct:
                winning_sizes_by_device.setdefault(str(device_id), set()).add(
                    int(row.get("size_bytes", 0))
                )
                winners.append({
                    "device_id": device_id,
                    "size_label": row.get("size_label"),
                    "size_bytes": row.get("size_bytes"),
                    "speedup_vs_rg_pct": speedup_vs_rg_pct,
                    "speedup_vs_tg_cpu_pct": speedup_vs_tg_cpu_pct,
                })

    qualifying_devices = {
        device_id
        for device_id, winning_sizes in winning_sizes_by_device.items()
        if required_size_bytes.issubset(winning_sizes)
    }

    if not winners or not qualifying_devices:
        if skipped_non_native_route:
            reason = (
                "No correctness-passing GPU row used NativeGpuBackend with sidecar_used=false "
                f"and beat both rg and tg_cpu by at least {min_speedup_pct:.0f}% at every "
                f"required {required_size_labels} scale."
            )
        else:
            reason = (
                "No correctness-passing GPU device beat both rg and tg_cpu by at least "
                f"{min_speedup_pct:.0f}% at every required {required_size_labels} scale."
            )
        return {
            "should_add_flag": False,
            "reason": reason,
            "winning_rows": [],
        }

    return {
        "should_add_flag": True,
        "reason": (
            "At least one GPU device passed required correctness and beat both rg and "
            f"tg_cpu by {min_speedup_pct:.0f}% or more at every required scale."
        ),
        "winning_rows": [
            winner for winner in winners if str(winner.get("device_id")) in qualifying_devices
        ],
    }


def _required_size_labels(required_corpus_sizes: tuple[int, ...]) -> list[str]:
    return [_format_size_label(size_bytes) for size_bytes in required_corpus_sizes]


def _promotion_evidence_contract(required_labels: list[str]) -> dict[str, object]:
    return {
        "promotion_scope": "declared_workload_class_only",
        "required_runtime_backend": "NativeGpuBackend",
        "required_sidecar_used": False,
        "required_workload_class": GPU_SCALE_WORKLOAD_CLASS,
        "required_correctness_sizes": required_labels,
        "required_speed_baselines": ["rg", "tg_cpu"],
        "fair_many_pattern_baseline": FAIR_RG_MULTI_PATTERN_BASELINE,
        "candidate_workload_classes": [
            GPU_MANY_PATTERN_WORKLOAD_CLASS,
            GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
        ],
        "sidecar_routing_counts_as_promotion": False,
        "fallback_or_sidecar_counts_as_gpu_proof": False,
        "public_managed_rows_must_not_be_sidecar": True,
        "many_pattern_claim_requires_fair_rg_multi_pattern_baseline": True,
    }


def _promotion_blockers(
    *,
    runtime_gate: dict[str, object],
    correctness_gate: dict[str, object],
    speed_gate: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    if runtime_gate.get("status") != "SUPPORTED":
        blockers.append("native_cuda_runtime_unsupported")
    if runtime_gate.get("sidecar_observed") is True:
        blockers.append("sidecar_routing_observed")
    correctness_status = correctness_gate.get("status")
    if correctness_status == "NOT_RUN":
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
    if runtime_gate.get("status") not in {"PASS", "SUPPORTED"}:
        return "native_cuda_runtime_unsupported"
    if correctness_gate.get("status") != "PASS":
        return "correctness_gate_failed"
    if speed_gate.get("status") != "PASS":
        return "speed_gate_failed"
    return "experimental"


def _observed_operational_backends(devices: list[dict[str, object]]) -> list[str]:
    observed = {
        str(device.get("tg_runtime_backend") or "unknown")
        for device in devices
        if device.get("operational", False)
    }
    return sorted(observed)


def _uses_native_cuda_runtime(device: dict[str, object]) -> bool:
    return (
        bool(device.get("operational", False))
        and device.get("tg_runtime_backend") == "NativeGpuBackend"
        and device.get("tg_runtime_sidecar_used") is False
    )


def _not_gpu_proof_reason(*, backend: object, sidecar_used: object) -> str:
    return (
        "Requested GPU execution did not produce NativeGpuBackend with "
        f"sidecar_used=false (routing_backend={backend or 'unknown'}, "
        f"sidecar_used={bool(sidecar_used)}); this is CPU/sidecar compatibility "
        "output, not GPU acceleration proof."
    )


def _gpu_proof_status_from_summary(summary: dict[str, object]) -> dict[str, object]:
    runtime_gate = summary.get("native_cuda_scale_gate")
    runtime_status = runtime_gate.get("status") if isinstance(runtime_gate, dict) else "UNSUPPORTED"
    promotion_ready = bool(summary.get("promotion_ready", False))
    if promotion_ready:
        return {
            "gpu_evidence_status": "promotion_ready",
            "gpu_proof": True,
            "native_gpu_unavailable": False,
            "not_gpu_proof_reason": None,
        }
    if runtime_status != "SUPPORTED":
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


def build_gpu_proof_summary(scale_gate_summary: dict[str, object]) -> dict[str, object]:
    proof_status = _gpu_proof_status_from_summary(scale_gate_summary)
    runtime_gate = scale_gate_summary.get("native_cuda_scale_gate")
    correctness_gate = scale_gate_summary.get("correctness_gate")
    speed_gate = scale_gate_summary.get("speed_gate")
    runtime_gate = runtime_gate if isinstance(runtime_gate, dict) else {}
    correctness_gate = correctness_gate if isinstance(correctness_gate, dict) else {}
    speed_gate = speed_gate if isinstance(speed_gate, dict) else {}

    local_gpu_proof = bool(proof_status.get("gpu_proof", False))
    blockers = _string_list(scale_gate_summary.get("promotion_blockers"))
    if local_gpu_proof:
        status = "local_promotion_ready"
        summary = (
            "Python GPU scale artifact observed local native CUDA promotion evidence; "
            "public managed release proof still requires the native benchmark gate."
        )
        next_action = "run-native-public-managed-proof-before-public-promotion"
    elif proof_status.get("gpu_evidence_status") == "unsupported":
        status = "unsupported"
        summary = (
            "Python GPU scale artifact is not native CUDA proof; CPU fallback, sidecar, "
            "or missing native runtime evidence blocks promotion."
        )
        next_action = "run-native-cuda-benchmark-with-cuda-enabled-tg"
    else:
        status = "experimental"
        summary = (
            "Python GPU scale artifact has native CUDA evidence, but correctness or speed "
            "gates still block promotion."
        )
        next_action = "fix-correctness-or-speed-gates"

    return {
        "status": status,
        "summary": summary,
        "gpu_evidence_status": proof_status.get("gpu_evidence_status"),
        "local_native_gpu_proof": local_gpu_proof,
        "public_gpu_proof": False,
        "public_managed_promotion_ready": False,
        "native_gpu_unavailable": proof_status.get("native_gpu_unavailable"),
        "not_gpu_proof_reason": proof_status.get("not_gpu_proof_reason"),
        "workload_class": scale_gate_summary.get("workload_class"),
        "scale_gate_promotion_ready": bool(scale_gate_summary.get("promotion_ready", False)),
        "blockers": blockers,
        "scale_gate_blockers": blockers,
        "next_action": next_action,
        "observed": {
            "runtime_gate_status": runtime_gate.get("status"),
            "correctness_gate_status": correctness_gate.get("status"),
            "speed_gate_status": speed_gate.get("status"),
            "runtime_observed_backends": runtime_gate.get("observed_backends"),
            "runtime_sidecar_observed": runtime_gate.get("sidecar_observed"),
        },
    }


def build_scale_gate_summary(
    *,
    devices: list[dict[str, object]],
    correctness_checks: list[dict[str, object]],
    gpu_auto_recommendation: dict[str, object],
    required_corpus_sizes: tuple[int, ...] = RECOMMENDATION_REQUIRED_CORPUS_SIZES,
    correctness_patterns: tuple[str, ...] = DEFAULT_CORRECTNESS_PATTERNS,
) -> dict[str, object]:
    required_labels = _required_size_labels(required_corpus_sizes)
    observed_backends = _observed_operational_backends(devices)
    sidecar_observed = any(
        bool(device.get("operational", False)) and bool(device.get("tg_runtime_sidecar_used"))
        for device in devices
    )
    has_native_cuda_backend = any(_uses_native_cuda_runtime(device) for device in devices)
    passing_device_ids = sorted(
        _passing_required_correctness_device_ids(
            correctness_checks=correctness_checks,
            correctness_patterns=correctness_patterns,
            required_corpus_sizes=required_corpus_sizes,
        )
    )

    if has_native_cuda_backend:
        native_gate = {
            "status": "SUPPORTED",
            "required_backend": "NativeGpuBackend",
            "observed_backends": observed_backends,
            "sidecar_observed": sidecar_observed,
            "reason": "At least one operational device routed through the native CUDA backend.",
        }
    else:
        if sidecar_observed and "NativeGpuBackend" in observed_backends:
            reason = (
                "Operational GPU devices used sidecar-contaminated routing; "
                "NativeGpuBackend is only promotion evidence when sidecar_used is false."
            )
        elif observed_backends:
            reason = (
                "Operational GPU devices routed outside the native CUDA backend; "
                "Python/Torch sidecar rows are not native CUDA scale proof."
            )
        else:
            reason = "No operational GPU devices were available for native CUDA scale proof."
        native_gate = {
            "status": "UNSUPPORTED",
            "required_backend": "NativeGpuBackend",
            "observed_backends": observed_backends,
            "sidecar_observed": sidecar_observed,
            "reason": reason,
        }

    if correctness_checks:
        correctness_status = "PASS" if passing_device_ids else "FAIL"
        correctness_reason = (
            "Native CUDA correctness passed at every required scale."
            if passing_device_ids
            else "Native CUDA correctness did not pass every required scale."
        )
    else:
        correctness_status = "NOT_RUN"
        correctness_reason = "Native CUDA correctness checks did not run."

    correctness_gate = {
        "status": correctness_status,
        "required_sizes": required_labels,
        "passing_device_ids": passing_device_ids,
        "reason": correctness_reason,
    }

    if not has_native_cuda_backend:
        speed_gate = {
            "status": "NOT_RUN",
            "required_baselines": ["rg", "tg_cpu"],
            "reason": (
                "Native CUDA speed gate did not run because the native CUDA scale gate "
                "is unsupported."
            ),
        }
        summary = (
            "Python GPU scale rows are unsupported for native CUDA promotion; run "
            "benchmarks/run_gpu_native_benchmarks.py with a CUDA-enabled native tg binary "
            "to evaluate correctness and speed separately."
        )
    else:
        speed_gate = {
            "status": "PASS" if gpu_auto_recommendation.get("should_add_flag") else "FAIL",
            "required_baselines": ["rg", "tg_cpu"],
            "reason": str(gpu_auto_recommendation.get("reason", "")),
        }
        summary = (
            "Native CUDA correctness and speed gates passed."
            if gpu_auto_recommendation.get("should_add_flag")
            else "Native CUDA promotion is blocked by correctness or speed gate evidence."
        )

    promotion_ready = has_native_cuda_backend and bool(
        gpu_auto_recommendation.get("should_add_flag", False)
    )
    return {
        "benchmark_surface": "python-gpu-scale",
        "workload_class": GPU_SCALE_WORKLOAD_CLASS,
        "workload_taxonomy": build_gpu_workload_taxonomy(),
        "promotion_evidence_contract": _promotion_evidence_contract(required_labels),
        "native_cuda_scale_gate": native_gate,
        "correctness_gate": correctness_gate,
        "speed_gate": speed_gate,
        "promotion_blockers": _promotion_blockers(
            runtime_gate=native_gate,
            correctness_gate=correctness_gate,
            speed_gate=speed_gate,
        ),
        "workload_evidence_status": _workload_evidence_status(
            runtime_gate=native_gate,
            correctness_gate=correctness_gate,
            speed_gate=speed_gate,
            promotion_ready=promotion_ready,
        ),
        "promotion_ready": promotion_ready,
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark explicit GPU search routing against rg/tg CPU across corpus sizes.",
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
        "--sidecar-python",
        help="Python interpreter for GPU sidecar execution. Defaults to TG_SIDECAR_PYTHON or .venv_cuda.",
    )
    parser.add_argument(
        "--corpus-sizes",
        type=parse_corpus_sizes,
        default=DEFAULT_CORPUS_SIZES,
        help="Comma-separated corpus sizes such as 1MB,10MB,100MB,1GB,5GB.",
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
        "--shards",
        type=int,
        default=DEFAULT_SHARD_COUNT,
        help="Number of log shard files per generated corpus.",
    )
    return parser
