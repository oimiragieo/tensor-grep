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
    GB,
    MB,
    generate_gpu_scale_corpus,
    parse_corpus_sizes,
)

DEFAULT_CORPUS_SIZES = (10 * MB, 100 * MB, 500 * MB, 1 * GB)
DEFAULT_RUNS = 3
DEFAULT_WARMUP = 0
DEFAULT_COMMAND_TIMEOUT_S = 180
DEFAULT_GPU_DEVICE_ID = 0
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
) -> dict[str, object]:
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
            }
        if result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": samples,
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
                "throughput_bytes_s": None,
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
    }


def _parse_json_payload(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise ValueError("search output did not produce a JSON object")
    return payload


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
        raise RuntimeError((result.stderr or "").strip() or f"command failed: {_command_display(command)}")
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
) -> dict[str, object]:
    for _ in range(warmup):
        for command in commands:
            warmup_result = _run_command(command, env=env, capture_output=False, timeout_s=timeout_s)
            if isinstance(warmup_result, subprocess.TimeoutExpired):
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": f"command timed out after {timeout_s}s",
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
                }
            if warmup_result.returncode != 0:
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": [],
                    "stderr": (warmup_result.stderr or "").strip(),
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
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
                }
            if result.returncode != 0:
                return {
                    "status": "FAIL",
                    "median_s": None,
                    "samples_s": samples,
                    "stderr": (result.stderr or "").strip(),
                    "command_group": [_command_display(candidate) for candidate in commands],
                    "throughput_bytes_s": None,
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


def run_correctness_check(
    *,
    tg_binary: Path,
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
            "error": f"CPU correctness command timed out after {timeout_s}s",
            "matches_equal": False,
        }
    if isinstance(gpu_result, subprocess.TimeoutExpired):
        return {
            "status": "FAIL",
            "error": f"GPU correctness command timed out after {timeout_s}s",
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
    cpu_total_matches = int(cpu_payload.get("total_matches", 0))
    gpu_total_matches = int(gpu_payload.get("total_matches", 0))
    cpu_total_files = _infer_total_files(cpu_payload)
    gpu_total_files = _infer_total_files(gpu_payload)
    return {
        "status": "PASS" if cpu_total_matches == gpu_total_matches else "FAIL",
        "cpu_total_matches": cpu_total_matches,
        "gpu_total_matches": gpu_total_matches,
        "cpu_total_files": cpu_total_files,
        "gpu_total_files": gpu_total_files,
        "matches_equal": cpu_total_matches == gpu_total_matches,
        "files_equal": cpu_total_files == gpu_total_files,
    }


def create_error_fixture(error_dir: Path) -> Path:
    error_dir.mkdir(parents=True, exist_ok=True)
    (error_dir / "good.log").write_text(
        "INFO boot\nERROR gpu benchmark sentinel\n",
        encoding="utf-8",
    )
    (error_dir / "empty.log").write_text("", encoding="utf-8")
    (error_dir / "binary.bin").write_bytes(b"\x00gpu benchmark sentinel\x00")
    (error_dir / "invalid_utf8.log").write_bytes(
        b"\xff\xfeERROR gpu benchmark sentinel\n"
    )
    return error_dir


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
    invalid_device_stderr = "command timed out"
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

    nvrtc_env = _build_command_env(
        {"TG_TEST_CUDA_BEHAVIOR": "nvrtc-failure:simulated NVRTC compile error"}
    )
    nvrtc_failure = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=nvrtc_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    nvrtc_status = "FAIL"
    nvrtc_stderr = "command timed out"
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

    timeout_env = _build_command_env(
        {"TG_TEST_CUDA_BEHAVIOR": f"timeout:{timeout_simulation_ms}ms"}
    )
    timeout_result = _run_command(
        build_tg_gpu_search_command(tg_binary, pattern, corpus_dir, device_id),
        env=timeout_env,
        capture_output=True,
        timeout_s=timeout_s,
    )
    timeout_status = "FAIL"
    timeout_stderr = "command timed out"
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

    for size_bytes in corpus_sizes:
        size_label = _format_size_label(size_bytes)
        corpus_dir = bench_dir / size_label
        corpus_info = generate_gpu_scale_corpus(
            corpus_dir,
            target_bytes=size_bytes,
            shard_count=shard_count,
        )
        actual_bytes = int(corpus_info["actual_bytes"])

        rg_result = benchmark_search_command(
            build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
        )
        tg_cpu_result = benchmark_search_command(
            build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
        )
        tg_gpu_result = benchmark_search_command(
            build_tg_gpu_search_command(tg_binary, benchmark_pattern, corpus_dir, device_id),
            env=env,
            runs=runs,
            warmup=warmup,
            timeout_s=command_timeout_s,
            corpus_bytes=actual_bytes,
        )
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
            "rg": rg_result,
            "tg_cpu": tg_cpu_result,
            "tg_gpu": tg_gpu_result,
        }
        rows.append(row)

        correctness = run_correctness_check(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            pattern=benchmark_pattern,
            device_id=device_id,
            env=env,
            timeout_s=command_timeout_s,
        )
        correctness["size_label"] = size_label
        correctness["size_bytes"] = size_bytes
        if not correctness.get("matches_equal"):
            errors.append(f"GPU correctness mismatch at {size_label}.")
        correctness_checks.append(correctness)

        for candidate, name in ((rg_result, "rg"), (tg_cpu_result, "tg_cpu"), (tg_gpu_result, "tg_gpu")):
            if candidate.get("status") != "PASS":
                errors.append(f"{name} benchmark failed at {size_label}: {candidate.get('stderr', '')}")

    error_tests = run_gpu_error_tests(
        tg_binary=tg_binary,
        corpus_dir=bench_dir,
        device_id=device_id,
        timeout_s=command_timeout_s,
        timeout_simulation_ms=timeout_simulation_ms,
    )
    for name, payload in error_tests.items():
        if payload.get("status") != "PASS":
            errors.append(f"GPU error test {name} failed: {payload.get('stderr', '')}")

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

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes} for size_bytes in corpus_sizes
        ],
        "rows": rows,
        "correctness_checks": correctness_checks,
        "error_tests": error_tests,
        "crossover": crossover,
        "throughput_target": throughput_target,
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
        help="Comma-separated corpus sizes such as 10MB,100MB,500MB,1GB.",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Benchmark samples per command.")
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


