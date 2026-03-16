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
DEFAULT_CORPUS_SIZES = (1 * MB, 10 * MB, 100 * MB, 1 * GB)
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
PAYLOAD_FILLER = "payload=" + ("0123456789abcdef" * 224)


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
        candidates.extend(
            [
                ROOT_DIR / ".venv_cuda" / "Scripts" / "python.exe",
                ROOT_DIR / ".venv" / "Scripts" / "python.exe",
            ]
        )
    else:
        candidates.extend(
            [
                ROOT_DIR / ".venv_cuda" / "bin" / "python",
                ROOT_DIR / ".venv" / "bin" / "python",
            ]
        )

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


def benchmark_search_command(
    command: list[str],
    *,
    env: dict[str, str],
    runs: int,
    warmup: int,
) -> dict[str, object]:
    for _ in range(warmup):
        warmup_result = _run_command(command, env=env, capture_output=False)
        if warmup_result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [],
                "stderr": (warmup_result.stderr or "").strip(),
                "command": _command_display(command),
            }

    samples: list[float] = []
    last_stderr = ""
    for _ in range(runs):
        start = time.perf_counter()
        result = _run_command(command, env=env, capture_output=False)
        elapsed = time.perf_counter() - start
        if result.returncode != 0:
            return {
                "status": "FAIL",
                "median_s": None,
                "samples_s": [round(sample, 6) for sample in samples],
                "stderr": (result.stderr or "").strip(),
                "command": _command_display(command),
            }
        samples.append(round(elapsed, 6))
        last_stderr = (result.stderr or "").strip()

    return {
        "status": "PASS",
        "median_s": round(statistics.median(samples), 6),
        "samples_s": samples,
        "stderr": last_stderr,
        "command": _command_display(command),
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

    if rg_result.returncode != 0:
        return {
            "device_id": device_id,
            "pattern": pattern,
            "status": "FAIL",
            "error": (rg_result.stderr or "").strip(),
            "matches_equal": False,
            "files_equal": False,
        }
    if gpu_result.returncode != 0:
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


def analyze_gpu_auto_recommendation(rows: list[dict[str, object]]) -> dict[str, object]:
    winners: list[dict[str, object]] = []
    for row in rows:
        rg_result = row.get("rg", {})
        rg_median = rg_result.get("median_s") if isinstance(rg_result, dict) else None
        if not isinstance(rg_median, (int, float)) or rg_median <= 0:
            continue
        for gpu_result in row.get("gpu", []):
            gpu_median = gpu_result.get("median_s")
            if gpu_result.get("status") != "PASS" or not isinstance(gpu_median, (int, float)):
                continue
            speedup_vs_rg_pct = round((rg_median - gpu_median) / rg_median * 100.0, 2)
            gpu_result["speedup_vs_rg_pct"] = speedup_vs_rg_pct
            if speedup_vs_rg_pct >= 20.0:
                winners.append(
                    {
                        "device_id": gpu_result.get("device_id"),
                        "size_label": row.get("size_label"),
                        "size_bytes": row.get("size_bytes"),
                        "speedup_vs_rg_pct": speedup_vs_rg_pct,
                    }
                )

    if not winners:
        return {
            "should_add_flag": False,
            "reason": "No measured GPU/device row beat rg by at least 20% at any corpus size.",
            "winning_rows": [],
        }

    return {
        "should_add_flag": True,
        "reason": "At least one measured GPU/device row beat rg by 20% or more.",
        "winning_rows": winners,
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
    devices = list(probe.get("devices", [])) if isinstance(probe.get("devices", []), list) else []
    warnings = list(probe.get("warnings", [])) if isinstance(probe.get("warnings", []), list) else []
    errors: list[str] = []
    if probe.get("error"):
        warnings.append(str(probe["error"]))

    command_env = _build_command_env(sidecar_python)
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

        rg_result = benchmark_search_command(
            build_rg_search_command(rg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
        )
        tg_cpu_result = benchmark_search_command(
            build_tg_cpu_search_command(tg_binary, benchmark_pattern, corpus_dir),
            env=command_env,
            runs=runs,
            warmup=warmup,
        )

        gpu_results: list[dict[str, object]] = []
        for device in devices:
            entry = {
                "device_id": device.get("device_id"),
                "name": device.get("name"),
                "vram_capacity_mb": device.get("vram_capacity_mb"),
                "capability": device.get("capability"),
            }
            if not device.get("operational", False):
                entry.update(
                    {
                        "status": "UNSUPPORTED",
                        "median_s": None,
                        "samples_s": [],
                        "stderr": device.get("error", "device probe failed"),
                    }
                )
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
                )
                entry.update(result)
            gpu_results.append(entry)

        row = {
            "size_label": size_label,
            "size_bytes": size_bytes,
            "actual_bytes": corpus_info["actual_bytes"],
            "file_count": corpus_info["file_count"],
            "total_lines": corpus_info["total_lines"],
            "pattern_counts": corpus_info["pattern_counts"],
            "rg": rg_result,
            "tg_cpu": tg_cpu_result,
            "gpu": gpu_results,
        }

        rg_median = rg_result.get("median_s") if isinstance(rg_result, dict) else None
        tg_cpu_median = tg_cpu_result.get("median_s") if isinstance(tg_cpu_result, dict) else None
        for gpu_result in gpu_results:
            gpu_median = gpu_result.get("median_s")
            if isinstance(gpu_median, (int, float)) and isinstance(rg_median, (int, float)) and rg_median > 0:
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

    correctness_corpus_size = next(
        (size for size in corpus_sizes if size >= 10 * MB),
        corpus_sizes[0],
    )
    correctness_corpus_dir = generated_corpora[correctness_corpus_size]
    correctness_checks: list[dict[str, object]] = []
    for device in devices:
        if not device.get("operational", False):
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
            check["device_name"] = device.get("name")
            check["corpus_size_label"] = _format_size_label(correctness_corpus_size)
            if not (check.get("matches_equal") and check.get("files_equal")):
                errors.append(
                    f"Correctness mismatch for GPU {device.get('device_id')} pattern {pattern!r}."
                )
            correctness_checks.append(check)

    return {
        "bench_dir": str(bench_dir),
        "corpus_sizes": [
            {"label": _format_size_label(size_bytes), "bytes": size_bytes} for size_bytes in corpus_sizes
        ],
        "devices": devices,
        "rows": rows,
        "correctness_checks": correctness_checks,
        "gpu_auto_recommendation": analyze_gpu_auto_recommendation(rows),
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
        help="Comma-separated corpus sizes such as 1MB,10MB,100MB,1GB.",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Benchmark samples per command.")
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
        payload.update({
            "errors": [f"tg binary not found: {tg_binary}"],
            "warnings": [],
            "rows": [],
            "correctness_checks": [],
            "corpus_sizes": [],
            "devices": [],
            "gpu_auto_recommendation": {
                "should_add_flag": False,
                "reason": "Benchmark did not run because the tg binary was missing.",
                "winning_rows": [],
            },
        })
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
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 1 if payload.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
