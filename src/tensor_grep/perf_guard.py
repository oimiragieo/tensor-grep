from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def ensure_artifacts_dir(root_dir: Path) -> Path:
    artifacts_dir = root_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_regressions(
    baseline: dict[str, Any], current: dict[str, Any], max_regression_pct: float = 10.0
) -> list[str]:
    """
    Compare scenario timings. Returns a list of regression messages.
    Lower times are better.
    """
    regressions: list[str] = []
    baseline_rows = {row["name"]: row for row in baseline.get("rows", [])}
    current_rows = {row["name"]: row for row in current.get("rows", [])}

    for name, cur in current_rows.items():
        base = baseline_rows.get(name)
        if not base:
            continue
        cur_time = cur.get("tg_time_s")
        base_time = base.get("tg_time_s")
        if not isinstance(cur_time, (float, int)) or not isinstance(base_time, (float, int)):
            continue
        if base_time <= 0:
            continue
        pct_delta = ((float(cur_time) - float(base_time)) / float(base_time)) * 100.0
        if pct_delta > max_regression_pct:
            regressions.append(
                f"{name}: tg_time_s regressed by {pct_delta:.2f}% "
                f"(baseline={base_time:.3f}s current={cur_time:.3f}s)"
            )
    return regressions
