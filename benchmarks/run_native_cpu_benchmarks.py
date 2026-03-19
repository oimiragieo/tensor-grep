from __future__ import annotations

import argparse
import os
import platform
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

from native_cpu_benchmark_utils import (  # noqa: E402
    DEFAULT_LARGE_FILE_BYTES,
    DEFAULT_MANY_FILE_COUNT,
    ensure_large_file_fixture,
    ensure_many_file_fixture,
    resolve_native_cpu_bench_data_dir,
)
from run_benchmarks import (  # noqa: E402
    collect_timing_samples,
    default_binary_path,
    generate_test_data,
    resolve_bench_data_dir,
    resolve_rg_binary,
    resolve_tg_binary,
    run_cmd_capture,
    run_cmd_timing,
)

DEFAULT_TIMING_SAMPLES = 5
DEFAULT_WARMUP_RUNS = 1


def build_rg_search_command(
    rg_binary: str,
    pattern: str,
    target: Path,
    *,
    fixed_strings: bool = False,
) -> list[str]:
    cmd = [rg_binary, "--no-ignore"]
    if fixed_strings:
        cmd.append("-F")
    cmd.extend([pattern, str(target)])
    return cmd


def build_tg_cpu_search_command(
    tg_binary: Path,
    pattern: str,
    target: Path,
    *,
    fixed_strings: bool = False,
) -> list[str]:
    cmd = [str(tg_binary), "search", "--cpu", "--no-ignore"]
    if fixed_strings:
        cmd.append("-F")
    cmd.extend([pattern, str(target)])
    return cmd


def build_rg_count_command(
    rg_binary: str,
    pattern: str,
    target: Path,
    *,
    fixed_strings: bool = False,
) -> list[str]:
    cmd = [rg_binary, "--no-ignore", "-c"]
    if fixed_strings:
        cmd.append("-F")
    cmd.extend([pattern, str(target)])
    return cmd


def build_tg_cpu_count_command(
    tg_binary: Path,
    pattern: str,
    target: Path,
    *,
    fixed_strings: bool = False,
) -> list[str]:
    cmd = [str(tg_binary), "search", "--cpu", "--no-ignore", "-c"]
    if fixed_strings:
        cmd.append("-F")
    cmd.extend([pattern, str(target)])
    return cmd


def sum_count_output(text: str) -> int:
    total = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(":")
        value = parts[-1].strip()
        if value.isdigit():
            total += int(value)
    return total


def run_match_count(cmd: list[str]) -> dict[str, object]:
    elapsed_s, stdout = run_cmd_capture(cmd)
    return {
        "seconds": round(elapsed_s, 6),
        "total_matches": sum_count_output(stdout),
    }


