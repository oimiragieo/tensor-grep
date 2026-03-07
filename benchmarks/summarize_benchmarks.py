from __future__ import annotations

import argparse
import json
from pathlib import Path

from tensor_grep.perf_guard import check_regressions, detect_environment_mismatch


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_row(rows: list[dict], name: str) -> dict | None:
    for row in rows:
        if row.get("name") == name:
            return row
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Render benchmark summary in markdown.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--max-regression-pct", type=float, default=10.0)
    parser.add_argument("--min-baseline-time-s", type=float, default=0.2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    baseline = _load_json(Path(args.baseline))
    current = _load_json(Path(args.current))

    regressions = check_regressions(
        baseline=baseline,
        current=current,
        max_regression_pct=args.max_regression_pct,
        min_baseline_time_s=args.min_baseline_time_s,
    )
    env_mismatch = detect_environment_mismatch(baseline=baseline, current=current)

    baseline_rows = baseline.get("rows", [])
    current_rows = current.get("rows", [])

    lines = [
        "## Benchmark Regression Report",
        "",
        f"- Baseline: `{args.baseline}`",
        f"- Current: `{args.current}`",
        f"- Max regression threshold: `{args.max_regression_pct:.1f}%`",
        f"- Min baseline time: `{args.min_baseline_time_s:.3f}s`",
        (
            f"- Environment mismatch: `{env_mismatch}`"
            if env_mismatch
            else "- Environment mismatch: `none`"
        ),
        "",
        "| Scenario | Baseline tg (s) | Current tg (s) | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]

    for cur in current_rows:
        name = str(cur.get("name", "unknown"))
        base = _find_row(baseline_rows, name)
        base_t = base.get("tg_time_s") if base else None
        cur_t = cur.get("tg_time_s")
        if isinstance(base_t, (int, float)) and isinstance(cur_t, (int, float)) and base_t > 0:
            delta = ((float(cur_t) - float(base_t)) / float(base_t)) * 100.0
            delta_s = f"{delta:+.2f}%"
            lines.append(f"| {name} | {base_t:.3f} | {cur_t:.3f} | {delta_s} |")
        else:
            lines.append(f"| {name} | n/a | {cur_t if cur_t is not None else 'n/a'} | n/a |")

    lines.append("")
    if regressions:
        lines.append("### Regressions")
        for r in regressions:
            lines.append(f"- {r}")
    else:
        lines.append("### Regressions")
        lines.append("- None")

    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
