from __future__ import annotations

import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gen_corpus import write_manifest  # noqa: E402
from run_ast_benchmarks import (  # noqa: E402
    build_command_string,
    resolve_hyperfine_binary,
    resolve_tg_binary,
)

DEFAULT_SCALES = (1000, 5000, 10000)
DEFAULT_QUERY_PATTERNS = (
    "ERROR timeout",
    "WARN retry budget",
    "trace_id=",
)
DEFAULT_LINES_PER_FILE = 12
BUILD_TIME_THRESHOLD_S = 60.0
REQUIRED_MIN_SCALE_FILES = 10000


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_index_scaling.json"


def resolve_index_scaling_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_INDEX_SCALING_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_index_scaling"


def parse_scales(value: str) -> tuple[int, ...]:
    try:
        scales = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "scales must be a comma-separated list of integers"
        ) from exc
    if not scales:
        raise argparse.ArgumentTypeError("at least one file-count scale is required")
    if any(scale <= 0 for scale in scales):
        raise argparse.ArgumentTypeError("all scales must be positive integers")
    return scales


def _recreate_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _build_log_lines(file_index: int, *, lines_per_file: int, rng: random.Random) -> list[str]:
    lines: list[str] = []
    for line_index in range(lines_per_file):
        trace_id = f"{file_index:05d}-{line_index:02d}-{rng.randint(1000, 9999)}"
        if line_index % 4 == 0:
            lines.append(
                "ERROR timeout contacting primary-db "
                f"service=auth trace_id={trace_id} shard={file_index % 32}\n"
            )
        elif line_index % 4 == 1:
            lines.append(
                "WARN retry budget exhausted "
                f"service=worker trace_id={trace_id} attempt={line_index + 1}\n"
            )
        elif line_index % 4 == 2:
            lines.append(
                f"INFO request completed service=api trace_id={trace_id} file={file_index:05d}\n"
            )
        else:
            lines.append(
                f"DEBUG cache warm service=search trace_id={trace_id} bucket={file_index // 250:04d}\n"
            )
    return lines


def generate_index_scaling_corpus(
    output_dir: Path,
    *,
    file_count: int,
    lines_per_file: int,
    seed: int,
) -> dict[str, object]:
    _recreate_dir(output_dir)
    rng = random.Random(seed)
    total_lines = 0

    for file_index in range(file_count):
        shard_dir = output_dir / f"bucket_{file_index // 250:04d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        file_path = shard_dir / f"log_{file_index:05d}.log"
        lines = _build_log_lines(file_index, lines_per_file=lines_per_file, rng=rng)
        total_lines += len(lines)
        file_path.write_text("".join(lines), encoding="utf-8")

    manifest_path = write_manifest(
        output_dir, output_dir.parent / f"{output_dir.name}.manifest.sha256"
    )
    return {
        "corpus_dir": output_dir,
        "manifest_path": manifest_path,
        "file_count": file_count,
        "lines_per_file": lines_per_file,
        "total_lines": total_lines,
        "seed": seed,
    }


def build_tg_index_search_cmd(*, tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--index",
        "--fixed-strings",
        "--no-ignore",
        "--count",
        pattern,
        str(corpus_dir),
    ]


