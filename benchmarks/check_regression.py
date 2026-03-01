from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tensor_grep.perf_guard import check_regressions


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

    regressions = check_regressions(
        baseline=baseline, current=current, max_regression_pct=args.max_regression_pct
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
