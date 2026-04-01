from __future__ import annotations

import argparse
import platform
import statistics
import sys
import time
from collections.abc import Callable
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
    return ROOT_DIR / "artifacts" / "bench_editor_profiling.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark editor-plane context-render and blast-radius-render profiling phases."
    )
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--provider",
        default="native",
        choices=("native", "lsp", "hybrid"),
        help="Semantic provider mode for blast-radius-render measurements.",
    )
    return parser.parse_args()


def _time_profiled_samples(
    fn: Callable[[], dict[str, Any]],
    *,
    repeats: int,
) -> tuple[list[float], list[float], dict[str, Any]]:
    samples: list[float] = []
    profiling_samples: list[float] = []
    payload: dict[str, Any] | None = None
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        payload = fn()
        samples.append(round(time.perf_counter() - started, 6))
        profiling = dict(payload.get("_profiling", {}))
        profiling_samples.append(round(float(profiling.get("total_elapsed_s", 0.0)), 6))
    return samples, profiling_samples, payload or {}


def _profiling_fields(
    payload: dict[str, Any],
) -> tuple[float, dict[str, float], list[dict[str, Any]]]:
    profiling = dict(payload.get("_profiling", {}))
    return (
        float(profiling.get("total_elapsed_s", 0.0)),
        {
            str(name): float(value)
            for name, value in dict(profiling.get("breakdown_pct", {})).items()
        },
        [dict(phase) for phase in list(profiling.get("phases", []))],
    )


def benchmark_context_render_fixture(
    fixture: dict[str, Any],
    *,
    repeats: int,
) -> dict[str, Any]:
    root = Path(str(fixture["root"]))
    query = str(fixture.get("query", "create invoice"))
    samples, profiling_samples, payload = _time_profiled_samples(
        lambda: repo_map.build_context_render(
            query,
            root,
            max_files=4,
            max_sources=6,
            profile=True,
        ),
        repeats=repeats,
    )
    _, profiling_breakdown_pct, profiling_phases = _profiling_fields(payload)
    return {
        "fixture": str(fixture.get("name", root.name)),
        "mode": "context-render",
        "root": str(root),
        "file_count": int(fixture.get("file_count", 0)),
        "query": query,
        "samples_s": samples,
        "median_s": round(float(statistics.median(samples)), 6),
        "profiling_samples_s": profiling_samples,
        "profiling_total_elapsed_s": round(float(statistics.median(profiling_samples)), 6),
        "profiling_breakdown_pct": profiling_breakdown_pct,
        "profiling_phases": profiling_phases,
        "returned_files": len(payload.get("files", [])),
        "returned_tests": len(payload.get("tests", [])),
        "token_estimate": int(payload.get("token_estimate", 0)),
        "truncated": bool(payload.get("truncated", False)),
    }


def _blast_radius_target(fixture: dict[str, Any]) -> tuple[str, int]:
    scenarios = list(fixture.get("blast_radius_symbols", []))
    if scenarios:
        selected = max(scenarios, key=lambda item: int(item.get("depth", 0)))
        return str(selected.get("symbol", fixture.get("target_symbol", "create_invoice"))), int(
            selected.get("depth", 3)
        )
    return str(fixture.get("target_symbol", "create_invoice")), 3


def benchmark_blast_radius_fixture(
    fixture: dict[str, Any],
    *,
    repeats: int,
    provider: str = "native",
) -> dict[str, Any]:
    root = Path(str(fixture["root"]))
    symbol, max_depth = _blast_radius_target(fixture)
    samples, profiling_samples, payload = _time_profiled_samples(
        lambda: repo_map.build_symbol_blast_radius_render(
            symbol,
            root,
            max_depth=max_depth,
            max_files=6,
            max_sources=6,
            profile=True,
            semantic_provider=provider,
        ),
        repeats=repeats,
    )
    _, profiling_breakdown_pct, profiling_phases = _profiling_fields(payload)
    return {
        "fixture": str(fixture.get("name", root.name)),
        "mode": "blast-radius-render",
        "root": str(root),
        "file_count": int(fixture.get("file_count", 0)),
        "symbol": symbol,
        "max_depth": max_depth,
        "semantic_provider": provider,
        "samples_s": samples,
        "median_s": round(float(statistics.median(samples)), 6),
        "profiling_samples_s": profiling_samples,
        "profiling_total_elapsed_s": round(float(statistics.median(profiling_samples)), 6),
        "profiling_breakdown_pct": profiling_breakdown_pct,
        "profiling_phases": profiling_phases,
        "returned_files": len(payload.get("files", [])),
        "returned_tests": len(payload.get("tests", [])),
        "token_estimate": int(payload.get("token_estimate", 0)),
        "truncated": bool(payload.get("truncated", False)),
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    fixtures = ensure_editor_plane_fixture_set(resolve_editor_plane_bench_dir())

    rows: list[dict[str, Any]] = []
    for name in ("small", "medium", "large"):
        if name not in fixtures:
            continue
        fixture = dict(fixtures[name])
        fixture["name"] = name
        rows.append(benchmark_context_render_fixture(fixture, repeats=args.repeats))
        rows.append(
            benchmark_blast_radius_fixture(fixture, repeats=args.repeats, provider=args.provider)
        )

    payload = {
        "artifact": "bench_editor_profiling",
        "suite": "run_editor_profiling",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "repeats": args.repeats,
        "semantic_provider": args.provider,
        "rows": rows,
    }
    write_json(output_path, payload)

    for row in rows:
        label = row.get("query") or row.get("symbol") or row["fixture"]
        print(
            f"{row['fixture']} {row['mode']}: wall={row['median_s']:.4f}s "
            f"profiled={row['profiling_total_elapsed_s']:.4f}s ({label})"
        )
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
