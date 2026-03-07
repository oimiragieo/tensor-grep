from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tensor_grep.perf_guard import check_regressions, detect_environment_mismatch


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare current benchmark JSON against a baseline."
    )
    parser.add_argument("--baseline", required=True, help="Path to baseline benchmark JSON")
    parser.add_argument("--current", required=True, help="Path to current benchmark JSON")
    parser.add_argument(
        "--max-regression-pct",
        type=float,
        default=10.0,
        help="Maximum allowed slowdown percentage before failing",
    )
    parser.add_argument(
        "--min-baseline-time-s",
        type=float,
        default=0.2,
        help="Ignore scenarios with baseline time below this threshold to reduce CI jitter",
    )
    parser.add_argument(
        "--allow-env-mismatch",
        action="store_true",
        help="Allow baseline/current benchmark comparison across different recorded environments",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    current_path = Path(args.current)
    if not baseline_path.exists():
        print(f"Baseline not found: {baseline_path}", file=sys.stderr)
        return 2
    if not current_path.exists():
        print(f"Current result not found: {current_path}", file=sys.stderr)
        return 2

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(current_path.read_text(encoding="utf-8"))

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
