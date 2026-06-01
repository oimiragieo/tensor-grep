from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from run_benchmarks import resolve_rg_binary  # noqa: E402

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


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_gpu_bench_data_dir() -> Path:
    """
    Resolve GPU benchmark data location. Defaults to artifacts to avoid mutating
    tracked repository fixtures during repeated local/CI benchmark runs.
    """
    override = os.environ.get("TENSOR_GREP_GPU_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "gpu_bench_data"


def resolve_gpu_sidecar_python(raw: str | None = None) -> Path | None:
    if raw:
        return Path(raw).expanduser().resolve()

    env_value = os.environ.get("TG_SIDECAR_PYTHON")
    if env_value:
        return Path(env_value).expanduser().resolve()

    candidates = []
    if os.name == "nt":
        candidates.extend([
            ROOT_DIR / ".venv_cuda" / "Scripts" / "python.exe",
            ROOT_DIR / ".venv" / "Scripts" / "python.exe",
        ])
    else:
        candidates.extend([
            ROOT_DIR / ".venv_cuda" / "bin" / "python",
            ROOT_DIR / ".venv" / "bin" / "python",
        ])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    executable = Path(sys.executable)
    return executable.resolve() if executable.exists() else None


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


def generate_gpu_scale_corpus(
    output_dir: Path,
    *,
    target_bytes: int,
    shard_count: int,
) -> dict[str, object]:
    _recreate_dir(output_dir)

    file_paths = [output_dir / f"shard_{index:02d}.log" for index in range(shard_count)]
    handles = [file_path.open("w", encoding="utf-8") for file_path in file_paths]
    total_bytes = 0
    total_lines = 0
    pattern_counts = dict.fromkeys(DEFAULT_CORRECTNESS_PATTERNS, 0)

    try:
        while total_bytes < target_bytes:
            buffers = [[] for _ in range(shard_count)]
            for _ in range(2048):
                shard_id = total_lines % shard_count
                line, matched_pattern = _build_corpus_line(total_lines, shard_id)
                encoded = line.encode("utf-8")
                if total_bytes + len(encoded) > target_bytes and total_bytes >= target_bytes:
                    break
                buffers[shard_id].append(line)
                total_bytes += len(encoded)
                total_lines += 1
                if matched_pattern is not None:
                    pattern_counts[matched_pattern] += 1
                if total_bytes >= target_bytes:
                    break
            for handle, lines in zip(handles, buffers, strict=True):
                if lines:
                    handle.write("".join(lines))
            if total_bytes >= target_bytes:
                break
    finally:
        for handle in handles:
            handle.close()

    return {
        "corpus_dir": output_dir,
        "actual_bytes": total_bytes,
        "total_lines": total_lines,
        "file_count": shard_count,
        "pattern_counts": pattern_counts,
    }


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


def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )


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


def benchmark_search_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=False)
        if (
            warmup_result.returncode == 1
            and allow_no_match
            and not (warmup_result.stderr or "").strip()
        ):
            no_match_exit_accepted = True
        elif warmup_result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (warmup_result.stderr or "").strip(),
                "command": _command_display(command),
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        start = time.perf_counter()
        result = _run_command(command, env=env, capture_output=False)
        elapsed = time.perf_counter() - start
        if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
            no_match_exit_accepted = True
        elif result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [round(sample, 6) for sample in samples],
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        samples.append(round(elapsed, 6))
        last_stderr = (result.stderr or "").strip()

    return {
        "status": "PASS",
        "median_s": round(statistics.median(samples), 6),
        "samples_s": samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


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


def run_correctness_check(
    *,
    rg_binary: str,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
) -> dict[str, object]:
    rg_command = build_rg_search_command(rg_binary, pattern, corpus_dir)
    gpu_command = build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id)
    rg_result = _run_command(rg_command, env=env, capture_output=True)
    gpu_result = _run_command(gpu_command, env=env, capture_output=True)

    if rg_result.returncode not in (0, 1):
        return {
            "device_id": device_id,
            "pattern": pattern,
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
        }
    if gpu_result.returncode not in (0, 1):
        return {
            "device_id": device_id,
            "pattern": pattern,
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
        }

    rg_matches, rg_files = _parse_match_output(rg_result.stdout or "")
    gpu_matches, gpu_files = _parse_match_output(gpu_result.stdout or "")
    return {
        "device_id": device_id,
        "pattern": pattern,
        "status": "PASS",
        "rg_matches": rg_matches,
        "gpu_matches": gpu_matches,
        "matches_equal": rg_matches == gpu_matches,
        "files_equal": rg_files == gpu_files,
        "rg_files": rg_files,
        "gpu_files": gpu_files,
    }


