from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from tensor_grep.perf_guard import write_json  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_external_profile_analysis.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate profiling phases from bakeoff or external-eval artifacts."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    return parser.parse_args()


def _collect_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifact = str(payload.get("artifact", ""))
    if artifact == "bench_bakeoff":
        return [dict(row) for row in list(payload.get("rows", []))]
    if artifact == "bench_external_eval":
        rows: list[dict[str, Any]] = []
        for pack in list(payload.get("packs", [])):
            if not isinstance(pack, dict):
                continue
            for row in list(pack.get("rows", [])):
                if isinstance(row, dict):
                    rows.append(dict(row))
        return rows
    raise ValueError(f"Unsupported artifact for profiling analysis: {artifact}")


def analyze_external_profiling(payload: dict[str, Any]) -> dict[str, Any]:
    phase_elapsed: dict[str, float] = {}
    phase_calls: dict[str, int] = {}
    total_elapsed = 0.0
    profiled_rows = 0
    for row in _collect_rows(payload):
        profiling = row.get("_profiling")
        if not isinstance(profiling, dict):
            continue
        profiled_rows += 1
        total_elapsed += float(profiling.get("total_elapsed_s", 0.0))
        for phase in list(profiling.get("phases", [])):
            if not isinstance(phase, dict):
                continue
            name = str(phase.get("name", ""))
            if not name:
                continue
            phase_elapsed[name] = phase_elapsed.get(name, 0.0) + float(phase.get("elapsed_s", 0.0))
            phase_calls[name] = phase_calls.get(name, 0) + int(phase.get("calls", 0))
    phases: list[dict[str, Any]] = []
    for name in sorted(phase_elapsed):
        elapsed = phase_elapsed[name]
        calls = phase_calls.get(name, 0)
        phases.append(
            {
                "name": name,
                "elapsed_s": round(elapsed, 6),
                "calls": calls,
                "avg_elapsed_s": round(elapsed / max(calls, 1), 6),
                "percent_total_elapsed": round((elapsed / total_elapsed) * 100.0, 4)
                if total_elapsed
                else 0.0,
            }
        )
    phases.sort(key=lambda phase: (-float(phase["elapsed_s"]), str(phase["name"])))
    return {
        "artifact": "bench_external_profile_analysis",
        "suite": "analyze_external_profiling",
        "generated_at_epoch_s": time.time(),
        "input_artifact": str(payload.get("artifact", "")),
        "profiled_rows": profiled_rows,
        "total_profiled_elapsed_s": round(total_elapsed, 6),
        "phases": phases,
        "dominant_phases": phases[:10],
    }


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    analysis = analyze_external_profiling(payload)
    output_path = Path(args.output).expanduser().resolve()
    write_json(output_path, analysis)
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
