from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def resolve_auto_baseline_path(current: dict, milestone: str | None) -> Path:
    current_env = current.get("environment", {})
    current_suite = str(current.get("suite") or "run_benchmarks")
    if milestone:
        if current_suite == "run_benchmarks":
            return Path(f"benchmarks/baseline_{milestone}.json")
        normalized_suite = current_suite.removeprefix("run_").removesuffix("_benchmarks")
        return Path(f"benchmarks/baseline_{normalized_suite}_{milestone}.json")

    current_platform = (
        str(current_env.get("platform")).lower()
        if isinstance(current_env, dict) and current_env.get("platform")
        else platform.system().lower()
    )
    if current_platform.startswith("win"):
        return Path("benchmarks/baselines/run_benchmarks.windows.json")
    if current_platform.startswith("linux"):
        return Path("benchmarks/baselines/run_benchmarks.ubuntu.json")
    raise SystemExit(
        "Unsupported platform for --baseline auto: "
        f"{current_platform}. Provide --baseline explicitly."
    )


def main() -> int:
    from tensor_grep.perf_guard import check_regressions, detect_environment_mismatch

    parser = argparse.ArgumentParser(
        description="Compare current benchmark JSON against a baseline."
    )
    parser.add_argument(
        "--baseline",
        default="auto",
        help=(
            "Path to baseline benchmark JSON, or `auto` to resolve "
            "benchmarks/baselines/run_benchmarks.<platform>.json"
        ),
    )
    parser.add_argument(
        "--milestone",
        default=None,
        help="Optional milestone label used with --baseline auto (for example: m1).",
    )
    parser.add_argument("--current", required=True, help="Path to current benchmark JSON")
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=5.0,
        help="Maximum allowed slowdown percentage before failing",
    )
    parser.add_argument(
        "--min-baseline-time-s",
        type=float,
        default=0.1,
        help="Ignore scenarios with baseline time below this threshold to reduce CI jitter",
    )
    parser.add_argument(
        "--allow-env-mismatch",
        action="store_true",
        help="Allow baseline/current benchmark comparison across different recorded environments",
    )
    args = parser.parse_args()

    current_path = Path(args.current)
    if not current_path.exists():
        print(f"Current result not found: {current_path}", file=sys.stderr)
        return 2

    current = json.loads(current_path.read_text(encoding="utf-8"))
    baseline_path = Path(args.baseline)
    if args.baseline == "auto":
        try:
            baseline_path = resolve_auto_baseline_path(current=current, milestone=args.milestone)
        except SystemExit as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if not baseline_path.exists():
        print(f"Baseline not found: {baseline_path}", file=sys.stderr)
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    baseline_suite = baseline.get("suite")
    current_suite = current.get("suite")
    if baseline_suite and current_suite and baseline_suite != current_suite:
        print(
            "Benchmark suite mismatch detected "
            f"(baseline={baseline_suite} current={current_suite}).",
            file=sys.stderr,
        )
        return 2

    env_mismatch = detect_environment_mismatch(baseline=baseline, current=current)
    if env_mismatch and not args.allow_env_mismatch:
        print(
            "Benchmark environment mismatch detected "
            f"({env_mismatch}). Refusing regression comparison. "
            "Use --allow-env-mismatch to override."
        )
        return 2

    regressions = check_regressions(
        baseline=baseline,
        current=current,
        max_regression_pct=args.max_regression_pct,
        min_baseline_time_s=args.min_baseline_time_s,
    )
    if regressions:
        print("Benchmark regressions detected:")
        for msg in regressions:
            print(f"- {msg}")
        return 1

    print("No benchmark regressions detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