def probe_tg_gpu_runtime_backend(
    *,
    tg_binary: Path,
    device_id: int,
    env: dict[str, str],
    bench_dir: Path,
) -> dict[str, object]:
    probe_dir = bench_dir / "_runtime_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "gpu_runtime_probe.log"
    probe_file.write_text("tg gpu runtime probe\n", encoding="utf-8")
    try:
        probe_path = str(probe_file.relative_to(ROOT_DIR))
    except ValueError:
        probe_path = str(probe_file)
    command = [
        str(tg_binary),
        "search",
        "--gpu-device-ids",
        str(device_id),
        "--no-ignore",
        "--json",
        "tg gpu runtime probe",
        probe_path,
    ]
    result = _run_command(command, env=env, capture_output=True)
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (result.stderr or "").strip() or "GPU runtime probe failed.",
            "command": _command_display(command),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "FAIL",
            "error": f"GPU runtime probe returned invalid JSON: {exc}",
            "command": _command_display(command),
        }
    return {
        "status": "PASS",
        "routing_backend": payload.get("routing_backend"),
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": bool(payload.get("sidecar_used", False)),
        "command": _command_display(command),
        **({"pipeline": payload["pipeline"]} if isinstance(payload.get("pipeline"), dict) else {}),
    }


def probe_tg_gpu_native_stats_pipeline(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
) -> dict[str, object]:
    command = build_tg_gpu_native_stats_command(tg_binary, [pattern], corpus_dir, [device_id])
    started_at = time.perf_counter()
    try:
        result = _run_command(command, env=env, capture_output=True)
    except OSError as exc:
        return {
            "status": "FAIL",
            "stderr": str(exc),
            "command": _command_display(command),
            "process_median_s": round(time.perf_counter() - started_at, 6),
        }
    process_median_s = round(time.perf_counter() - started_at, 6)
    command_display = _command_display(command)
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "stderr": (result.stderr or "").strip(),
            "command": command_display,
            "process_median_s": process_median_s,
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "status": "FAIL",
            "stderr": f"native GPU stats returned invalid JSON: {exc}",
            "command": command_display,
            "process_median_s": process_median_s,
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("pipeline"), dict):
        return {
            "status": "FAIL",
            "stderr": "native GPU stats did not include pipeline metrics",
            "command": command_display,
            "process_median_s": process_median_s,
        }
    return {
        "status": "PASS",
        "pipeline": payload["pipeline"],
        "command": command_display,
        "process_median_s": process_median_s,
    }


def probe_gpu_devices(sidecar_python: Path | None) -> dict[str, object]:
    if sidecar_python is None or not sidecar_python.exists():
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": "GPU sidecar Python interpreter was not found.",
        }

    probe_script = """
import json
import warnings

payload = {"available": False, "torch_version": None, "devices": [], "warnings": []}
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    try:
        import torch

        payload["torch_version"] = torch.__version__
        payload["available"] = bool(torch.cuda.is_available())
        for device_id in range(torch.cuda.device_count()):
            entry = {
                "device_id": device_id,
                "name": torch.cuda.get_device_name(device_id),
                "capability": list(torch.cuda.get_device_capability(device_id)),
                "vram_capacity_mb": int(torch.cuda.get_device_properties(device_id).total_memory // (1024 * 1024)),
            }
            try:
                tensor = torch.zeros(1, device=f"cuda:{device_id}")
                entry["operational"] = True
                entry["probe_value"] = float(tensor.cpu()[0])
            except Exception as exc:
                entry["operational"] = False
                entry["error"] = str(exc)
            payload["devices"].append(entry)
    except Exception as exc:
        payload["error"] = str(exc)
    payload["warnings"] = [str(w.message) for w in caught]
print(json.dumps(payload))
"""
    env = _build_command_env(None)
    result = subprocess.run(
        [str(sidecar_python), "-c", probe_script],
        cwd=ROOT_DIR,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": (result.stderr or "").strip() or "GPU probe failed.",
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "torch_version": None,
            "devices": [],
            "warnings": [],
            "error": f"GPU probe returned invalid JSON: {exc}",
        }