def create_long_line_corpus(output_dir: Path, *, target_bytes: int, pattern: str) -> dict[str, object]:
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


def create_cuda_graph_corpus(output_dir: Path, *, file_count: int, pattern: str) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    repeated = "padding block for cuda graph batches " * 96
    body = (
        "INFO graph capture bootstrap\n"
        f"{repeated}\n"
        f"{pattern}\n"
        "WARN graph replay footer\n"
    )
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
            [build_rg_search_command(rg_binary, pattern, throughput_corpus) for pattern in throughput_patterns],
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
        gpu_stats = gpu_group.pop("payload", {}) if isinstance(gpu_group.get("payload"), dict) else {}
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
        throughput_rows.append(
            {
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
            }
        )

    advanced["throughput_rows"] = throughput_rows
    advanced["throughput_workload"] = {
        "pattern_count": len(throughput_patterns),
        "line_bytes": DEFAULT_ADVANCED_THROUGHPUT_LINE_BYTES,
        "device_ids": multi_gpu_device_ids,
        "mode": "multi-pattern sparse-match long-line native GPU summary benchmark",
    }

    stream_stats = _run_json_command(
        build_tg_gpu_native_stats_command(tg_binary, [DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]),
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
        [build_tg_cpu_search_command(tg_binary, pattern, one_gib_corpus) for pattern in multi_patterns],
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        workload_bytes=one_gib_actual_bytes * len(multi_patterns),
    )
    multi_pattern_gpu_stats = (
        multi_pattern_gpu_benchmark.pop("payload", {})
        if isinstance(multi_pattern_gpu_benchmark.get("payload"), dict)
        else {}
    )
    multi_pattern_gpu_median = multi_pattern_gpu_benchmark.get("median_s")
    multi_pattern_cpu_median = multi_pattern_cpu_benchmark.get("median_s")
    multi_pattern_speedup = (
        round(float(multi_pattern_cpu_median) / float(multi_pattern_gpu_median), 4)
        if isinstance(multi_pattern_gpu_median, (float, int))
        and isinstance(multi_pattern_cpu_median, (float, int))
        and float(multi_pattern_gpu_median) > 0
        else None
    )
    multi_pattern_pipeline = multi_pattern_gpu_stats.get("pipeline", {})
    multi_pattern_status = (
        "PASS"
        if multi_pattern_gpu_benchmark.get("status") == "PASS"
        and multi_pattern_cpu_benchmark.get("status") == "PASS"
        and int(multi_pattern_pipeline.get("pattern_count", 0)) == len(multi_patterns)
        and bool(multi_pattern_pipeline.get("single_dispatch"))
        and multi_pattern_speedup is not None
        and multi_pattern_speedup > 1.0
        else "FAIL"
    )
    advanced["multi_pattern"] = {
        "status": multi_pattern_status,
        "patterns": multi_patterns,
        "gpu": multi_pattern_gpu_benchmark,
        "cpu_sequential": multi_pattern_cpu_benchmark,
        "speedup_vs_cpu": multi_pattern_speedup,
        "gpu_stats": multi_pattern_gpu_stats,
    }
    if multi_pattern_status != "PASS":
        errors.append("Multi-pattern GPU benchmark did not beat sequential CPU execution.")

    single_gpu_benchmark = benchmark_json_metric_command(
        build_tg_gpu_native_stats_command(tg_binary, [DEFAULT_BENCHMARK_PATTERN], one_gib_corpus, [device_id]),
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
        single_gpu_benchmark.pop("payload", {}) if isinstance(single_gpu_benchmark.get("payload"), dict) else {}
    )
    multi_gpu_stats = (
        multi_gpu_benchmark.pop("payload", {}) if isinstance(multi_gpu_benchmark.get("payload"), dict) else {}
    )
    single_gpu_median = single_gpu_benchmark.get("median_s")
    multi_gpu_median = multi_gpu_benchmark.get("median_s")
    multi_gpu_improvement_pct = (
        round(
            ((float(single_gpu_median) - float(multi_gpu_median)) / float(single_gpu_median)) * 100.0,
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
        and int(multi_gpu_stats.get("total_matches", -1)) == int(multi_gpu_single_stats.get("total_matches", -2))
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
        build_tg_gpu_search_command(tg_binary, DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus, device_id),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_cpu_benchmark = benchmark_search_command(
        build_tg_cpu_search_command(tg_binary, DEFAULT_ADVANCED_LONG_LINE_PATTERN, long_line_corpus),
        env=env,
        runs=runs,
        warmup=warmup,
        timeout_s=command_timeout_s,
        corpus_bytes=long_line_actual_bytes,
    )
    long_line_stats = _run_json_command(
        build_tg_gpu_native_stats_command(tg_binary, [DEFAULT_ADVANCED_LONG_LINE_PATTERN], long_line_corpus, [device_id]),
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
        "rg_binary": str(rg_binary),
        "runs": args.runs,
        "warmup": args.warmup,
        "gpu_device_id": args.device_id,
        "command_timeout_s": args.command_timeout_s,
    }

    if not tg_binary.exists():
        payload.update(
            {
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
                "advanced": {"enabled": args.advanced},
                "crossover": {
                    "exists": False,
                    "first_gpu_faster_than_rg": None,
                    "summary": "Benchmark did not run because the tg binary was missing.",
                    "recommended_optimizations": GPU_TIMEOUT_OPTIMIZATIONS,
                },
            }
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
    payload["passed"] = not payload.get("errors")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
