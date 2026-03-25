from __future__ import annotations

import argparse
import platform
import statistics
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

from editor_plane_benchmark_utils import (  # noqa: E402
    ensure_editor_plane_fixture_set,
    resolve_editor_plane_bench_dir,
)

from tensor_grep.cli import repo_map  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_blast_radius.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark editor-plane blast-radius render latency at increasing graph depths."
    )
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--repeats", type=int, default=3)
    return parser.parse_args()


def benchmark_blast_radius_fixture(
    fixture: dict[str, Any],
    *,
    repeats: int,
) -> list[dict[str, Any]]:
    root = Path(str(fixture["root"]))
    rows: list[dict[str, Any]] = []
    for scenario in list(fixture.get("blast_radius_symbols", [])):
        symbol = str(scenario["symbol"])
        graph_depth = int(scenario["depth"])
        samples: list[float] = []
        payload: dict[str, Any] | None = None
        for _ in range(max(1, repeats)):
            started = time.perf_counter()
            payload = repo_map.build_symbol_blast_radius_render(
                symbol,
                root,
                max_depth=graph_depth,
                max_files=6,
                max_sources=6,
            )
            samples.append(round(time.perf_counter() - started, 6))
        rows.append(
            {
                "fixture": str(fixture.get("name", root.name)),
                "symbol": symbol,
                "graph_depth": graph_depth,
                "file_count": int(fixture.get("file_count", 0)),
                "samples_s": samples,
                "median_s": round(float(statistics.median(samples)), 6),
                "returned_files": len(payload.get("files", [])) if payload else 0,
                "returned_tests": len(payload.get("tests", [])) if payload else 0,
            }
        )
    return rows


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    fixtures = ensure_editor_plane_fixture_set(resolve_editor_plane_bench_dir())
    ordered_names = [name for name in ("medium", "large") if name in fixtures] or sorted(fixtures)

    rows: list[dict[str, Any]] = []
    for name in ordered_names:
        fixture = dict(fixtures[name])
        fixture["name"] = name
        rows.extend(benchmark_blast_radius_fixture(fixture, repeats=args.repeats))

    payload = {
        "artifact": "bench_blast_radius",
        "suite": "run_blast_radius_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "repeats": args.repeats,
        "rows": rows,
    }
    write_json(output_path, payload)

    for row in rows:
        print(
            f"{row['fixture']} depth={row['graph_depth']}: "
            f"{row['median_s']:.4f}s ({row['symbol']})"
        )
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
