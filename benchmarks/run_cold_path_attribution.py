# ruff: noqa: I001
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from run_benchmarks import (  # noqa: E402
    SCENARIOS,
    benchmark_binary_warnings,
    benchmark_claim_blockers,
    benchmark_launcher_warnings,
    build_tg_benchmark_cmd_with_mode,
    classify_tg_launcher_command,
    collect_timing_samples,
    emit_benchmark_claim_blockers,
    generate_test_data,
    resolve_rg_binary,
    resolve_tg_binary_with_source,
    run_cmd_capture,
)
from tensor_grep.perf_guard import benchmark_host_key, write_json  # noqa: E402

DEFAULT_LAUNCHER_MODES = [
    "explicit_binary",
    "discovered_cli_binary",
    "python_module_launcher",
]


def resolve_bench_data_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_BENCH_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_data"


def _scenario_commands(
    *,
    bench_dir: Path,
    tg_binary: Path,
    launcher_modes: list[str],
) -> tuple[list[dict[str, object]], dict[str, str], list[str]]:
    rows: list[dict[str, object]] = []
    launcher_command_kinds: dict[str, str] = {}
    warnings: list[str] = []
    rg_binary = resolve_rg_binary()

    for scenario in SCENARIOS:
        rg_cmd = [
            rg_binary,
            "--no-ignore",
            *[str(bench_dir) if arg == "bench_data" else arg for arg in scenario["rg_args"][1:]],
        ]
        rg_time_s, rg_samples_s = collect_timing_samples(rg_cmd)

        for launcher_mode in launcher_modes:
            tg_args = [
                str(bench_dir) if arg == "bench_data" else arg for arg in scenario["tg_args"][2:]
            ]
            tg_cmd, resolved_mode = build_tg_benchmark_cmd_with_mode(
                tg_args,
                binary=tg_binary,
                return_mode=True,
                launcher_mode=launcher_mode,
            )
            command_kind = classify_tg_launcher_command(tg_cmd)
            launcher_command_kinds[resolved_mode] = command_kind
            row_warnings = benchmark_launcher_warnings(
                command_kind=command_kind,
                launcher_mode=resolved_mode,
            )
            for warning in row_warnings:
                if warning not in warnings:
                    warnings.append(warning)
            tg_time_s, tg_samples_s = collect_timing_samples(tg_cmd)
            trace_path = (
                bench_dir / f"{scenario['name'].replace(' ', '_').lower()}-{launcher_mode}.json"
            )
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            if trace_path.exists():
                trace_path.unlink()
            _, phase_trace_stdout = run_cmd_capture(
                tg_cmd,
                env_overrides={"TG_STARTUP_TRACE_PATH": str(trace_path)},
            )
            phase_trace = _parse_phase_trace(phase_trace_stdout, trace_path)

            rows.append({
                "name": f"{scenario['name']} [{launcher_mode}]",
                "scenario": scenario["name"],
                "launcher_mode": launcher_mode,
                "resolved_launcher_mode": resolved_mode,
                "tg_launcher_command_kind": command_kind,
                "rg_time_s": rg_time_s,
                "rg_samples_s": rg_samples_s,
                "tg_time_s": tg_time_s,
                "tg_samples_s": tg_samples_s,
                "phase_trace": phase_trace,
                "warnings": row_warnings,
            })

    return rows, launcher_command_kinds, warnings


def _parse_phase_trace(stdout: str, trace_path: Path | None = None) -> object | None:
    if trace_path is not None and trace_path.exists():
        try:
            return json.loads(trace_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _unique_launcher_modes(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(DEFAULT_LAUNCHER_MODES)
    result: list[str] = []
    for mode in requested:
        if mode not in result:
            result.append(mode)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a cold-path attribution benchmark for tensor-grep."
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="Path to tg binary. Defaults to rust_core/target/release/tg.exe.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path. Defaults to artifacts/bench_cold_path_attribution.json",
    )
    parser.add_argument(
        "--launcher-mode",
        action="append",
        choices=tuple(DEFAULT_LAUNCHER_MODES),
        default=None,
        help="Launcher mode to compare. May be repeated. Defaults to the full cold-path set.",
    )
    parser.add_argument(
        "--allow-claim-unsafe-launcher",
        action="store_true",
        help=(
            "Allow exploratory attribution runs even when launcher provenance is unsafe "
            "for benchmark claims, such as a stale in-tree native tg binary."
        ),
    )
    args = parser.parse_args(argv)

    tg_binary, tg_binary_source = resolve_tg_binary_with_source(args.binary)
    warnings = benchmark_binary_warnings(tg_binary)
    for warning in warnings:
        print(f"[warning] {warning}", file=sys.stderr)
    blockers = benchmark_claim_blockers(warnings)
    if blockers and not args.allow_claim_unsafe_launcher:
        emit_benchmark_claim_blockers(blockers)
        return 2

    bench_dir = resolve_bench_data_dir()
    bench_dir.mkdir(parents=True, exist_ok=True)
    generate_test_data(str(bench_dir), num_files=2, lines_per_file=2_000_000)

    launcher_modes = _unique_launcher_modes(args.launcher_mode)
    rows, launcher_command_kinds, launcher_warnings = _scenario_commands(
        bench_dir=bench_dir,
        tg_binary=tg_binary,
        launcher_modes=launcher_modes,
    )
    for warning in launcher_warnings:
        if warning not in warnings:
            warnings.append(warning)
            print(f"[warning] {warning}", file=sys.stderr)

    payload = {
        "artifact": "bench_cold_path_attribution",
        "suite": "cold_path_attribution",
        "generated_at_epoch_s": time.time(),
        "benchmark_host_key": benchmark_host_key({
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        }),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
            "tg_binary_source": tg_binary_source,
            "tg_launcher_command_kinds": launcher_command_kinds,
        },
        "host_provenance": {
            "benchmark_host_key": benchmark_host_key({
                "platform": platform.system().lower(),
                "machine": platform.machine().lower(),
                "python_version": platform.python_version(),
            }),
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
            "tg_binary_source": tg_binary_source,
            "tg_launcher_command_kinds": launcher_command_kinds,
        },
        "launcher_modes": launcher_modes,
        "warnings": warnings,
        "rows": rows,
    }

    output_path = args.output or (ROOT_DIR / "artifacts" / "bench_cold_path_attribution.json")
    write_json(output_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
