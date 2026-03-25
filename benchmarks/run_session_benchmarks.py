from __future__ import annotations

import argparse
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Iterable
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

_REFRESH_RATIO_THRESHOLD = 0.5
_REFRESH_COMPARISON_TARGET_FILE_COUNT = 1200


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_session.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark editor-plane session open, query, and incremental refresh latency."
    )
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--query-repeats", type=int, default=3)
    parser.add_argument(
        "--modified-file-counts",
        default="1,3,5",
        help="Comma-separated counts for incremental refresh comparison scenarios.",
    )
    return parser.parse_args()


def _copy_fixture_root(root: Path) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="tg_editor_plane_session_"))
    work_root = temp_root / root.name
    shutil.copytree(root, work_root)
    shutil.rmtree(work_root / ".tensor-grep", ignore_errors=True)
    return work_root


def _resolve_modified_counts(raw_counts: str) -> tuple[int, ...]:
    counts = [int(part.strip()) for part in raw_counts.split(",") if part.strip()]
    return tuple(count for count in counts if count > 0) or (1, 3, 5)


def _rewrite_file(path: Path, marker: str) -> None:
    original = path.read_text(encoding="utf-8")
    path.write_text(f"{original.rstrip()}\n{marker}\n", encoding="utf-8")


def _mapped_mutable_paths(fixture: dict[str, Any], work_root: Path) -> list[Path]:
    original_root = Path(str(fixture["root"]))
    mutable_paths: list[Path] = []
    for raw_path in fixture.get("mutable_files", []):
        source_path = Path(str(raw_path))
        try:
            relative = source_path.resolve().relative_to(original_root.resolve())
        except ValueError:
            continue
        mutable_paths.append((work_root / relative).resolve())
    return [path for path in mutable_paths if path.exists()]


