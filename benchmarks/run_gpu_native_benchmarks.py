from __future__ import annotations

import argparse
import json
import os
import platform
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

from run_benchmarks import resolve_rg_binary, resolve_tg_binary  # noqa: E402
from run_gpu_benchmarks import (  # noqa: E402
    DEFAULT_BENCHMARK_PATTERN,
    DEFAULT_CORRECTNESS_PATTERNS,
    DEFAULT_SHARD_COUNT,
    FAIR_RG_MULTI_PATTERN_BASELINE,
    GB,
    GPU_MANY_PATTERN_WORKLOAD_CLASS,
    GPU_RESIDENT_REPEATED_QUERY_WORKLOAD_CLASS,
    MB,
    build_gpu_readiness_next_steps,
    build_gpu_workload_taxonomy,
    extract_gpu_pipeline_breakdown,
    generate_gpu_scale_corpus,
    parse_corpus_sizes,
    summarize_gpu_pipeline_bottlenecks,
)

from tensor_grep.cli.runtime_paths import inspect_native_tg_binary  # noqa: E402

DEFAULT_CORPUS_SIZES = (10 * MB, 100 * MB, 500 * MB, 1 * GB, 5 * GB)
DEFAULT_RUNS = 3
DEFAULT_WARMUP = 0
DEFAULT_COMMAND_TIMEOUT_S = 180
DEFAULT_GPU_DEVICE_ID = 0
NATIVE_SCALE_WORKLOAD_CLASS = "single_pattern_cold_grep"
NATIVE_MANY_PATTERN_WORKLOAD_CLASS = GPU_MANY_PATTERN_WORKLOAD_CLASS
DEFAULT_TIMEOUT_SIMULATION_MS = 300
DEFAULT_TIMEOUT_DESCRIPTION = "simulation-backed via TG_TEST_CUDA_BEHAVIOR"
DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES = 1 * GB
DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES = 256 * MB
DEFAULT_ADVANCED_GRAPH_PATTERN = "ERROR cuda graph sentinel"
DEFAULT_ADVANCED_GRAPH_FILE_COUNT = 160
DEFAULT_ADVANCED_GRAPH_BATCH_BYTES = 4 * 1024
DEFAULT_ADVANCED_LONG_LINE_TARGET_BYTES = 128 * MB
DEFAULT_ADVANCED_LONG_LINE_PATTERN = "ERROR long line sentinel"
DEFAULT_ADVANCED_THROUGHPUT_PATTERN_COUNT = 4
DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES = 64 * 1024
DEFAULT_ADVANCED_THROUGHPUT_MAX_BATCH_BYTES = 16 * MB
DEFAULT_ADVANCED_OOM_BYTES = 13 * GB
DEFAULT_MULTI_GPU_DEVICE_ID = 1
MIN_GPU_THROUGHPUT_SPEEDUP_VS_RG = 10.0
MIN_MULTI_GPU_IMPROVEMENT_PCT = 15.0
GPU_TIMEOUT_OPTIMIZATIONS = [
    "cache NVRTC-compiled kernels across CLI invocations",
    "overlap host-to-device transfer with kernel execution via CUDA streams",
    "use pinned host buffers for large corpus transfers",
]


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_run_gpu_native_benchmarks.json"


def resolve_gpu_native_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_GPU_NATIVE_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "gpu_native_bench_data"


def _format_size_label(size_bytes: int) -> str:
    if size_bytes % GB == 0:
        return f"{size_bytes // GB}GB"
    if size_bytes % MB == 0:
        return f"{size_bytes // MB}MB"
    return f"{size_bytes}B"


