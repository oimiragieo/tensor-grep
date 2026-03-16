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
        else:
            tg_gpu_result["ratio_vs_rg"] = None

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

    crossover = analyze_crossover(rows)
    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes} for size_bytes in corpus_sizes
        ],
        "rows": rows,
        "correctness_checks": correctness_checks,
        "error_tests": error_tests,
        "crossover": crossover,
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
    return parser


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
    )
    payload.update(result)
    payload["passed"] = not payload.get("errors")
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