def _amplify_work_root(work_root: Path, *, target_file_count: int) -> int:
    current_files = sorted(work_root.rglob("*.py"))
    if len(current_files) >= target_file_count:
        return len(current_files)

    src_dir = work_root / "src"
    extra_needed = target_file_count - len(current_files)
    for index in range(extra_needed):
        path = src_dir / f"amplify_{index:04d}.py"
        assignment_block = "\n".join(
            f"AMPLIFY_{index:04d}_{inner:03d} = {index + inner}"
            for inner in range(240)
        )
        path.write_text(
            "\n".join(
                [
                    f'"""Synthetic refresh corpus file {index:04d}."""',
                    "",
                    assignment_block,
                    "",
                    f"def amplify_{index:04d}_seed(value: int) -> int:",
                    "    return value + 1",
                    "",
                    f"def amplify_{index:04d}_branch(value: int) -> int:",
                    f"    return amplify_{index:04d}_seed(value) + 2",
                    "",
                    f"def amplify_{index:04d}_leaf(value: int) -> int:",
                    f"    return amplify_{index:04d}_branch(value) + 3",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return len(sorted(work_root.rglob("*.py")))


def _measure_refresh_path(
    fixture: dict[str, Any],
    *,
    modified_file_count: int,
    incremental: bool,
) -> tuple[float, dict[str, Any], list[str], int]:
    work_root = _copy_fixture_root(Path(str(fixture["root"])))
    try:
        actual_file_count = _amplify_work_root(
            work_root,
            target_file_count=_REFRESH_COMPARISON_TARGET_FILE_COUNT,
        )
        opened = session_store.open_session(str(work_root))
        previous_payload = session_store.get_session(opened.session_id, str(work_root))
        previous_map = dict(previous_payload["repo_map"])
        mutable_paths = _mapped_mutable_paths(fixture, work_root)
        if len(mutable_paths) < modified_file_count:
            raise RuntimeError(
                f"fixture {fixture.get('name', work_root.name)} has only {len(mutable_paths)} mutable files"
            )

        time.sleep(0.01)
        modified_paths: list[str] = []
        for index, path in enumerate(mutable_paths[:modified_file_count]):
            _rewrite_file(path, f"BENCHMARK_TOUCH_{modified_file_count}_{index} = {index}")
            modified_paths.append(str(path.resolve()))

        changeset = session_store._stale_changeset(previous_payload)
        if changeset is None:
            raise RuntimeError("expected a non-empty changeset after modifying the fixture")
        started = time.perf_counter()
        payload = (
            repo_map.build_repo_map_incremental(previous_map, changeset)
            if incremental
            else repo_map.build_repo_map(work_root)
        )
        elapsed = round(time.perf_counter() - started, 6)
        return elapsed, payload, modified_paths, actual_file_count
    finally:
        shutil.rmtree(work_root.parent, ignore_errors=True)


def benchmark_session_fixture(
    fixture: dict[str, Any],
    *,
    query_repeats: int,
) -> dict[str, Any]:
    work_root = _copy_fixture_root(Path(str(fixture["root"])))
    try:
        started = time.perf_counter()
        opened = session_store.open_session(str(work_root))
        open_session_s = round(time.perf_counter() - started, 6)

        query = str(fixture.get("query", "create invoice"))
        query_samples_s: list[float] = []
        payload: dict[str, Any] | None = None
        for _ in range(max(1, query_repeats)):
            started = time.perf_counter()
            payload = session_store.session_context_render(
                opened.session_id,
                query,
                str(work_root),
                max_files=4,
                max_sources=6,
            )
            query_samples_s.append(round(time.perf_counter() - started, 6))

        return {
            "fixture": str(fixture.get("name", Path(str(fixture["root"])).name)),
            "file_count": int(fixture.get("file_count", 0)),
            "open_session_s": open_session_s,
            "query_samples_s": query_samples_s,
            "query_median_s": round(float(statistics.median(query_samples_s)), 6),
            "session_id": opened.session_id,
            "query": query,
            "query_token_estimate": int(payload.get("token_estimate", 0)) if payload else 0,
        }
    finally:
        shutil.rmtree(work_root.parent, ignore_errors=True)


def benchmark_incremental_refresh_comparison(
    fixture: dict[str, Any],
    *,
    modified_file_counts: Iterable[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for modified_file_count in modified_file_counts:
        (
            incremental_refresh_s,
            _incremental_payload,
            modified_paths,
            actual_file_count,
        ) = _measure_refresh_path(
            fixture,
            modified_file_count=modified_file_count,
            incremental=True,
        )
        full_rebuild_s, _full_payload, _, _ = _measure_refresh_path(
            fixture,
            modified_file_count=modified_file_count,
            incremental=False,
        )
        ratio = (
            round(incremental_refresh_s / full_rebuild_s, 4)
            if full_rebuild_s > 0
            else float("inf")
        )
        rows.append(
            {
                "fixture": str(fixture.get("name", Path(str(fixture["root"])).name)),
                "file_count": int(fixture.get("file_count", 0)),
                "comparison_file_count": actual_file_count,
                "modified_file_count": modified_file_count,
                "modified_paths": modified_paths,
                "incremental_refresh_s": incremental_refresh_s,
                "full_rebuild_s": full_rebuild_s,
                "ratio": ratio,
                "passed_ratio_gate": bool(
                    full_rebuild_s > 0
                    and incremental_refresh_s < (_REFRESH_RATIO_THRESHOLD * full_rebuild_s)
                ),
                "refresh_type": "incremental",
                "full_refresh_type": "full",
            }
        )
    return rows


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    fixtures = ensure_editor_plane_fixture_set(resolve_editor_plane_bench_dir())
    ordered_fixtures = sorted(
        (
            {**fixture, "name": name}
            for name, fixture in fixtures.items()
        ),
        key=lambda fixture: (int(fixture.get("file_count", 0)), str(fixture.get("name", ""))),
    )
    modified_file_counts = _resolve_modified_counts(args.modified_file_counts)

    session_rows = [
        benchmark_session_fixture(fixture, query_repeats=args.query_repeats)
        for fixture in ordered_fixtures
    ]
    largest_fixture = max(ordered_fixtures, key=lambda fixture: int(fixture.get("file_count", 0)))
    refresh_rows = benchmark_incremental_refresh_comparison(
        largest_fixture,
        modified_file_counts=modified_file_counts,
    )
    passed = all(bool(row.get("passed_ratio_gate")) for row in refresh_rows)

    payload = {
        "artifact": "bench_session",
        "suite": "run_session_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "query_repeats": args.query_repeats,
        "modified_file_counts": list(modified_file_counts),
        "refresh_ratio_threshold": _REFRESH_RATIO_THRESHOLD,
        "session_rows": session_rows,
        "refresh_rows": refresh_rows,
        "rows": [*session_rows, *refresh_rows],
        "passed": passed,
    }
    write_json(output_path, payload)

    for row in session_rows:
        print(
            f"{row['fixture']}: open={row['open_session_s']:.4f}s "
            f"query={row['query_median_s']:.4f}s"
        )
    for row in refresh_rows:
        print(
            f"{row['fixture']} refresh modified={row['modified_file_count']}: "
            f"incremental={row['incremental_refresh_s']:.4f}s "
            f"full={row['full_rebuild_s']:.4f}s ratio={row['ratio']:.4f}"
        )
    print(f"Results written to {output_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