def probe_native_gpu_devices(*, tg_binary: Path, env: dict[str, str]) -> dict[str, object]:
    command = [str(tg_binary), "devices", "--json"]
    try:
        result = _run_command(command, env=env, capture_output=True)
    except OSError as exc:
        return {
            "available": False,
            "devices": [],
            "warnings": [f"Native GPU inventory failed: {exc}"],
            "command": _command_display(command),
        }
    if result.returncode != 0:
        return {
            "available": False,
            "devices": [],
            "warnings": [
                "Native GPU inventory failed: "
                + ((result.stderr or "").strip() or f"exit {result.returncode}")
            ],
            "command": _command_display(command),
        }
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "devices": [],
            "warnings": [f"Native GPU inventory returned invalid JSON: {exc}"],
            "command": _command_display(command),
        }

    raw_devices = payload.get("devices", [])
    if not isinstance(raw_devices, list):
        raw_devices = []
    raw_routable_ids = payload.get("routable_device_ids")
    routable_ids = (
        {
            int(device_id)
            for device_id in raw_routable_ids
            if isinstance(device_id, int) or str(device_id).isdigit()
        }
        if isinstance(raw_routable_ids, list)
        else set()
    )

    devices: list[dict[str, object]] = []
    for raw_device in raw_devices:
        if not isinstance(raw_device, dict):
            continue
        raw_device_id = raw_device.get("device_id")
        if not isinstance(raw_device_id, int) and not str(raw_device_id).isdigit():
            continue
        device_id = int(raw_device_id)
        native_operational = (
            device_id in routable_ids if routable_ids else bool(payload.get("has_gpu"))
        )
        entry: dict[str, object] = {
            "device_id": device_id,
            "name": raw_device.get("name") or f"CUDA device {device_id}",
            "native_operational": native_operational,
            "operational": native_operational,
        }
        for key in ("capability", "vram_capacity_mb"):
            if key in raw_device:
                entry[key] = raw_device[key]
        devices.append(entry)

    return {
        "available": bool(payload.get("has_gpu")) and bool(devices),
        "devices": devices,
        "warnings": [],
        "command": _command_display(command),
    }


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