def _command_display(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return " ".join(command)


def _build_command_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    if extra:
        env.update(extra)
    return env


def build_rg_search_command(rg_binary: str, pattern: str, corpus_dir: Path) -> list[str]:
    return [rg_binary, "--no-ignore", "-F", pattern, str(corpus_dir)]


def build_rg_json_command(rg_binary: str, pattern: str, corpus_dir: Path) -> list[str]:
    return [rg_binary, "--json", "--no-ignore", "-F", pattern, str(corpus_dir)]


def build_rg_multi_pattern_search_command(
    rg_binary: str,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[str]:
    command = [rg_binary, "--no-ignore", "-F"]
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_rg_multi_pattern_json_command(
    rg_binary: str,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[str]:
    command = [rg_binary, "--json", "--no-ignore", "-F"]
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_tg_cpu_search_command(tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--cpu",
        "--no-ignore",
        "-F",
        pattern,
        str(corpus_dir),
    ]


def build_tg_gpu_search_command(
    tg_binary: Path,
    pattern: str,
    corpus_dir: Path,
    device_id: int,
) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--gpu-device-ids",
        str(device_id),
        "--no-ignore",
        "-F",
        pattern,
        str(corpus_dir),
    ]


def build_tg_json_command(
    tg_binary: Path,
    pattern: str,
    corpus_dir: Path,
    *,
    force_cpu: bool = False,
    device_id: int | None = None,
) -> list[str]:
    command = [str(tg_binary), "search"]
    if force_cpu:
        command.append("--cpu")
    if device_id is not None:
        command.extend(["--gpu-device-ids", str(device_id)])
    command.extend(["--json", "--no-ignore", "-F", pattern, str(corpus_dir)])
    return command


def build_tg_multi_pattern_json_command(
    tg_binary: Path,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
    *,
    force_cpu: bool = False,
    device_id: int | None = None,
) -> list[str]:
    command = [str(tg_binary), "search"]
    if force_cpu:
        command.append("--cpu")
    if device_id is not None:
        command.extend(["--gpu-device-ids", str(device_id)])
    command.extend(["--json", "--no-ignore", "-F"])
    for pattern in patterns:
        command.extend(["-e", pattern])
    command.append(str(corpus_dir))
    return command


def build_tg_gpu_native_stats_command(
    tg_binary: Path,
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
    device_ids: list[int] | tuple[int, ...],
    *,
    max_batch_bytes: int | None = None,
    summary_only: bool = True,
) -> list[str]:
    command = [str(tg_binary), "__gpu-native-stats"]
    for pattern in patterns:
        command.extend(["--pattern", pattern])
    command.extend(["--path", str(corpus_dir)])
    command.extend(["--gpu-device-ids", ",".join(str(device_id) for device_id in device_ids)])
    command.append("--no-ignore")
    if max_batch_bytes is not None:
        command.extend(["--max-batch-bytes", str(max_batch_bytes)])
    if summary_only:
        command.append("--summary-only")
    return command


def build_tg_gpu_transfer_benchmark_command(
    tg_binary: Path,
    *,
    device_id: int,
    total_bytes: int,
    batch_bytes: int,
    memory_kind: str,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-transfer-bench",
        "--device-id",
        str(device_id),
        "--total-bytes",
        str(total_bytes),
        "--batch-bytes",
        str(batch_bytes),
        "--memory-kind",
        memory_kind,
    ]


def build_tg_gpu_cuda_graph_benchmark_command(
    tg_binary: Path,
    *,
    pattern: str,
    corpus_dir: Path,
    device_id: int,
    max_batch_bytes: int,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-cuda-graphs",
        "--pattern",
        pattern,
        "--path",
        str(corpus_dir),
        "--device-id",
        str(device_id),
        "--no-ignore",
        "--max-batch-bytes",
        str(max_batch_bytes),
    ]


def build_tg_gpu_oom_probe_command(
    tg_binary: Path,
    *,
    device_id: int,
    bytes_to_allocate: int,
) -> list[str]:
    return [
        str(tg_binary),
        "__gpu-oom-probe",
        "--device-id",
        str(device_id),
        "--bytes",
        str(bytes_to_allocate),
    ]


def _run_command(
    command: list[str],
    *,
    env: dict[str, str],
    capture_output: bool,
    timeout_s: int,
) -> subprocess.CompletedProcess[str] | subprocess.TimeoutExpired:
    try:
        return subprocess.run(
            command,
            cwd=ROOT_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return exc


def benchmark_search_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    corpus_bytes: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
        if isinstance(warmup_result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
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
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        started_at = time.perf_counter()
        result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
        elapsed = round(time.perf_counter() - started_at, 6)
        if isinstance(result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
            no_match_exit_accepted = True
        elif result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
                "allow_no_match": allow_no_match,
                "no_match_exit_accepted": no_match_exit_accepted,
            }
        samples.append(elapsed)
        last_stderr = (result.stderr or "").strip()

    median_s = round(statistics.median(samples), 6)
    throughput = round(corpus_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "throughput_bytes_s": throughput,
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


def _parse_json_payload(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("search output did not produce a JSON object")
    return payload


def _normalized_match_path(value: object) -> str:
    return str(value or "").replace("\\", "/")


def _normalized_match_text(value: object) -> str:
    return str(value or "").rstrip("\r\n")


def _extract_tg_match_signatures(payload: dict[str, object]) -> list[tuple[str, int, str]]:
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []
    signatures: list[tuple[str, int, str]] = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        # Native tg search JSON (SearchMatchJson in rust_core/src/main.rs) emits
        # the line number under the key `line`; the rg-passthrough serializer uses
        # `line_number`. Read `line` first, fall back to `line_number` so both the
        # native `--gpu-device-ids` path and the CPU/rg path parse correctly (#131 F2).
        line_number = match.get("line", match.get("line_number"))
        if not isinstance(line_number, int):
            line_number = 0
        signatures.append((
            _normalized_match_path(match.get("file")),
            line_number,
            _normalized_match_text(match.get("text")),
        ))
    return sorted(signatures)


def _extract_rg_json_match_signatures(stdout: str) -> list[tuple[str, int, str]]:
    signatures: list[tuple[str, int, str]] = []
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        if not isinstance(event, dict) or event.get("type") != "match":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        path = data.get("path")
        path_text = path.get("text") if isinstance(path, dict) else ""
        line_number = data.get("line_number")
        if not isinstance(line_number, int):
            line_number = 0
        lines = data.get("lines")
        line_text = lines.get("text") if isinstance(lines, dict) else ""
        signatures.append((
            _normalized_match_path(path_text),
            line_number,
            _normalized_match_text(line_text),
        ))
    return sorted(signatures)


def _signature_file_count(signatures: list[tuple[str, int, str]]) -> int:
    return len({signature[0] for signature in signatures if signature[0]})


def _signature_files(signatures: list[tuple[str, int, str]]) -> set[str]:
    return {signature[0] for signature in signatures if signature[0]}


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _timeout_stderr(timeout_s: int) -> str:
    return f"command timed out after {timeout_s}s"


def _native_gpu_route_failure(payload: dict[str, object]) -> dict[str, object] | None:
    routing_backend = str(payload.get("routing_backend") or "unknown")
    routing_reason = payload.get("routing_reason")
    sidecar_used = bool(payload.get("sidecar_used", False))
    if routing_backend == "NativeGpuBackend" and not sidecar_used:
        return None
    return {
        "status": "UNSUPPORTED",
        "routing_backend": routing_backend,
        "routing_reason": routing_reason,
        "sidecar_used": sidecar_used,
        "promotion_evidence": False,
        "not_gpu_proof_reason": (
            "Requested GPU execution did not produce NativeGpuBackend with "
            f"sidecar_used=false (routing_backend={routing_backend}, "
            f"sidecar_used={sidecar_used}); this is CPU/sidecar compatibility "
            "output, not GPU acceleration proof."
        ),
        "error": (
            "GPU route did not use NativeGpuBackend "
            f"(routing_backend={routing_backend}, sidecar_used={sidecar_used}); "
            "sidecar-routed GPU rows are not native CUDA scale proof."
        ),
    }


def probe_native_gpu_runtime_backend(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    command = build_tg_json_command(tg_binary, pattern, corpus_dir, device_id=device_id)
    result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
    command_display = _command_display(command)
    if isinstance(result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": _timeout_stderr(timeout_s),
            "command": command_display,
        }
    if result.returncode != 0:
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": (result.stderr or "").strip(),
            "command": command_display,
        }
    try:
        payload = _parse_json_payload(result.stdout or "{}")
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "status": "FAIL",
            "routing_backend": "unknown",
            "routing_reason": None,
            "sidecar_used": None,
            "error": f"failed to parse GPU runtime JSON: {exc}",
            "command": command_display,
        }
    route_failure = _native_gpu_route_failure(payload)
    if route_failure is not None:
        route_failure["command"] = command_display
        if isinstance(payload.get("pipeline"), dict):
            route_failure["pipeline"] = payload["pipeline"]
        return route_failure
    probe: dict[str, object] = {
        "status": "PASS",
        "routing_backend": str(payload.get("routing_backend") or "NativeGpuBackend"),
        "routing_reason": payload.get("routing_reason"),
        "sidecar_used": bool(payload.get("sidecar_used", False)),
        "command": command_display,
    }
    if isinstance(payload.get("pipeline"), dict):
        probe["pipeline"] = payload["pipeline"]
    return probe


def _run_json_command(
    command: list[str],
    *,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
    if isinstance(result, subprocess.TimeoutExpired):
        raise RuntimeError(f"command timed out after {timeout_s}s: {_command_display(command)}")
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or f"command failed: {_command_display(command)}"
        )
    return _parse_json_payload(result.stdout or "{}")


def _lookup_nested_float(payload: dict[str, object], *path: str) -> float | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (float, int)):
        return float(current)
    return None


def benchmark_json_metric_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    corpus_bytes: int,
    metric_path: tuple[str, ...],
    metric_scale: float = 1.0,
) -> dict[str, object]:
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
        if isinstance(warmup_result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }
        if warmup_result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (warmup_result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

    samples: list[float] = []
    process_samples: list[float] = []
    last_stderr = ""
    last_payload: dict[str, object] = {}
    for _ in range(runs):
        started_at = time.perf_counter()
        result = _run_command(command, env=env, capture_output=True, timeout_s=timeout_s)
        process_elapsed = round(time.perf_counter() - started_at, 6)
        if isinstance(result, subprocess.TimeoutExpired):
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": f"command timed out after {timeout_s}s",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }
        if result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

        payload = _parse_json_payload(result.stdout or "{}")
        metric_value = _lookup_nested_float(payload, *metric_path)
        if metric_value is None:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "process_samples_s": process_samples,
                "stderr": f"missing metric at {'.'.join(metric_path)}",
                "command": _command_display(command),
                "throughput_bytes_s": None,
            }

        samples.append(round(metric_value * metric_scale, 6))
        process_samples.append(process_elapsed)
        last_payload = payload
        last_stderr = (result.stderr or "").strip()

    median_s = round(statistics.median(samples), 6)
    process_median_s = round(statistics.median(process_samples), 6) if process_samples else None
    throughput = round(corpus_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "process_median_s": process_median_s,
        "process_samples_s": process_samples,
        "stderr": last_stderr,
        "command": _command_display(command),
        "throughput_bytes_s": throughput,
        "payload": last_payload,
    }


def benchmark_command_group(
    commands: list[list[str]],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
    timeout_s: int,
    workload_bytes: int,
    allow_no_match: bool = False,
) -> dict[str, object]:
    no_match_exit_accepted = False
    for _ in range(warmup):
        for command in commands:
            warmup_result = _run_command(
                command, env=env, capture_output=False, timeout_s=timeout_s
            )
            if isinstance(warmup_result, subprocess.TimeoutExpired):
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": f"command timed out after {timeout_s}s",
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
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
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        started_at = time.perf_counter()
        for command in commands:
            result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
            if isinstance(result, subprocess.TimeoutExpired):
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": samples,
                    "stderr": f"command timed out after {timeout_s}s",
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
            if result.returncode == 1 and allow_no_match and not (result.stderr or "").strip():
                no_match_exit_accepted = True
            elif result.returncode != 0:
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": samples,
                    "stderr": (result.stderr or "").strip(),
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                    "allow_no_match": allow_no_match,
                    "no_match_exit_accepted": no_match_exit_accepted,
                }
            last_stderr = (result.stderr or "").strip()
        elapsed = round(time.perf_counter() - started_at, 6)
        samples.append(elapsed)

    median_s = round(statistics.median(samples), 6)
    throughput = round(workload_bytes / median_s, 2) if median_s > 0 else None
    return {
        "status": "PASS",
        "median_s": median_s,
        "samples_s": samples,
        "stderr": last_stderr,
        "command_group": [_command_display(candidate) for candidate in commands],
        "throughput_bytes_s": throughput,
        "allow_no_match": allow_no_match,
        "no_match_exit_accepted": no_match_exit_accepted,
    }


def _infer_total_files(payload: dict[str, object]) -> int:
    total_files = payload.get("total_files")
    if isinstance(total_files, int) and total_files > 0:
        return total_files
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return 0
    files = {
        match.get("file")
        for match in matches
        if isinstance(match, dict) and isinstance(match.get("file"), str)
    }
    return len(files)


def cpu_oracle_search(
    patterns: list[str] | tuple[str, ...],
    corpus_dir: Path,
) -> list[tuple[str, int, str]]:
    """Independent, obviously-correct CPU oracle for fixed-string multi-pattern search.

    Walks every file under corpus_dir using plain Python string iteration and
    the built-in ``in`` operator (str.find semantics).  This function is the
    ground-truth reference that BOTH the existing brute-force GPU kernel and
    the future PFAC kernel must agree with.  It is intentionally written with
    no dependency on rg, the GPU kernel, or any search library so it can serve
    as an independent third party in correctness comparisons.

    Returns sorted (normalized_path, line_number, normalized_line_text) tuples
    using the same normalization helpers used by _extract_tg_match_signatures
    and _extract_rg_json_match_signatures.  Each line is reported at most once,
    regardless of how many of the supplied patterns it matches — matching the
    semantics of ``rg -F -e p1 -e p2 …``.

    Line numbers are 1-indexed, consistent with rg --json output.
    """
    if not patterns:
        return []
    signatures: list[tuple[str, int, str]] = []
    for file_path in sorted(corpus_dir.rglob("*")):
        if not file_path.is_file():
            continue
        # Skip dot-prefixed files and files inside dot-prefixed directories,
        # matching rg's default behaviour of ignoring hidden paths.
        if any(part.startswith(".") for part in file_path.parts):
            continue
        # Skip binary files: rg skips files whose content contains a NUL byte.
        try:
            probe = file_path.read_bytes()[:8192]
        except OSError:
            continue
        if b"\x00" in probe:
            continue
        # Decode as latin-1 (never raises; each byte maps 1-to-1 to a Unicode code
        # point).  errors="replace" diverges from rg's match text for invalid UTF-8
        # sequences because it rewrites them to U+FFFD, whereas rg surfaces the raw
        # bytes; latin-1 preserves the raw byte values faithfully.
        try:
            text = file_path.read_text(encoding="latin-1")
        except OSError:
            continue
        path_str = _normalized_match_path(str(file_path))
        # Split on \n only, matching rg's line-splitting behaviour.
        # str.splitlines() also splits on \r, \v, \f, \x1c-\x1e, \x85,
        # U+2028, U+2029 — none of which rg treats as line boundaries.
        # Drop the trailing empty element produced by a final \n.
        raw_lines = text.split("\n")
        if raw_lines and raw_lines[-1] == "":
            raw_lines = raw_lines[:-1]
        for line_number, raw_line in enumerate(raw_lines, start=1):
            line_text = _normalized_match_text(raw_line)
            for pattern in patterns:
                if pattern in raw_line:
                    signatures.append((path_str, line_number, line_text))
                    break  # each line reported at most once (rg -F -e … -e … semantics)
    return sorted(signatures)


def run_correctness_check(
    *,
    tg_binary: Path,
    rg_binary: str = "rg",
    corpus_dir: Path,
    pattern: str,
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    cpu_result = _run_command(
        build_tg_json_command(tg_binary, pattern, corpus_dir, force_cpu=True),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    gpu_result = _run_command(
        build_tg_json_command(tg_binary, pattern, corpus_dir, device_id=device_id),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )

    if isinstance(cpu_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"CPU correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
        }
    if isinstance(gpu_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"GPU correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
        }
    if cpu_result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (cpu_result.stderr or "").strip(),
            "matches_equal": False,
        }
    if gpu_result.returncode != 0:
        return {
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
        }

    cpu_payload = _parse_json_payload(cpu_result.stdout or "{}")
    gpu_payload = _parse_json_payload(gpu_result.stdout or "{}")
    route_failure = _native_gpu_route_failure(gpu_payload)
    if route_failure is not None:
        return {
            **route_failure,
            "matches_equal": False,
            "files_equal": False,
        }

    rg_result = _run_command(
        build_rg_json_command(rg_binary, pattern, corpus_dir),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    if isinstance(rg_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"rg correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if rg_result.returncode not in {0, 1}:
        return {
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }

    cpu_total_matches = int(cpu_payload.get("total_matches", 0))
    gpu_total_matches = int(gpu_payload.get("total_matches", 0))
    cpu_total_files = _infer_total_files(cpu_payload)
    gpu_total_files = _infer_total_files(gpu_payload)
    cpu_signatures = _extract_tg_match_signatures(cpu_payload)
    gpu_signatures = _extract_tg_match_signatures(gpu_payload)
    rg_signatures = _extract_rg_json_match_signatures(rg_result.stdout or "")
    cpu_gpu_matches_equal = cpu_signatures == gpu_signatures
    cpu_gpu_files_equal = cpu_total_files == gpu_total_files
    rg_matches_equal = rg_signatures == gpu_signatures
    rg_files_equal = _signature_file_count(rg_signatures) == gpu_total_files
    # Independent CPU oracle: plain Python fixed-string search, no dependency on rg
    # or the GPU kernel.  oracle_status is PASS iff the oracle agrees with rg.
    try:
        oracle_signatures = cpu_oracle_search([pattern], corpus_dir)
        oracle_matches_equal = oracle_signatures == rg_signatures
        oracle_status: str = "PASS" if oracle_matches_equal else "FAIL"
    except Exception:
        oracle_signatures = []
        oracle_matches_equal = False
        oracle_status = "ERROR"
    return {
        "status": (
            "PASS"
            if cpu_total_matches == gpu_total_matches
            and cpu_gpu_matches_equal
            and cpu_gpu_files_equal
            and rg_matches_equal
            and rg_files_equal
            and oracle_status == "PASS"
            else "FAIL"
        ),
        "cpu_total_matches": cpu_total_matches,
        "gpu_total_matches": gpu_total_matches,
        "rg_total_matches": len(rg_signatures),
        "cpu_total_files": cpu_total_files,
        "gpu_total_files": gpu_total_files,
        "rg_total_files": _signature_file_count(rg_signatures),
        "matches_equal": cpu_total_matches == gpu_total_matches and cpu_gpu_matches_equal,
        "files_equal": cpu_gpu_files_equal,
        "rg_matches_equal": rg_matches_equal,
        "rg_files_equal": rg_files_equal,
        "rg_match_identity_equal": rg_matches_equal,
        "oracle_status": oracle_status,
        "oracle_total_matches": len(oracle_signatures),
        "oracle_total_files": _signature_file_count(oracle_signatures),
        "oracle_matches_equal": oracle_matches_equal,
    }


def run_many_pattern_correctness_check(
    *,
    tg_binary: Path,
    rg_binary: str,
    corpus_dir: Path,
    patterns: list[str] | tuple[str, ...],
    device_id: int,
    env: dict[str, str],
    timeout_s: int,
) -> dict[str, object]:
    cpu_result = _run_command(
        build_tg_multi_pattern_json_command(
            tg_binary,
            patterns,
            corpus_dir,
            force_cpu=True,
        ),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    gpu_result = _run_command(
        build_tg_multi_pattern_json_command(
            tg_binary,
            patterns,
            corpus_dir,
            device_id=device_id,
        ),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    rg_result = _run_command(
        build_rg_multi_pattern_json_command(rg_binary, patterns, corpus_dir),
        env=env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    base_payload: dict[str, object] = {
        "workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
        "patterns": list(patterns),
        "fair_rg_baseline": "single_invocation_rg_fixed_multi_pattern",
    }
    if isinstance(cpu_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"CPU many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if isinstance(gpu_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"GPU many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if isinstance(rg_result, subprocess.TimeoutExpired):
        return {
            **base_payload,
            "status": "FAIL",
            "error": f"rg many-pattern correctness {_timeout_stderr(timeout_s)}",
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if cpu_result.returncode != 0:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (cpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if gpu_result.returncode != 0:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (gpu_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    if rg_result.returncode not in {0, 1}:
        return {
            **base_payload,
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }

    cpu_payload = _parse_json_payload(cpu_result.stdout or "{}")
    gpu_payload = _parse_json_payload(gpu_result.stdout or "{}")
    route_failure = _native_gpu_route_failure(gpu_payload)
    if route_failure is not None:
        return {
            **base_payload,
            **route_failure,
            "matches_equal": False,
            "files_equal": False,
            "rg_matches_equal": False,
            "rg_files_equal": False,
            "rg_match_identity_equal": False,
        }
    cpu_signatures = _extract_tg_match_signatures(cpu_payload)
    gpu_signatures = _extract_tg_match_signatures(gpu_payload)
    rg_signatures = _extract_rg_json_match_signatures(rg_result.stdout or "")
    cpu_gpu_matches_equal = cpu_signatures == gpu_signatures
    cpu_gpu_files_equal = _signature_files(cpu_signatures) == _signature_files(gpu_signatures)
    rg_matches_equal = rg_signatures == gpu_signatures
    rg_files_equal = _signature_files(rg_signatures) == _signature_files(gpu_signatures)
    # Independent CPU oracle: plain Python fixed-string search, no dependency on rg
    # or the GPU kernel.  oracle_status is PASS iff the oracle agrees with rg.
    try:
        oracle_signatures = cpu_oracle_search(list(patterns), corpus_dir)
        oracle_matches_equal = oracle_signatures == rg_signatures
        oracle_status: str = "PASS" if oracle_matches_equal else "FAIL"
    except Exception:
        oracle_signatures = []
        oracle_matches_equal = False
        oracle_status = "ERROR"
    return {
        **base_payload,
        "status": (
            "PASS"
            if cpu_gpu_matches_equal
            and cpu_gpu_files_equal
            and rg_matches_equal
            and rg_files_equal
            and oracle_status == "PASS"
            else "FAIL"
        ),
        "cpu_total_matches": len(cpu_signatures),
        "gpu_total_matches": len(gpu_signatures),
        "rg_total_matches": len(rg_signatures),
        "cpu_total_files": _signature_file_count(cpu_signatures),
        "gpu_total_files": _signature_file_count(gpu_signatures),
        "rg_total_files": _signature_file_count(rg_signatures),
        "matches_equal": cpu_gpu_matches_equal,
        "files_equal": cpu_gpu_files_equal,
        "rg_matches_equal": rg_matches_equal,
        "rg_files_equal": rg_files_equal,
        "rg_match_identity_equal": rg_matches_equal,
        "oracle_status": oracle_status,
        "oracle_total_matches": len(oracle_signatures),
        "oracle_total_files": _signature_file_count(oracle_signatures),
        "oracle_matches_equal": oracle_matches_equal,
    }


def create_error_fixture(error_dir: Path) -> Path:
    error_dir.mkdir(parents=True, exist_ok=True)
    (error_dir / "good.log").write_text(
        "INFO boot\nERROR gpu benchmark sentinel\n",
        encoding="utf-8",
    )
    (error_dir / "empty.log").write_text("", encoding="utf-8")
    (error_dir / "binary.bin").write_bytes(b"\x00gpu benchmark sentinel\x00")
    (error_dir / "invalid_utf8.log").write_bytes(b"\xff\xfeERROR gpu benchmark sentinel\n")
    return error_dir


def create_runtime_probe_fixture(probe_dir: Path) -> Path:
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "probe.log").write_text(
        "INFO boot\nERROR gpu benchmark sentinel\n",
        encoding="utf-8",
    )
    return probe_dir


def run_gpu_error_tests(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    device_id: int,
    timeout_s: int,
    timeout_simulation_ms: int,
) -> dict[str, object]:
    base_env = _build_command_env()
    pattern = DEFAULT_BENCHMARK_PATTERN

    invalid_device = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, 99),
        env=base_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    invalid_device_status = "FAIL"
    invalid_device_stderr = _timeout_stderr(timeout_s)
    invalid_device_code = None
    if not isinstance(invalid_device, subprocess.TimeoutExpired):
        invalid_device_stderr = (invalid_device.stderr or "").strip()
        invalid_device_code = invalid_device.returncode
        invalid_device_status = (
            "PASS"
            if invalid_device.returncode == 2
            and "99" in invalid_device_stderr
            and "available CUDA devices" in invalid_device_stderr
            else "FAIL"
        )

    nvrtc_env = _build_command_env({
        "TG_TEST_CUDA_BEHAVIOR": "nvrtc-failure:simulated NVRTC compile error"
    })
    nvrtc_failure = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=nvrtc_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    nvrtc_status = "FAIL"
    nvrtc_stderr = _timeout_stderr(timeout_s)
    nvrtc_code = None
    if not isinstance(nvrtc_failure, subprocess.TimeoutExpired):
        nvrtc_stderr = (nvrtc_failure.stderr or "").strip()
        nvrtc_code = nvrtc_failure.returncode
        nvrtc_status = (
            "PASS"
            if nvrtc_failure.returncode == 2
            and "CUDA kernel compilation failed" in nvrtc_stderr
            and "simulated NVRTC compile error" in nvrtc_stderr
            else "FAIL"
        )

    timeout_env = _build_command_env({
        "TG_TEST_CUDA_BEHAVIOR": f"timeout:{timeout_simulation_ms}ms"
    })
    timeout_result = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=timeout_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    timeout_status = "FAIL"
    timeout_stderr = _timeout_stderr(timeout_s)
    timeout_code = None
    if not isinstance(timeout_result, subprocess.TimeoutExpired):
        timeout_stderr = (timeout_result.stderr or "").strip()
        timeout_code = timeout_result.returncode
        timeout_status = (
            "PASS"
            if timeout_result.returncode == 2 and "timed out" in timeout_stderr.lower()
            else "FAIL"
        )

    malformed_dir = create_error_fixture(corpus_dir / "error_cases")
    malformed_gpu = _run_command(
        build_tg_json_command(tg_binary, pattern, malformed_dir, device_id=device_id),
        env=base_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    malformed_payload: dict[str, object] = {
        "status": "FAIL",
        "simulated": False,
    }
    if not isinstance(malformed_gpu, subprocess.TimeoutExpired):
        if malformed_gpu.returncode == 0:
            gpu_payload = _parse_json_payload(malformed_gpu.stdout or "{}")
            malformed_status = "PASS"
            malformed_payload = {
                "status": malformed_status,
                "exit_code": malformed_gpu.returncode,
                "simulated": False,
                "gpu_total_matches": int(gpu_payload.get("total_matches", 0)),
                "gpu_total_files": int(gpu_payload.get("total_files", 0)),
            }
        else:
            malformed_payload = {
                "status": "FAIL",
                "exit_code": malformed_gpu.returncode,
                "simulated": False,
                "stderr": (malformed_gpu.stderr or "").strip(),
            }
    else:
        malformed_payload = {
            "status": "FAIL",
            "exit_code": None,
            "simulated": False,
            "stderr": _timeout_stderr(timeout_s),
        }

    return {
        "invalid_device": {
            "status": invalid_device_status,
            "exit_code": invalid_device_code,
            "stderr": invalid_device_stderr,
            "simulated": False,
        },
        "nvrtc_failure": {
            "status": nvrtc_status,
            "exit_code": nvrtc_code,
            "stderr": nvrtc_stderr,
            "simulated": True,
        },
        "timeout": {
            "status": timeout_status,
            "exit_code": timeout_code,
            "stderr": timeout_stderr,
            "simulated": True,
            "timeout_ms": timeout_simulation_ms,
        },
        "malformed_inputs": malformed_payload,
    }


def build_unsupported_native_gpu_error_tests(
    runtime_probe: dict[str, object],
    *,
    timeout_simulation_ms: int,
) -> dict[str, object]:
    diagnostic = str(runtime_probe.get("error") or "native GPU runtime route unsupported")
    base_payload = {
        "status": "UNSUPPORTED",
        "exit_code": None,
        "stderr": diagnostic,
        "routing_backend": runtime_probe.get("routing_backend"),
        "routing_reason": runtime_probe.get("routing_reason"),
        "sidecar_used": runtime_probe.get("sidecar_used"),
    }
    return {
        "invalid_device": {
            **base_payload,
            "simulated": False,
        },
        "nvrtc_failure": {
            **base_payload,
            "simulated": True,
        },
        "timeout": {
            **base_payload,
            "simulated": True,
            "timeout_ms": timeout_simulation_ms,
        },
        "malformed_inputs": {
            **base_payload,
            "simulated": False,
        },
    }


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


def run_gpu_native_benchmarks(
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
    env = _build_command_env()
    rows: list[dict[str, object]] = []
    correctness_checks: list[dict[str, object]] = []
    warnings: list[str] = [f"Timeout validation is {DEFAULT_TIMEOUT_DESCRIPTION}."]
    errors: list[str] = []
    runtime_probe_dir = create_runtime_probe_fixture(bench_dir / "runtime_probe")
    runtime_probe = probe_native_gpu_runtime_backend(
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
        size_label = _format_size_label(size_bytes)
        corpus_dir = bench_dir / size_label
        corpus_info = generate_gpu_scale_corpus(
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

        rg_result = benchmark_search_command(
            build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
            allow_no_match=allow_no_match,
        )
        tg_cpu_result = benchmark_search_command(
            build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
            allow_no_match=allow_no_match,
        )
        if runtime_probe.get("status") == "PASS":
            tg_gpu_result = benchmark_search_command(
                build_tg_gpu_search_command(tg_binary, benchmark_pattern, corpus_dir, device_id),
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
            native_stats_result = benchmark_json_metric_command(
                build_tg_gpu_native_stats_command(
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
            correctness = run_correctness_check(
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
        error_tests = run_gpu_error_tests(
            tg_binary=tg_binary,
            corpus_dir=bench_dir,
            device_id=device_id,
            timeout_s=command_timeout_s,
            timeout_simulation_ms=timeout_simulation_ms,
        )
    else:
        diagnostic = str(runtime_probe.get("error") or "native GPU runtime route unsupported")
        warnings.append(f"GPU native error diagnostics unsupported before timing: {diagnostic}")
        error_tests = build_unsupported_native_gpu_error_tests(
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
        advanced_payload, advanced_warnings, advanced_errors = run_advanced_gpu_native_benchmarks(
            tg_binary=tg_binary,
            rg_binary=rg_binary,
            bench_dir=bench_dir,
            rows=rows,
            runs=runs,
            warmup=warmup,
            device_id=device_id,
            command_timeout_s=command_timeout_s,
        )
        warnings.extend(advanced_warnings)
        errors.extend(advanced_errors)

    crossover = analyze_crossover(rows)
    throughput_rows = advanced_payload.get("throughput_rows") if advanced else None
    if not isinstance(throughput_rows, list):
        throughput_rows = rows
    throughput_target = analyze_throughput_target(throughput_rows)
    if not throughput_target.get("met"):
        errors.append(str(throughput_target.get("summary", "GPU throughput target was not met.")))
    gpu_pipeline_samples = collect_gpu_native_pipeline_samples(rows, advanced_payload)
    gpu_bottleneck_summary = summarize_gpu_pipeline_bottlenecks(gpu_pipeline_samples)

    scale_gate_summary = build_native_scale_gate_summary(
        rows,
        correctness_checks=correctness_checks,
    )

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes}
            for size_bytes in corpus_sizes
        ],
        "rows": rows,
        "correctness_checks": correctness_checks,
        "error_tests": error_tests,
        "crossover": crossover,
        "throughput_target": throughput_target,
        "scale_gate_summary": scale_gate_summary,
        **_gpu_proof_status_from_native_summary(scale_gate_summary),
        "gpu_bottleneck_summary": gpu_bottleneck_summary,
        "gpu_readiness_next_steps": build_gpu_readiness_next_steps(gpu_bottleneck_summary),
        "advanced": advanced_payload,
        "warnings": warnings,
        "errors": errors,
        "benchmark_pattern": benchmark_pattern,
        "gpu_device_id": device_id,
        "command_timeout_s": command_timeout_s,
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


def run_advanced_gpu_native_benchmarks(
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
    env = _build_command_env()
    warnings: list[str] = []
    errors: list[str] = []
    advanced: dict[str, object] = {"enabled": True}

    one_gib_row = _get_row_for_size(rows, "1GB")
    one_gib_corpus = bench_dir / "1GB"
    one_gib_actual_bytes = int(one_gib_row.get("actual_bytes", 0))
    multi_gpu_device_ids = [device_id, DEFAULT_MULTI_GPU_DEVICE_ID]

    throughput_patterns = [
        f"ERROR advanced throughput sentinel {index:02d}"
        for index in range(DEFAULT_ADVANCED_THROUGHPUT_PATTERN_COUNT)
    ]
    throughput_rows = []
    for size_bytes in (100 * MB, 500 * MB, 1 * GB):
        size_label = _format_size_label(size_bytes)
        throughput_info = create_advanced_throughput_corpus(
            bench_dir / f"advanced_throughput_{size_label}",
            target_bytes=size_bytes,
            patterns=throughput_patterns,
            shard_count=DEFAULT_SHARD_COUNT,
            line_bytes=DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
        )
        throughput_corpus = Path(throughput_info["corpus_dir"])
        actual_bytes = int(throughput_info["actual_bytes"])
        rg_group = benchmark_command_group(
            [
                build_rg_search_command(rg_binary, pattern, throughput_corpus)
                for pattern in throughput_patterns
            ],
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            workload_bytes=actual_bytes * len(throughput_patterns),
        )
        gpu_group = benchmark_json_metric_command(
            build_tg_gpu_native_stats_command(
                tg_binary,
                throughput_patterns,
                throughput_corpus,
                multi_gpu_device_ids,
                max_batch_bytes=DEFAULT_ADVANCED_THROUGHPUT_MAX_BATCH_BYTES,
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
        "line_bytes": DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
        "device_ids": multi_gpu_device_ids,
        "mode": "multi-pattern sparse-match long-line native GPU summary benchmark",
    }

    stream_stats = _run_json_command(
        build_tg_gpu_native_stats_command(
            tg_binary, [DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]
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

    pinned_transfer = _run_json_command(
        build_tg_gpu_transfer_benchmark_command(
            tg_binary,
            device_id=device_id,
            total_bytes=DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES,
            batch_bytes=DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES,
            memory_kind="pinned",
        ),
        env=env,
        timeout_s=command_timeout_s,
    )
    pageable_transfer = _run_json_command(
        build_tg_gpu_transfer_benchmark_command(
            tg_binary,
            device_id=device_id,
            total_bytes=DEFAULT_ADVANCED_TRANSFER_TOTAL_BYTES,
            batch_bytes=DEFAULT_ADVANCED_TRANSFER_BATCH_BYTES,
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

    multi_patterns = list(DEFAULT_CORRECTNESS_PATTERNS)
    multi_pattern_gpu_benchmark = benchmark_json_metric_command(
        build_tg_gpu_native_stats_command(tg_binary, multi_patterns, one_gib_corpus, [device_id]),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes * len(multi_patterns),
        metric_path=("pipeline", "wall_time_ms"),
        metric_scale=0.001,
    )
    multi_pattern_cpu_benchmark = benchmark_command_group(
        [
            build_tg_cpu_search_command(tg_binary, pattern, one_gib_corpus)
            for pattern in multi_patterns
        ],
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        workload_bytes=one_gib_actual_bytes * len(multi_patterns),
    )
    multi_pattern_rg_benchmark = benchmark_search_command(
        build_rg_multi_pattern_search_command(rg_binary, multi_patterns, one_gib_corpus),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes,
    )
    multi_pattern_correctness = run_many_pattern_correctness_check(
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
        "workload_class": NATIVE_MANY_PATTERN_WORKLOAD_CLASS,
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
    multi_pattern_payload["proof_gate"] = build_many_pattern_proof_gate(
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

    single_gpu_benchmark = benchmark_json_metric_command(
        build_tg_gpu_native_stats_command(
            tg_binary, [DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=one_gib_actual_bytes,
        metric_path=("pipeline", "wall_time_ms"),
        metric_scale=0.001,
    )
    multi_gpu_benchmark = benchmark_json_metric_command(
        build_tg_gpu_native_stats_command(
            tg_binary,
            [DEFAULT_BENCHMARK_PATTERN],
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
        and multi_gpu_improvement_pct >= MIN_MULTI_GPU_IMPROVEMENT_PCT
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
            f"Multi-GPU benchmark did not achieve the required {MIN_MULTI_GPU_IMPROVEMENT_PCT:.0f}% improvement."
        )

    long_line_info = create_long_line_corpus(
        bench_dir / "advanced_long_lines",
        target_bytes=DEFAULT_ADVANCED_LONG_LINE_TARGET_BYTES,
        pattern=DEFAULT_ADVANCED_LONG_LINE_PATTERN,
    )
    long_line_corpus = Path(long_line_info["corpus_dir"])
    long_line_actual_bytes = int(long_line_info["actual_bytes"])
    long_line_gpu_benchmark = benchmark_search_command(
        build_tg_gpu_search_command(
            tg_binary, DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus, device_id
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_cpu_benchmark = benchmark_search_command(
        build_tg_cpu_search_command(
            tg_binary, DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus
        ),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_stats = _run_json_command(
        build_tg_gpu_native_stats_command(
            tg_binary, [DEFAULT_ADVANCED_LONG_LINE_PATTERN], long_line_corpus, [device_id]
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

    cuda_graph_info = create_cuda_graph_corpus(
        bench_dir / "advanced_cuda_graphs",
        file_count=DEFAULT_ADVANCED_GRAPH_FILE_COUNT,
        pattern=DEFAULT_ADVANCED_GRAPH_PATTERN,
    )
    cuda_graph_corpus = Path(cuda_graph_info["corpus_dir"])
    cuda_graph_benchmark = _run_json_command(
        build_tg_gpu_cuda_graph_benchmark_command(
            tg_binary,
            pattern=DEFAULT_ADVANCED_GRAPH_PATTERN,
            corpus_dir=cuda_graph_corpus,
            device_id=device_id,
            max_batch_bytes=DEFAULT_ADVANCED_GRAPH_BATCH_BYTES,
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

    oom_result = _run_command(
        build_tg_gpu_oom_probe_command(
            tg_binary,
            device_id=device_id,
            bytes_to_allocate=DEFAULT_ADVANCED_OOM_BYTES,
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
        "requested_bytes": DEFAULT_ADVANCED_OOM_BYTES,
        "exit_code": oom_exit_code,
        "stderr": oom_stderr,
        "simulated": False,
    }
    if oom_status != "PASS":
        errors.append("GPU OOM validation did not return a clear out-of-memory error message.")

    return advanced, warnings, errors


def main() -> int:
    args = build_parser().parse_args()
    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tg_binary = resolve_tg_binary(args.binary)
    rg_binary = resolve_rg_binary()
    bench_dir = resolve_gpu_native_bench_data_dir()
    tg_binary_metadata = inspect_native_tg_binary(tg_binary)

    payload: dict[str, object] = {
        "artifact": "bench_gpu_native_scale",
        "suite": "run_gpu_native_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "tg_binary": str(tg_binary),
        "tg_binary_metadata": tg_binary_metadata,
        "rg_binary": str(rg_binary),
        "runs": args.runs,
        "warmup": args.warmup,
        "gpu_device_id": args.device_id,
        "command_timeout_s": args.command_timeout_s,
    }

    if not tg_binary.exists():
        payload.update({
            "errors": [f"tg binary not found: {tg_binary}"],
            "warnings": [],
            "rows": [],
            "correctness_checks": [],
            "error_tests": {},
            "corpus_sizes": [],
            "throughput_target": {
                "met": False,
                "winning_rows": [],
                "best_attempt": None,
                "summary": "Benchmark did not run because the tg binary was missing.",
            },
            "scale_gate_summary": build_native_scale_gate_summary(
                [],
                correctness_checks=[],
            ),
            "advanced": {"enabled": args.advanced},
            "crossover": {
                "exists": False,
                "first_gpu_faster_than_rg": None,
                "summary": "Benchmark did not run because the tg binary was missing.",
                "recommended_optimizations": GPU_TIMEOUT_OPTIMIZATIONS,
            },
        })
        public_gate = build_public_managed_gpu_proof_gate(
            tg_binary_metadata=tg_binary_metadata,
            scale_gate_summary=payload["scale_gate_summary"]
            if isinstance(payload["scale_gate_summary"], dict)
            else {},
            advanced_payload=payload.get("advanced")
            if isinstance(payload.get("advanced"), dict)
            else None,
            requested=args.public_managed_proof,
        )
        scale_gate_summary = payload["scale_gate_summary"]
        proof_status = _gpu_proof_status_from_native_summary(
            scale_gate_summary if isinstance(scale_gate_summary, dict) else {}
        )
        payload.update(proof_status)
        payload["public_managed_gpu_proof_gate"] = public_gate
        payload["public_managed_promotion_ready"] = public_gate["public_managed_promotion_ready"]
        payload["public_gpu_proof"] = public_gate["public_gpu_proof"]
        payload["gpu_proof_summary"] = build_gpu_proof_summary(
            scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
            public_managed_gpu_proof_gate=public_gate,
        )
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 1

    result = run_gpu_native_benchmarks(
        tg_binary=tg_binary,
        rg_binary=str(rg_binary),
        bench_dir=bench_dir,
        corpus_sizes=args.corpus_sizes,
        runs=args.runs,
        warmup=args.warmup,
        device_id=args.device_id,
        command_timeout_s=args.command_timeout_s,
        shard_count=args.shards,
        benchmark_pattern=DEFAULT_BENCHMARK_PATTERN,
        timeout_simulation_ms=args.timeout_simulation_ms,
        advanced=args.advanced,
    )
    payload.update(result)
    scale_gate_summary = payload.get("scale_gate_summary")
    payload.update(
        _gpu_proof_status_from_native_summary(
            scale_gate_summary if isinstance(scale_gate_summary, dict) else {}
        )
    )
    public_gate = build_public_managed_gpu_proof_gate(
        tg_binary_metadata=tg_binary_metadata,
        scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
        advanced_payload=payload.get("advanced")
        if isinstance(payload.get("advanced"), dict)
        else None,
        requested=args.public_managed_proof,
    )
    payload["public_managed_gpu_proof_gate"] = public_gate
    payload["public_managed_promotion_ready"] = public_gate["public_managed_promotion_ready"]
    payload["public_gpu_proof"] = public_gate["public_gpu_proof"]
    payload["gpu_proof_summary"] = build_gpu_proof_summary(
        scale_gate_summary=scale_gate_summary if isinstance(scale_gate_summary, dict) else {},
        public_managed_gpu_proof_gate=public_gate,
    )
    if args.public_managed_proof and public_gate["status"] != "PASS":
        errors = payload.setdefault("errors", [])
        if isinstance(errors, list):
            errors.append(
                "public managed GPU proof gate failed: "
                + ", ".join(str(blocker) for blocker in public_gate["blockers"])
            )
    payload["passed"] = not payload.get("errors")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
