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

from tensor_grep.cli import repo_map, session_store  # noqa: E402


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_context_render.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark editor-plane context render cold and warm session-backed paths."
    )
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--repeats", type=int, default=3, help="Cold render timing samples.")
    parser.add_argument(
        "--session-repeats",
        type=int,
        default=3,
        help="Warm session-backed render timing samples.",
    )
    return parser.parse_args()


def _time_samples(
    fn: Callable[[], dict[str, Any]],
    *,
    repeats: int,
) -> tuple[list[float], dict[str, Any]]:
    samples: list[float] = []
    payload: dict[str, Any] | None = None
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        payload = fn()
        samples.append(round(time.perf_counter() - started, 6))
    return samples, payload or {}


def benchmark_context_render_fixture(
    fixture: dict[str, Any],
    *,
    repeats: int,
    session_repeats: int,
) -> dict[str, Any]:
    root = Path(str(fixture["root"]))
    query = str(fixture.get("query", "create invoice"))
    cold_samples, cold_payload = _time_samples(
        lambda: repo_map.build_context_render(query, root, max_files=4, max_sources=6),
        repeats=repeats,
    )
    session_id = session_store.open_session(str(root)).session_id
    warm_samples, warm_payload = _time_samples(
        lambda: session_store.session_context_render(
            session_id,
            query,
            str(root),
            max_files=4,
            max_sources=6,
        ),
        repeats=session_repeats,
    )
    return {
        "fixture": str(fixture.get("name", root.name)),
        "root": str(root),
        "file_count": int(fixture.get("file_count", 0)),
        "query": query,
        "cold_samples_s": cold_samples,
        "cold_median_s": round(float(statistics.median(cold_samples)), 6),
        "warm_session_samples_s": warm_samples,
        "warm_session_median_s": round(float(statistics.median(warm_samples)), 6),
        "session_id": session_id,
        "cold_token_estimate": int(cold_payload.get("token_estimate", 0)),
        "warm_token_estimate": int(warm_payload.get("token_estimate", 0)),
        "cold_truncated": bool(cold_payload.get("truncated", False)),
        "warm_truncated": bool(warm_payload.get("truncated", False)),
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    fixtures = ensure_editor_plane_fixture_set(resolve_editor_plane_bench_dir())

    rows = []
    for name in ("small", "medium", "large"):
        if name not in fixtures:
            continue
        fixture = dict(fixtures[name])
        fixture["name"] = name
        rows.append(
            benchmark_context_render_fixture(
                fixture,
                repeats=args.repeats,
                session_repeats=args.session_repeats,
            )
        )

    payload = {
        "artifact": "bench_context_render",
        "suite": "run_context_render_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "repeats": args.repeats,
        "session_repeats": args.session_repeats,
        "rows": rows,
    }
    write_json(output_path, payload)

    for row in rows:
        print(
            f"{row['fixture']}: cold={row['cold_median_s']:.4f}s "
            f"warm={row['warm_session_median_s']:.4f}s"
        )
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