def build_tg_plain_search_cmd(*, tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [
        str(tg_binary),
        "search",
        "--fixed-strings",
        "--no-ignore",
        "--count",
        pattern,
        str(corpus_dir),
    ]


def build_remove_index_command(index_path: Path) -> str:
    script = f"from pathlib import Path; p = Path(r'''{index_path}'''); p.unlink(missing_ok=True)"
    return build_command_string([sys.executable, "-c", script])


def run_hyperfine_benchmark(
    hyperfine_path: Path,
    *,
    commands: list[str],
    runs: int,
    warmup: int,
    prepare: str | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tg_index_scaling_hf_") as tmp_dir:
        export_path = Path(tmp_dir) / "hyperfine.json"
        cmd = [
            str(hyperfine_path),
            "--runs",
            str(runs),
            "--warmup",
            str(warmup),
            "--export-json",
            str(export_path),
        ]
        if prepare:
            cmd.extend(["--prepare", prepare])
        cmd.extend(commands)
        completed = subprocess.run(cmd, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"hyperfine failed with exit code {completed.returncode}: {' '.join(commands)}"
            )
        return json.loads(export_path.read_text(encoding="utf-8"))


def run_count_command(command: list[str]) -> int:
    completed = subprocess.run(
        command,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}\n{stderr}"
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError(f"command produced no count output: {' '.join(command)}")
    try:
        return int(stdout)
    except ValueError as exc:
        raise RuntimeError(f"expected integer count output, saw: {stdout!r}") from exc


def benchmark_scale(
    *,
    tg_binary: Path,
    hyperfine_binary: Path,
    corpus_info: dict[str, object],
    query_patterns: tuple[str, ...],
    runs: int,
    warmup: int,
) -> dict[str, object]:
    corpus_dir = Path(corpus_info["corpus_dir"])
    index_path = corpus_dir / ".tg_index"

    build_command = build_tg_index_search_cmd(
        tg_binary=tg_binary,
        pattern=query_patterns[0],
        corpus_dir=corpus_dir,
    )
    build_command_string_value = build_command_string(build_command)
    build_hyperfine = run_hyperfine_benchmark(
        hyperfine_binary,
        commands=[build_command_string_value],
        runs=runs,
        warmup=warmup,
        prepare=build_remove_index_command(index_path),
    )
    build_time_s = round(float(build_hyperfine["results"][0]["median"]), 6)

    if not index_path.exists():
        raise RuntimeError(f"index file was not created by tg search --index: {index_path}")

    query_commands = [
        build_tg_index_search_cmd(tg_binary=tg_binary, pattern=pattern, corpus_dir=corpus_dir)
        for pattern in query_patterns
    ]
    plain_query_commands = [
        build_tg_plain_search_cmd(tg_binary=tg_binary, pattern=pattern, corpus_dir=corpus_dir)
        for pattern in query_patterns
    ]
    query_command_strings = [build_command_string(command) for command in query_commands]
    query_hyperfine = run_hyperfine_benchmark(
        hyperfine_binary,
        commands=query_command_strings,
        runs=runs,
        warmup=warmup,
    )

    queries: list[dict[str, object]] = []
    for pattern, command, plain_command, result in zip(
        query_patterns,
        query_commands,
        plain_query_commands,
        query_hyperfine["results"],
        strict=True,
    ):
        median_s = round(float(result["median"]), 6)
        indexed_matches = run_count_command(command)
        plain_matches = run_count_command(plain_command)
        counts_match = indexed_matches == plain_matches
        queries.append(
            {
                "pattern": pattern,
                "median_s": median_s,
                "matches": indexed_matches,
                "indexed_matches": indexed_matches,
                "plain_matches": plain_matches,
                "counts_match": counts_match,
                "command": build_command_string(command),
                "plain_command": build_command_string(plain_command),
            }
        )

    query_median_s = round(statistics.median(query["median_s"] for query in queries), 6)
    build_within_threshold = build_time_s <= BUILD_TIME_THRESHOLD_S
    query_correct = all(bool(query["counts_match"]) for query in queries)
    return {
        "name": f"index_scale_{corpus_info['file_count']}_files",
        "file_count": corpus_info["file_count"],
        "lines_per_file": corpus_info["lines_per_file"],
        "total_lines": corpus_info["total_lines"],
        "corpus_dir": str(corpus_dir),
        "manifest_path": str(corpus_info["manifest_path"]),
        "index_path": str(index_path),
        "build_pattern": query_patterns[0],
        "build_command": build_command_string_value,
        "build_time_s": build_time_s,
        "build_time_threshold_s": BUILD_TIME_THRESHOLD_S,
        "build_within_threshold": build_within_threshold,
        "index_size_bytes": index_path.stat().st_size,
        "query_median_s": query_median_s,
        "query_correct": query_correct,
        "queries": queries,
        "build_hyperfine": build_hyperfine,
        "query_hyperfine": query_hyperfine,
    }


def run_index_scaling_benchmark(
    *,
    tg_binary: Path,
    hyperfine_binary: Path,
    bench_dir: Path,
    scales: tuple[int, ...],
    lines_per_file: int,
    seed: int,
    query_patterns: tuple[str, ...],
    runs: int,
    warmup: int,
) -> dict[str, object]:
    rows = []
    for index, file_count in enumerate(scales):
        corpus_info = generate_index_scaling_corpus(
            bench_dir / f"scale_{file_count:05d}",
            file_count=file_count,
            lines_per_file=lines_per_file,
            seed=seed + index,
        )
        rows.append(
            benchmark_scale(
                tg_binary=tg_binary,
                hyperfine_binary=hyperfine_binary,
                corpus_info=corpus_info,
                query_patterns=query_patterns,
                runs=runs,
                warmup=warmup,
            )
        )

    return {
        "bench_dir": str(bench_dir),
        "build_time_threshold_s": BUILD_TIME_THRESHOLD_S,
        "required_min_scale_files": REQUIRED_MIN_SCALE_FILES,
        "required_scale_validated": any(
            row["file_count"] >= REQUIRED_MIN_SCALE_FILES and row["build_within_threshold"]
            for row in rows
        ),
        "rows": rows,
        "passed": all(
            row["build_time_s"] > 0
            and row["index_size_bytes"] > 0
            and row["query_median_s"] > 0
            and len(row["queries"]) >= 3
            and row["query_correct"]
            for row in rows
        )
        and any(
            row["file_count"] >= REQUIRED_MIN_SCALE_FILES and row["build_within_threshold"]
            for row in rows
        ),
    }


def build_base_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "artifact": "bench_index_scaling",
        "suite": "run_index_scaling_benchmark",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "scales": list(args.scales),
        "query_patterns": list(DEFAULT_QUERY_PATTERNS),
        "lines_per_file": args.lines_per_file,
        "build_time_threshold_s": BUILD_TIME_THRESHOLD_S,
        "required_min_scale_files": REQUIRED_MIN_SCALE_FILES,
        "query_latency_gated": False,
        "runs": args.runs,
        "warmup": args.warmup,
        "seed": args.seed,
        "rows": [],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark tg search --index build cost, warm query latency, and index size across file-count scales."
    )
    parser.add_argument("--binary", default=str(default_binary_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument(
        "--scales",
        type=parse_scales,
        default=DEFAULT_SCALES,
        help="Comma-separated file-count scales to benchmark (default: 1000,5000,10000).",
    )
    parser.add_argument("--lines-per-file", type=int, default=DEFAULT_LINES_PER_FILE)
    parser.add_argument(
        "--runs", type=int, default=3, help="Number of hyperfine runs per build/query command."
    )
    parser.add_argument("--warmup", type=int, default=1, help="Number of hyperfine warmup runs.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic corpus seed.")
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    payload = build_base_payload(args)
    tg_binary = resolve_tg_binary(args.binary)
    hyperfine_binary = resolve_hyperfine_binary()

    errors: list[str] = []
    if args.lines_per_file < 1:
        errors.append("lines-per-file must be >= 1")
    if args.runs < 1:
        errors.append("runs must be >= 1")
    if args.warmup < 0:
        errors.append("warmup must be >= 0")
    if len(args.scales) < 3:
        errors.append("at least three scales are required to measure index scaling")
    if max(args.scales) < REQUIRED_MIN_SCALE_FILES:
        errors.append(f"at least one scale must be >= {REQUIRED_MIN_SCALE_FILES} files")
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")
    if hyperfine_binary is None:
        errors.append(
            "hyperfine was not found. Install it (for example `cargo install hyperfine --locked`) or set HYPERFINE_BINARY."
        )

    if errors:
        payload.update({"passed": False, "error": " ".join(errors)})
        write_json(output_path, payload)
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    assert hyperfine_binary is not None
    bench_dir = resolve_index_scaling_bench_dir()
    try:
        results = run_index_scaling_benchmark(
            tg_binary=tg_binary,
            hyperfine_binary=hyperfine_binary,
            bench_dir=bench_dir,
            scales=args.scales,
            lines_per_file=args.lines_per_file,
            seed=args.seed,
            query_patterns=DEFAULT_QUERY_PATTERNS,
            runs=args.runs,
            warmup=args.warmup,
        )
    except (RuntimeError, ValueError) as exc:
        payload.update({"passed": False, "error": str(exc)})
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 2

    payload.update(
        {
            "tg_binary": str(tg_binary),
            "hyperfine_binary": str(hyperfine_binary),
            **results,
        }
    )
    write_json(output_path, payload)

    for row in payload["rows"]:
        print(
            f"{row['file_count']} files: build={row['build_time_s']:.3f}s "
            f"query_median={row['query_median_s']:.3f}s index_size={row['index_size_bytes']}B "
            f"build_within_threshold={row['build_within_threshold']} query_correct={row['query_correct']}"
        )
    print(f"Results written to {output_path}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