def run_gpu_scale_benchmarks(
    *,
    tg_binary: Path,
    rg_binary: str,
    bench_dir: Path,
    corpus_sizes: tuple[int, ...],
    runs: int,
    warmup: int,
    sidecar_python: Path | None,
    benchmark_pattern: str,
    correctness_patterns: tuple[str, ...],
    shard_count: int,
) -> dict[str, object]:
    probe = probe_gpu_devices(sidecar_python)
    command_env = _build_command_env(sidecar_python)
    torch_devices = (
        list(probe.get("devices", [])) if isinstance(probe.get("devices", []), list) else []
    )
    warnings = (
        list(probe.get("warnings", [])) if isinstance(probe.get("warnings", []), list) else []
    )
    errors: list[str] = []
    if probe.get("error"):
        warnings.append(str(probe["error"]))
    native_probe = probe_native_gpu_devices(tg_binary=tg_binary, env=command_env)
    if isinstance(native_probe.get("warnings", []), list):
        warnings.extend(str(warning) for warning in native_probe.get("warnings", []))
    native_devices = (
        list(native_probe.get("devices", []))
        if isinstance(native_probe.get("devices", []), list)
        else []
    )
    devices = merge_gpu_device_inventory(torch_devices, native_devices)
    if not any(device.get("operational", False) for device in devices):
        recommendation = {
            "should_add_flag": False,
            "reason": "Skipped because no operational GPU devices were detected.",
            "winning_rows": [],
        }
        gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks([])
        scale_gate_summary = build_scale_gate_summary(
            devices=devices,
            correctness_checks=[],
            gpu_auto_recommendation=recommendation,
            correctness_patterns=correctness_patterns,
        )
        return {
            "bench_dir": str(bench_dir),
            "corpus_sizes": [
                {"label": _format_size_label(size_bytes), "bytes": size_bytes}
                for size_bytes in corpus_sizes
            ],
            "devices": devices,
            "rows": [],
            "correctness_checks": [],
            "gpu_auto_recommendation": recommendation,
            "gpu_bottleneck_summary": gpu_bottleneck_summary,
            "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
            "scale_gate_summary": scale_gate_summary,
            **_gpu_proof_status_from_summary(scale_gate_summary),
            "gpu_proof_summary": build_gpu_proof_summary(scale_gate_summary),
            "warnings": warnings,
            "errors": errors,
            "benchmark_pattern": benchmark_pattern,
            "correctness_patterns": list(correctness_patterns),
            "timing_backend": "perf_counter",
            "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
            "torch_version": probe.get("torch_version"),
            "status": "SKIP",
            "skipped": True,
        }

    runtime_probes: dict[int, dict[str, object]] = {}
    runtime_pipeline_samples: list[dict[str, object]] = []
    scale_pipeline_samples: list[dict[str, object]] = []
    for device in devices:
        if not device.get("operational", False):
            continue
        device_id = int(device["device_id"])
        runtime_probe = probe_tg_gpu_runtime_backend(
            tg_binary=tg_binary,
            device_id=device_id,
            env=command_env,
            bench_dir=bench_dir,
        )
        runtime_probes[device_id] = runtime_probe
        device["tg_runtime_backend"] = runtime_probe.get("routing_backend")
        device["tg_runtime_reason"] = runtime_probe.get("routing_reason")
        device["tg_runtime_sidecar_used"] = runtime_probe.get("sidecar_used")
        if isinstance(runtime_probe.get("pipeline"), dict):
            sample = extract_gpu_pipeline_breakdown(
                runtime_probe,
                source="runtime_probe",
                source_label=f"GPU {device_id} runtime probe",
            )
            if sample:
                runtime_pipeline_samples.append(sample)
        if not _uses_native_cuda_runtime(device):
            warnings.append(
                "GPU scale benchmark requires a CUDA-enabled native tg binary; "
                f"device {device_id} routed to "
                f"{runtime_probe.get('routing_backend') or 'unknown'} "
                f"(sidecar_used={bool(runtime_probe.get('sidecar_used'))})."
            )
    rows: list[dict[str, object]] = []
    generated_corpora: dict[int, Path] = {}

    for size_bytes in corpus_sizes:
        size_label = _format_size_label(size_bytes)
        corpus_dir = bench_dir / size_label
        corpus_info = generate_gpu_scale_corpus(
            corpus_dir,
            target_bytes=size_bytes,
            shard_count=shard_count,
        )
        generated_corpora[size_bytes] = corpus_dir
        pattern_counts = corpus_info.get("pattern_counts")
        expected_matches = (
            int(pattern_counts.get(benchmark_pattern, 0)) > 0
            if isinstance(pattern_counts, dict)
            else True
        )
        allow_no_match = not expected_matches

        rg_result = benchmark_search_command(
            build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
            allow_no_match=allow_no_match,
        )
        tg_cpu_result = benchmark_search_command(
            build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
            allow_no_match=allow_no_match,
        )

        gpu_results: list[dict[str, object]] = []
        for device in devices:
            entry = {
                "device_id": device.get("device_id"),
                "name": device.get("name"),
                "vram_capacity_mb": device.get("vram_capacity_mb"),
                "capability": device.get("capability"),
                "tg_runtime_backend": device.get("tg_runtime_backend"),
                "tg_runtime_reason": device.get("tg_runtime_reason"),
                "tg_runtime_sidecar_used": device.get("tg_runtime_sidecar_used"),
            }
            if not device.get("operational", False):
                entry.update({
                    "status": "UNSUPPORTED",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": device.get("error", "device probe failed"),
                    "promotion_evidence": False,
                    "not_gpu_proof_reason": _not_gpu_proof_reason(
                        backend=device.get("tg_runtime_backend"),
                        sidecar_used=device.get("tg_runtime_sidecar_used"),
                    ),
                })
            elif not _uses_native_cuda_runtime(device):
                runtime_probe = runtime_probes.get(int(device["device_id"]), {})
                entry.update({
                    "status": "UNSUPPORTED",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": (
                        "GPU scale benchmark requires a CUDA-enabled native tg binary; "
                        f"runtime probe routed to "
                        f"{runtime_probe.get('routing_backend') or 'unknown'} "
                        f"(sidecar_used={bool(runtime_probe.get('sidecar_used'))})."
                    ),
                    "command": runtime_probe.get("command"),
                    "promotion_evidence": False,
                    "not_gpu_proof_reason": _not_gpu_proof_reason(
                        backend=runtime_probe.get("routing_backend"),
                        sidecar_used=runtime_probe.get("sidecar_used"),
                    ),
                })
            else:
                result = benchmark_search_command(
                    build_tg_gpu_search_command(
                        tg_binary,
                        benchmark_pattern,
                        corpus_dir,
                        int(device["device_id"]),
                    ),
                    env=command_env,
                    runs=runs,
                    warmup=warmup,
                    allow_no_match=allow_no_match,
                )
                result["stderr"] = _clean_selected_gpu_stderr(
                    result.get("stderr"),
                    devices=devices,
                    selected_device_id=int(device["device_id"]),
                    warnings=warnings,
                )
                entry.update(result)
                entry["promotion_evidence"] = True
                native_stats_probe = probe_tg_gpu_native_stats_pipeline(
                    tg_binary=tg_binary,
                    corpus_dir=corpus_dir,
                    pattern=benchmark_pattern,
                    device_id=int(device["device_id"]),
                    env=command_env,
                )
                entry["native_stats_probe"] = native_stats_probe
                if isinstance(native_stats_probe.get("pipeline"), dict):
                    entry["native_stats_pipeline"] = native_stats_probe["pipeline"]
                    sample = extract_gpu_pipeline_breakdown(
                        native_stats_probe,
                        source="scale_native_stats",
                        source_label=f"{size_label} GPU {device.get('device_id')} native stats",
                        size_label=size_label,
                        process_median_s=native_stats_probe.get("process_median_s"),
                    )
                    if sample:
                        scale_pipeline_samples.append(sample)
            gpu_results.append(entry)

        row = {
            "size_label": size_label,
            "size_bytes": size_bytes,
            "actual_bytes": corpus_info["actual_bytes"],
            "file_count": corpus_info["file_count"],
            "total_lines": corpus_info["total_lines"],
            "pattern_counts": corpus_info["pattern_counts"],
            "expected_match": expected_matches,
            "rg": rg_result,
            "tg_cpu": tg_cpu_result,
            "gpu": gpu_results,
        }

        rg_median = rg_result.get("median_s") if isinstance(rg_result, dict) else None
        tg_cpu_median = tg_cpu_result.get("median_s") if isinstance(tg_cpu_result, dict) else None
        for gpu_result in gpu_results:
            gpu_median = gpu_result.get("median_s")
            if (
                isinstance(gpu_median, (int, float))
                and isinstance(rg_median, (int, float))
                and rg_median > 0
            ):
                gpu_result["speedup_vs_rg_pct"] = round(
                    (rg_median - gpu_median) / rg_median * 100.0,
                    2,
                )
            else:
                gpu_result["speedup_vs_rg_pct"] = None
            if (
                isinstance(gpu_median, (int, float))
                and isinstance(tg_cpu_median, (int, float))
                and tg_cpu_median > 0
            ):
                gpu_result["speedup_vs_tg_cpu_pct"] = round(
                    (tg_cpu_median - gpu_median) / tg_cpu_median * 100.0,
                    2,
                )
            else:
                gpu_result["speedup_vs_tg_cpu_pct"] = None

        rows.append(row)

    correctness_corpus_sizes = [size for size in corpus_sizes if size >= 1 * GB]
    if not correctness_corpus_sizes:
        correctness_corpus_sizes = [
            next(
                (size for size in corpus_sizes if size >= 10 * MB),
                corpus_sizes[0],
            )
        ]
    correctness_checks: list[dict[str, object]] = []
    for correctness_corpus_size in correctness_corpus_sizes:
        correctness_corpus_dir = generated_corpora[correctness_corpus_size]
        for device in devices:
            if not device.get("operational", False):
                continue
            if not _uses_native_cuda_runtime(device):
                continue
            for pattern in correctness_patterns:
                check = run_correctness_check(
                    rg_binary=rg_binary,
                    tg_binary=tg_binary,
                    corpus_dir=correctness_corpus_dir,
                    pattern=pattern,
                    device_id=int(device["device_id"]),
                    env=command_env,
                )
                for diagnostic_key in ("stderr", "error"):
                    if diagnostic_key in check:
                        check[diagnostic_key] = _clean_selected_gpu_stderr(
                            check.get(diagnostic_key),
                            devices=devices,
                            selected_device_id=int(device["device_id"]),
                            warnings=warnings,
                        )
                size_label = _format_size_label(correctness_corpus_size)
                check["device_name"] = device.get("name")
                check["corpus_size_label"] = size_label
                if not (check.get("matches_equal") and check.get("files_equal")):
                    errors.append(
                        "Correctness mismatch for GPU "
                        f"{device.get('device_id')} pattern {pattern!r} at {size_label}."
                    )
                correctness_checks.append(check)

    gpu_auto_recommendation = analyze_gpu_auto_recommendation(
        rows,
        correctness_checks=correctness_checks,
        correctness_patterns=correctness_patterns,
    )
    pipeline_samples = scale_pipeline_samples or runtime_pipeline_samples
    gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks(pipeline_samples)

    scale_gate_summary = build_scale_gate_summary(
        devices=devices,
        correctness_checks=correctness_checks,
        gpu_auto_recommendation=gpu_auto_recommendation,
        correctness_patterns=correctness_patterns,
    )

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes}
            for size_bytes in corpus_sizes
        ],
        "devices": devices,
        "rows": rows,
        "correctness_checks": correctness_checks,
        "gpu_auto_recommendation": gpu_auto_recommendation,
        "gpu_bottleneck_summary": gpu_bottleneck_summary,
        "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
        "scale_gate_summary": scale_gate_summary,
        **_gpu_proof_status_from_summary(scale_gate_summary),
        "gpu_proof_summary": build_gpu_proof_summary(scale_gate_summary),
        "warnings": warnings,
        "errors": errors,
        "benchmark_pattern": benchmark_pattern,
        "correctness_patterns": list(correctness_patterns),
        "timing_backend": "perf_counter",
        "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
        "torch_version": probe.get("torch_version"),
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


