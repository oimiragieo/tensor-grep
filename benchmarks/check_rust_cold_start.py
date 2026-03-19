from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO_NAME = "1. Simple String Match"


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def resolve_baseline_path(selection: str) -> Path:
    if selection != "auto":
        return Path(selection)

    current_platform = platform.system().lower()
    if current_platform.startswith("win"):
        return ROOT_DIR / "benchmarks" / "baselines" / "run_benchmarks.windows.json"
    if current_platform.startswith("linux"):
        return ROOT_DIR / "benchmarks" / "baselines" / "run_benchmarks.ubuntu.json"
    raise SystemExit(
        f"Unsupported platform for --baseline auto: {current_platform}. Provide --baseline explicitly."
    )


def resolve_hyperfine_binary() -> Path | None:
    if env_value := os.environ.get("HYPERFINE_BINARY"):
        candidate = Path(env_value)
        if candidate.exists():
            return candidate

    for name in ("hyperfine", "hyperfine.exe"):
        if resolved := shutil.which(name):
            return Path(resolved)

    cargo_candidate = Path.home() / ".cargo" / "bin" / "hyperfine.exe"
    if cargo_candidate.exists():
        return cargo_candidate

    return None


def load_baseline_seconds(baseline_path: Path, scenario_name: str) -> float:
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    for row in data.get("rows", []):
        if row.get("name") == scenario_name:
            return float(row["tg_time_s"])
    raise SystemExit(f"Scenario '{scenario_name}' not found in baseline file: {baseline_path}")


def build_command_string(binary: Path, pattern: str, search_path: Path) -> str:
    args = [str(binary), pattern, str(search_path)]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return " ".join(shlex.quote(part) for part in args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a hyperfine cold-start benchmark for tg.exe and compare it to the recorded Python bootstrap baseline."
    )
    parser.add_argument(
        "--binary",
        default=str(default_binary_path()),
        help="Path to the tg executable to benchmark.",
    )
    parser.add_argument(
        "--pattern",
        default="ERROR",
        help="Search pattern passed to tg.exe.",
    )
    parser.add_argument(
        "--path",
        default=str(ROOT_DIR / "bench_data"),
        help="Search path passed to tg.exe.",
    )
    parser.add_argument(
        "--baseline",
        default="auto",
        help="Path to the benchmark baseline JSON, or `auto` to resolve the platform baseline.",
    )
    parser.add_argument(
        "--scenario-name",
        default=DEFAULT_SCENARIO_NAME,
        help="Scenario name to read from the baseline benchmark JSON.",
    )
    parser.add_argument(
        "--min-improvement-ms",
        type=float,
        default=50.0,
        help="Minimum required improvement over the recorded baseline in milliseconds.",
    )
    parser.add_argument("--runs", type=int, default=30, help="Number of hyperfine benchmark runs.")
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
        help="Number of hyperfine warmup runs before measurement.",
    )
    parser.add_argument(
        "--output",
        help="Optional path for a machine-readable benchmark summary JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    binary_path = Path(args.binary)
    search_path = Path(args.path)
    baseline_path = resolve_baseline_path(args.baseline)
    hyperfine_path = resolve_hyperfine_binary()

    if not binary_path.exists():
        print(f"tg binary not found: {binary_path}", file=sys.stderr)
        return 2
    if not search_path.exists():
        print(f"Search path not found: {search_path}", file=sys.stderr)
        return 2
    if not baseline_path.exists():
        print(f"Baseline file not found: {baseline_path}", file=sys.stderr)
        return 2
    if hyperfine_path is None:
        print(
            "hyperfine was not found. Install it (for example `cargo install hyperfine --locked`) or set HYPERFINE_BINARY.",
            file=sys.stderr,
        )
        return 2

    baseline_seconds = load_baseline_seconds(baseline_path, args.scenario_name)
    command_string = build_command_string(binary_path, args.pattern, search_path)

    with tempfile.TemporaryDirectory() as tmp_dir:
        export_path = Path(tmp_dir) / "hyperfine.json"
        cmd = [
            str(hyperfine_path),
            "--runs",
            str(args.runs),
            "--warmup",
            str(args.warmup),
            "--export-json",
            str(export_path),
            command_string,
        ]
        completed = subprocess.run(cmd, check=False)
        if completed.returncode != 0:
            return completed.returncode

        hyperfine_data = json.loads(export_path.read_text(encoding="utf-8"))

    median_seconds = float(hyperfine_data["results"][0]["median"])
    improvement_ms = (baseline_seconds - median_seconds) * 1000.0
    required_target_seconds = baseline_seconds - (args.min_improvement_ms / 1000.0)
    passed = median_seconds <= required_target_seconds

    summary = {
        "binary": str(binary_path),
        "pattern": args.pattern,
        "path": str(search_path),
        "scenario_name": args.scenario_name,
        "baseline_path": str(baseline_path),
        "baseline_seconds": baseline_seconds,
        "median_seconds": median_seconds,
        "required_target_seconds": required_target_seconds,
        "improvement_ms": improvement_ms,
        "min_improvement_ms": args.min_improvement_ms,
        "runs": args.runs,
        "warmup": args.warmup,
        "command": command_string,
        "passed": passed,
        "hyperfine": hyperfine_data,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(
        f"Baseline={baseline_seconds:.3f}s median={median_seconds:.3f}s improvement={improvement_ms:.1f}ms target<= {required_target_seconds:.3f}s"
    )
    if passed:
        print("Rust cold-start benchmark gate passed.")
        return 0

    print(
        f"Rust cold-start benchmark gate failed: required at least {args.min_improvement_ms:.1f}ms improvement.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