def run_native_cpu_benchmark_case(
    *,
    name: str,
    pattern: str,
    target: Path,
    rg_binary: str,
    tg_binary: Path,
    sample_count: int,
    warmup_runs: int,
    max_ratio_vs_rg: float,
    require_tg_faster: bool = False,
    fixed_strings: bool = False,
    benchmark_count_mode: bool = False,
) -> dict[str, object]:
    if benchmark_count_mode:
        rg_cmd = build_rg_count_command(rg_binary, pattern, target, fixed_strings=fixed_strings)
        tg_cmd = build_tg_cpu_count_command(tg_binary, pattern, target, fixed_strings=fixed_strings)
    else:
        rg_cmd = build_rg_search_command(rg_binary, pattern, target, fixed_strings=fixed_strings)
        tg_cmd = build_tg_cpu_search_command(
            tg_binary, pattern, target, fixed_strings=fixed_strings
        )

    for _ in range(warmup_runs):
        run_cmd_timing(rg_cmd)
        run_cmd_timing(tg_cmd)

    rg_time, rg_samples = collect_timing_samples(rg_cmd, sample_count=sample_count)
    tg_time, tg_samples = collect_timing_samples(tg_cmd, sample_count=sample_count)

    rg_counts = run_match_count(
        build_rg_count_command(rg_binary, pattern, target, fixed_strings=fixed_strings)
    )
    tg_counts = run_match_count(
        build_tg_cpu_count_command(tg_binary, pattern, target, fixed_strings=fixed_strings)
    )
    counts_match = rg_counts["total_matches"] == tg_counts["total_matches"]

    ratio_vs_rg = round(tg_time / rg_time, 4) if rg_time > 0 else None
    threshold_pass = ratio_vs_rg is not None and ratio_vs_rg <= max_ratio_vs_rg
    if require_tg_faster:
        threshold_pass = bool(rg_time > tg_time)
    status = "PASS" if counts_match and threshold_pass else "FAIL"

    return {
        "name": name,
        "pattern": pattern,
        "target": str(target),
        "rg_cmd": subprocess.list2cmdline(rg_cmd) if os.name == "nt" else " ".join(rg_cmd),
        "tg_cmd": subprocess.list2cmdline(tg_cmd) if os.name == "nt" else " ".join(tg_cmd),
        "rg_samples_s": rg_samples,
        "rg_time_s": rg_time,
        "tg_samples_s": tg_samples,
        "tg_time_s": tg_time,
        "ratio_vs_rg": ratio_vs_rg,
        "threshold_ratio": max_ratio_vs_rg,
        "require_tg_faster": require_tg_faster,
        "fixed_strings": fixed_strings,
        "benchmark_count_mode": benchmark_count_mode,
        "status": status,
        "counts_match": counts_match,
        "rg_total_matches": rg_counts["total_matches"],
        "tg_total_matches": tg_counts["total_matches"],
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    parser = argparse.ArgumentParser(
        description="Benchmark native CPU search against ripgrep on standard, large-file, and many-file corpora."
    )
    parser.add_argument(
        "--binary",
        default=str(default_binary_path()),
        help="Path to tg binary. Defaults to rust_core/target/release/tg.exe.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path. Defaults to artifacts/bench_run_native_cpu_benchmarks.json",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_TIMING_SAMPLES,
        help="Number of timing samples per benchmark case.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_RUNS,
        help="Number of warmup runs before timing.",
    )
    args = parser.parse_args()

    tg_binary = resolve_tg_binary(args.binary)
    rg_binary = resolve_rg_binary()

    bench_dir = resolve_bench_data_dir()
    if not bench_dir.exists() or not any(bench_dir.glob("*.log")):
        generate_test_data(str(bench_dir), num_files=2, lines_per_file=2_000_000)

    native_cpu_data_dir = resolve_native_cpu_bench_data_dir()
    large_fixture = ensure_large_file_fixture(native_cpu_data_dir)
    many_fixture = ensure_many_file_fixture(native_cpu_data_dir)

    cases = [
        {
            "name": "cold_standard_corpus",
            "pattern": "ERROR",
            "target": bench_dir,
            "max_ratio_vs_rg": 1.05,
            "require_tg_faster": False,
            "fixed_strings": False,
        },
        {
            "name": "large_file_200mb",
            "pattern": "ERROR",
            "target": Path(large_fixture["path"]),
            "max_ratio_vs_rg": 1.15,
            "require_tg_faster": False,
            "fixed_strings": False,
            "benchmark_count_mode": False,
        },
        {
            "name": "large_file_200mb_count",
            "pattern": "ERROR",
            "target": Path(large_fixture["path"]),
            "max_ratio_vs_rg": 1.0,
            "require_tg_faster": True,
            "fixed_strings": False,
            "benchmark_count_mode": True,
        },
        {
            "name": "many_file_directory",
            "pattern": "ERROR",
            "target": Path(many_fixture["path"]),
            "max_ratio_vs_rg": 1.05,
            "require_tg_faster": False,
            "fixed_strings": False,
            "benchmark_count_mode": False,
        },
    ]

    rows = [
        run_native_cpu_benchmark_case(
            name=case["name"],
            pattern=case["pattern"],
            target=Path(case["target"]),
            rg_binary=rg_binary,
            tg_binary=tg_binary,
            sample_count=args.samples,
            warmup_runs=args.warmup,
            max_ratio_vs_rg=case["max_ratio_vs_rg"],
            require_tg_faster=case["require_tg_faster"],
            fixed_strings=case["fixed_strings"],
            benchmark_count_mode=case.get("benchmark_count_mode", False),
        )
        for case in cases
    ]
    passed = all(row["status"] == "PASS" for row in rows)

    payload = {
        "artifact": "bench_run_native_cpu_benchmarks",
        "suite": "run_native_cpu_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "timing_samples_per_case": args.samples,
        "warmup_runs_per_case": args.warmup,
        "tg_mode": "native_binary_direct",
        "thresholds": {
            "cold_standard_corpus_max_ratio_vs_rg": 1.05,
            "large_file_200mb_max_ratio_vs_rg": 1.15,
            "large_file_200mb_count_requires_tg_faster": True,
            "many_file_directory_max_ratio_vs_rg": 1.05,
        },
        "fixtures": {
            "standard_corpus": {
                "path": str(bench_dir),
            },
            "large_file": {
                "path": str(large_fixture["path"]),
                "target_bytes": DEFAULT_LARGE_FILE_BYTES,
                "actual_bytes": large_fixture.get("actual_bytes"),
                "cache_hit": large_fixture.get("cache_hit"),
            },
            "many_file": {
                "path": str(many_fixture["path"]),
                "file_count": many_fixture.get("file_count", DEFAULT_MANY_FILE_COUNT),
                "actual_bytes": many_fixture.get("actual_bytes"),
                "cache_hit": many_fixture.get("cache_hit"),
            },
        },
        "passed": passed,
        "rows": rows,
    }

    output_path = args.output or (ROOT_DIR / "artifacts" / "bench_run_native_cpu_benchmarks.json")
    write_json(output_path, payload)

    print("\nStarting Benchmarks: native CPU engine vs ripgrep")
    print("-" * 98)
    print(f"{'Scenario':<24} | {'ripgrep':>9} | {'tg-native':>9} | {'Ratio':>7} | {'Status':>6}")
    print("-" * 98)
    for row in rows:
        ratio_text = "n/a"
        if isinstance(row.get("ratio_vs_rg"), (float, int)):
            ratio_text = f"{float(row['ratio_vs_rg']):.3f}x"
        print(
            f"{row['name']:<24} | {float(row['rg_time_s']):>8.3f}s | {float(row['tg_time_s']):>8.3f}s | "
            f"{ratio_text:>7} | {row['status']:>6}"
        )

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
