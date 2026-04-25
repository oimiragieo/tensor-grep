from __future__ import annotations

# ruff: noqa: E402,I001

import argparse
import os
import platform
import shutil
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

from native_cpu_benchmark_utils import (
    ensure_large_file_fixture,
    resolve_native_cpu_bench_data_dir,
)
from run_benchmarks import (
    collect_timing_samples,
    default_binary_path,
    generate_test_data,
    resolve_bench_data_dir,
    resolve_rg_binary,
    resolve_tg_binary,
    run_cmd_capture,
)


DEFAULT_TIMING_SAMPLES = 3
DEFAULT_WARMUP_RUNS = 1


def stringify_cmd(cmd: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(cmd)
    return " ".join(cmd)


def resolve_optional_tool(name: str) -> str | None:
    return shutil.which(name)


def build_tool_commands(
    *,
    tg_binary: Path,
    rg_binary: str,
    target: Path,
    pattern: str,
) -> list[dict[str, str | list[str]]]:
    commands: list[dict[str, str | list[str]]] = [
        {
            "tool": "ripgrep",
            "command": [rg_binary, "--no-ignore", pattern, str(target)],
        },
        {
            "tool": "tensor-grep",
            "command": [str(tg_binary), "search", "--no-ignore", pattern, str(target)],
        },
        {
            "tool": "tensor-grep --cpu",
            "command": [
                str(tg_binary),
                "search",
                "--cpu",
                "--no-ignore",
                pattern,
                str(target),
            ],
        },
    ]

    git_binary = resolve_optional_tool("git")
    if git_binary:
        commands.append(
            {
                "tool": "git grep --no-index",
                "command": [git_binary, "grep", "--no-index", "-n", pattern, str(target)],
            }
        )

    ag_binary = resolve_optional_tool("ag")
    if ag_binary:
        commands.append(
            {
                "tool": "ag",
                "command": [ag_binary, "--nocolor", "--noheading", "-n", pattern, str(target)],
            }
        )

    ack_binary = resolve_optional_tool("ack")
    if ack_binary:
        commands.append(
            {
                "tool": "ack",
                "command": [
                    ack_binary,
                    "--nocolor",
                    "--nogroup",
                    "--noenv",
                    "-n",
                    pattern,
                    str(target),
                ],
            }
        )

    ugrep_binary = resolve_optional_tool("ugrep")
    if ugrep_binary:
        ugrep_cmd = [ugrep_binary, "-n", "-I", pattern, str(target)]
        if target.is_dir():
            ugrep_cmd.insert(1, "-r")
        commands.append(
            {
                "tool": "ugrep",
                "command": ugrep_cmd,
            }
        )

    grep_binary = resolve_optional_tool("grep")
    if grep_binary:
        grep_cmd = [grep_binary, "-n", "-E", pattern, str(target)]
        if target.is_dir():
            grep_cmd.insert(1, "-R")
        commands.append(
            {
                "tool": "grep",
                "command": grep_cmd,
            }
        )

    return commands


def benchmark_command(
    *,
    tool: str,
    cmd: list[str],
    sample_count: int,
    warmup_runs: int,
) -> dict[str, object]:
    for _ in range(warmup_runs):
        run_cmd_capture(cmd)

    median_s, samples_s = collect_timing_samples(cmd, sample_count=sample_count)
    capture_s, stdout = run_cmd_capture(cmd)
    line_count = len([line for line in stdout.splitlines() if line.strip()])
    return {
        "tool": tool,
        "command": stringify_cmd(cmd),
        "samples_s": samples_s,
        "median_s": median_s,
        "capture_s": round(capture_s, 6),
        "line_count": line_count,
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    parser = argparse.ArgumentParser(
        description="Run a small host-local CLI comparison benchmark for tensor-grep and available peer tools."
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
        help="Optional JSON output path. Defaults to artifacts/bench_tool_comparison.json",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_TIMING_SAMPLES,
        help="Number of timing samples per tool and scenario.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_RUNS,
        help="Number of warmup runs before timing each tool.",
    )
    args = parser.parse_args()

    tg_binary = resolve_tg_binary(args.binary)
    rg_binary = resolve_rg_binary()

    bench_dir = resolve_bench_data_dir()
    if not bench_dir.exists() or not any(bench_dir.glob("*.log")):
        generate_test_data(str(bench_dir), num_files=2, lines_per_file=2_000_000)

    native_cpu_data_dir = resolve_native_cpu_bench_data_dir()
    large_fixture = ensure_large_file_fixture(native_cpu_data_dir)
    large_file_path = Path(large_fixture["path"])

    scenarios = [
        {
            "name": "standard_corpus",
            "pattern": "ERROR",
            "target": bench_dir,
            "description": "Recursive text search over the standard benchmark corpus.",
        },
        {
            "name": "large_file_200mb",
            "pattern": "ERROR",
            "target": large_file_path,
            "description": "Single large-file search over the native CPU 200MB fixture.",
        },
    ]

    rows: list[dict[str, object]] = []
    for scenario in scenarios:
        tool_rows = [
            benchmark_command(
                tool=str(tool_payload["tool"]),
                cmd=list(tool_payload["command"]),
                sample_count=args.samples,
                warmup_runs=args.warmup,
            )
            for tool_payload in build_tool_commands(
                tg_binary=tg_binary,
                rg_binary=rg_binary,
                target=Path(scenario["target"]),
                pattern=str(scenario["pattern"]),
            )
        ]
        rg_row = next(row for row in tool_rows if row["tool"] == "ripgrep")
        rg_median = float(rg_row["median_s"])
        for row in tool_rows:
            row["scenario"] = scenario["name"]
            row["pattern"] = scenario["pattern"]
            row["target"] = str(scenario["target"])
            row["ratio_vs_ripgrep"] = (
                round(float(row["median_s"]) / rg_median, 4) if rg_median > 0 else None
            )
            rows.append(row)

    payload = {
        "artifact": "bench_tool_comparison",
        "suite": "run_tool_comparison_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
            "tg_binary_source": str(tg_binary),
            "available_tools": {
                "ripgrep": rg_binary,
                "git": resolve_optional_tool("git"),
                "ag": resolve_optional_tool("ag"),
                "ack": resolve_optional_tool("ack"),
                "ugrep": resolve_optional_tool("ugrep"),
                "grep": resolve_optional_tool("grep"),
            },
        },
        "timing_samples_per_case": args.samples,
        "warmup_runs_per_case": args.warmup,
        "notes": [
            "This host-local comparison is informational, not a release-gated regression suite.",
            "Only tools present on PATH are benchmarked.",
            "ratios are relative to ripgrep on the same scenario and host.",
        ],
        "rows": rows,
    }

    output_path = args.output or (ROOT_DIR / "artifacts" / "bench_tool_comparison.json")
    write_json(output_path, payload)

    print("\nStarting Benchmarks: host-local CLI tool comparison")
    print("-" * 108)
    print(f"{'Scenario':<18} | {'Tool':<22} | {'Median':>9} | {'vs rg':>7} | {'Lines':>8}")
    print("-" * 108)
    for row in rows:
        ratio_text = "n/a"
        if isinstance(row.get("ratio_vs_ripgrep"), (float, int)):
            ratio_text = f"{float(row['ratio_vs_ripgrep']):.3f}x"
        print(
            f"{row['scenario']!s:<18} | {row['tool']!s:<22} | "
            f"{float(row['median_s']):>8.3f}s | {ratio_text:>7} | {int(row['line_count']):>8}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