def main() -> int:
    args = build_parser().parse_args()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tg_binary = resolve_tg_binary(args.binary)
    sidecar_python = resolve_gpu_sidecar_python(args.sidecar_python)
    bench_dir = resolve_gpu_bench_data_dir()
    rg_binary = resolve_rg_binary()

    payload: dict[str, object] = {
        "artifact": "bench_gpu_scale",
        "suite": "run_gpu_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "tg_binary": str(tg_binary),
        "rg_binary": str(rg_binary),
        "sidecar_python": str(sidecar_python) if sidecar_python is not None else None,
        "runs": args.runs,
        "warmup": args.warmup,
    }

    if not tg_binary.exists():
        recommendation = {
            "should_add_flag": False,
            "reason": "Benchmark did not run because the tg binary was missing.",
            "winning_rows": [],
        }
        gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks([])
        payload.update({
            "errors": [f"tg binary not found: {tg_binary}"],
            "warnings": [],
            "rows": [],
            "correctness_checks": [],
            "corpus_sizes": [],
            "devices": [],
            "gpu_auto_recommendation": recommendation,
            "gpu_bottleneck_summary": gpu_bottleneck_summary,
            "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
            "scale_gate_summary": build_scale_gate_summary(
                devices=[],
                correctness_checks=[],
                gpu_auto_recommendation=recommendation,
            ),
        })
        scale_gate_summary = payload.get("scale_gate_summary")
        if isinstance(scale_gate_summary, dict):
            payload.update(_gpu_proof_status_from_summary(scale_gate_summary))
            payload["gpu_proof_summary"] = build_gpu_proof_summary(scale_gate_summary)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 1

    result = run_gpu_scale_benchmarks(
        tg_binary=tg_binary,
        rg_binary=str(rg_binary),
        bench_dir=bench_dir,
        corpus_sizes=args.corpus_sizes,
        runs=args.runs,
        warmup=args.warmup,
        sidecar_python=sidecar_python,
        benchmark_pattern=DEFAULT_BENCHMARK_PATTERN,
        correctness_patterns=DEFAULT_CORRECTNESS_PATTERNS,
        shard_count=args.shards,
    )
    payload.update(result)
    scale_gate_summary = payload.get("scale_gate_summary")
    if isinstance(scale_gate_summary, dict):
        payload.update(_gpu_proof_status_from_summary(scale_gate_summary))
        payload["gpu_proof_summary"] = build_gpu_proof_summary(scale_gate_summary)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 1 if payload.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
