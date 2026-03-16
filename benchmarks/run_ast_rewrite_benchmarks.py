"""Benchmark AST rewrite phases on synthetic Python corpora."""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
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

from gen_corpus import generate_python_ast_bench_corpus  # noqa: E402
from run_ast_benchmarks import resolve_ast_grep_binary, resolve_tg_binary  # noqa: E402

DEFAULT_PATTERN = "def $F($$$ARGS): return $EXPR"
DEFAULT_REPLACEMENT = "lambda $$$ARGS: $EXPR"
MIN_MATCHABLE_PATTERNS_PER_FILE = 5

TOTAL_EDITS_PATTERN = re.compile(r'"total_edits"\s*:\s*(\d+)')
TOTAL_FILES_SCANNED_PATTERN = re.compile(r'"total_files_scanned"\s*:\s*(\d+)')


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_ast_rewrite.json"


def resolve_ast_rewrite_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AST_REWRITE_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_ast_rewrite"


def ensure_rewrite_bench_corpus(
    output_dir: Path,
    *,
    file_count: int,
    total_loc: int,
    seed: int,
) -> dict[str, object]:
    minimum_total_loc = file_count * MIN_MATCHABLE_PATTERNS_PER_FILE
    if total_loc < minimum_total_loc:
        raise ValueError(
            "AST rewrite benchmark requires at least 5 matchable patterns per file "
            f"(requested {file_count} files and {total_loc} LOC, need >= {minimum_total_loc} LOC)."
        )

    payload = generate_python_ast_bench_corpus(
        output_dir,
        file_count=file_count,
        total_loc=total_loc,
        seed=seed,
    )
    payload["min_rewrites_per_file"] = payload["total_loc"] // payload["file_count"]
    return payload


def copy_corpus(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tg_rewrite_bench_"))
    shutil.copytree(src, tmp / "corpus")
    return tmp / "corpus"


def median(values: list[float]) -> float:
    s = sorted(values)
    return s[len(s) // 2]


def build_tg_rewrite_cmd(
    *,
    tg_binary: Path,
    replacement: str,
    pattern: str,
    corpus_dir: Path,
    extra_args: list[str] | None = None,
) -> list[str]:
    return [
        str(tg_binary),
        "run",
        "--lang",
        "python",
        "--rewrite",
        replacement,
        *(extra_args or []),
        pattern,
        str(corpus_dir),
    ]


def run_timed_command(command: list[str]) -> float:
    start = time.perf_counter()
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - start
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}\n{stderr}")
    return elapsed


def extract_rewrite_plan_metadata(plan_output_path: Path) -> dict[str, int]:
    with plan_output_path.open("r", encoding="utf-8") as handle:
        prefix = handle.read(65536)
    total_edits_match = TOTAL_EDITS_PATTERN.search(prefix)
    total_files_scanned_match = TOTAL_FILES_SCANNED_PATTERN.search(prefix)
    if total_edits_match is None or total_files_scanned_match is None:
        raise RuntimeError(f"unable to extract rewrite metadata from {plan_output_path}")
    return {
        "total_rewrites": int(total_edits_match.group(1)),
        "total_files_scanned": int(total_files_scanned_match.group(1)),
    }


def collect_rewrite_plan_metadata(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    replacement: str,
) -> dict[str, int]:
    with tempfile.TemporaryDirectory(prefix="tg_rewrite_plan_meta_") as tmp_dir:
        plan_output_path = Path(tmp_dir) / "plan.json"
        command = build_tg_rewrite_cmd(
            tg_binary=tg_binary,
            replacement=replacement,
            pattern=pattern,
            corpus_dir=corpus_dir,
        )
        with plan_output_path.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(
                command,
                stdout=handle,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            raise RuntimeError(
                f"failed to collect rewrite metadata with exit code {completed.returncode}: {' '.join(command)}\n{stderr}"
            )
        return extract_rewrite_plan_metadata(plan_output_path)


def benchmark_tg_phase(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    replacement: str,
    runs: int,
    extra_args: list[str] | None = None,
    needs_copy: bool,
) -> dict[str, object]:
    timings: list[float] = []
    for _ in range(runs):
        work_dir = copy_corpus(corpus_dir) if needs_copy else corpus_dir
        try:
            command = build_tg_rewrite_cmd(
                tg_binary=tg_binary,
                replacement=replacement,
                pattern=pattern,
                corpus_dir=work_dir,
                extra_args=extra_args,
            )
            timings.append(run_timed_command(command))
        finally:
            if needs_copy:
                shutil.rmtree(work_dir.parent, ignore_errors=True)

    return {
        "median": median(timings),
        "samples": timings,
    }


def benchmark_sg_apply_phase(
    *,
    sg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    replacement: str,
    runs: int,
) -> dict[str, object]:
    timings: list[float] = []
    for _ in range(runs):
        work_dir = copy_corpus(corpus_dir)
        try:
            timings.append(
                run_timed_command(
                    [
                        str(sg_binary),
                        "run",
                        "--lang",
                        "python",
                        "-p",
                        pattern,
                        "-r",
                        replacement,
                        "--update-all",
                        str(work_dir),
                    ]
                )
            )
        finally:
            shutil.rmtree(work_dir.parent, ignore_errors=True)

    return {
        "median": median(timings),
        "samples": timings,
    }


def run_rewrite_benchmark(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    replacement: str,
    runs: int,
    sg_binary: Path | None,
) -> dict[str, object]:
    metadata = collect_rewrite_plan_metadata(
        tg_binary=tg_binary,
        corpus_dir=corpus_dir,
        pattern=pattern,
        replacement=replacement,
    )
    phase_timings = {
        "plan": benchmark_tg_phase(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            pattern=pattern,
            replacement=replacement,
            runs=runs,
            needs_copy=False,
        ),
        "diff": benchmark_tg_phase(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            pattern=pattern,
            replacement=replacement,
            runs=runs,
            extra_args=["--diff"],
            needs_copy=False,
        ),
        "apply": benchmark_tg_phase(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            pattern=pattern,
            replacement=replacement,
            runs=runs,
            extra_args=["--apply"],
            needs_copy=True,
        ),
    }

    results: dict[str, object] = {
        "pattern": pattern,
        "replacement": replacement,
        "corpus_dir": str(corpus_dir),
        "runs": runs,
        "total_rewrites": metadata["total_rewrites"],
        "total_files_scanned": metadata["total_files_scanned"],
        "phase_timings_s": phase_timings,
        "tg_plan_median_s": phase_timings["plan"]["median"],
        "tg_plan_times": phase_timings["plan"]["samples"],
        "tg_diff_median_s": phase_timings["diff"]["median"],
        "tg_diff_times": phase_timings["diff"]["samples"],
        "tg_apply_median_s": phase_timings["apply"]["median"],
        "tg_apply_times": phase_timings["apply"]["samples"],
    }

    if sg_binary is not None:
        sg_apply = benchmark_sg_apply_phase(
            sg_binary=sg_binary,
            corpus_dir=corpus_dir,
            pattern=pattern,
            replacement=replacement,
            runs=runs,
        )
        results["sg_apply"] = sg_apply
        results["sg_apply_median_s"] = sg_apply["median"]
        results["sg_apply_times"] = sg_apply["samples"]
        sg_median = float(sg_apply["median"])
        tg_median = float(results["tg_apply_median_s"])
        results["ratio_tg_vs_sg"] = round(tg_median / sg_median, 3) if sg_median > 0 else None
    else:
        results["sg_apply"] = None
        results["sg_apply_median_s"] = None
        results["sg_apply_times"] = []
        results["ratio_tg_vs_sg"] = None

    return results


def build_base_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "artifact": "bench_ast_rewrite",
        "suite": "run_ast_rewrite_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "pattern": args.pattern,
        "replacement": args.replacement,
        "runs": args.runs,
        "file_count": args.files,
        "total_loc": args.loc,
        "seed": args.seed,
        "minimum_matchable_patterns_per_file": MIN_MATCHABLE_PATTERNS_PER_FILE,
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    parser = argparse.ArgumentParser(description="AST rewrite benchmark")
    parser.add_argument("--binary", default=str(default_binary_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--files", type=int, default=1000, help="Synthetic corpus file count.")
    parser.add_argument("--loc", type=int, default=50000, help="Synthetic corpus total LOC.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic corpus seed.")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--replacement", default=DEFAULT_REPLACEMENT)
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve()
    payload = build_base_payload(args)
    tg_binary = resolve_tg_binary(args.binary)
    sg_binary = resolve_ast_grep_binary()

    errors: list[str] = []
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")
    minimum_total_loc = args.files * MIN_MATCHABLE_PATTERNS_PER_FILE
    if args.loc < minimum_total_loc:
        errors.append(
            "AST rewrite benchmark requires at least 5 matchable patterns per file "
            f"(requested {args.files} files and {args.loc} LOC, need >= {minimum_total_loc} LOC)."
        )

    if errors:
        payload.update({"passed": False, "error": " ".join(errors)})
        write_json(output_path, payload)
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    try:
        corpus_info = ensure_rewrite_bench_corpus(
            resolve_ast_rewrite_bench_dir(),
            file_count=args.files,
            total_loc=args.loc,
            seed=args.seed,
        )
        corpus_dir = Path(corpus_info["corpus_dir"])
        results = run_rewrite_benchmark(
            tg_binary=tg_binary,
            sg_binary=sg_binary,
            corpus_dir=corpus_dir,
            pattern=args.pattern,
            replacement=args.replacement,
            runs=args.runs,
        )
    except RuntimeError as exc:
        payload.update({"passed": False, "error": str(exc)})
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 2

    payload.update(
        {
            "tg_binary": str(tg_binary),
            "sg_binary": str(sg_binary) if sg_binary is not None else None,
            "corpus_dir": str(corpus_dir),
            "manifest_path": str(corpus_info["manifest_path"]),
            "file_count": corpus_info["file_count"],
            "total_loc": corpus_info["total_loc"],
            "min_rewrites_per_file": corpus_info["min_rewrites_per_file"],
            **results,
        }
    )
    payload["passed"] = all(
        float(payload["phase_timings_s"][phase]["median"]) > 0 for phase in ("plan", "diff", "apply")
    )

    write_json(output_path, payload)

    print(f"tg plan  median: {payload['phase_timings_s']['plan']['median']:.3f}s")
    print(f"tg diff  median: {payload['phase_timings_s']['diff']['median']:.3f}s")
    print(f"tg apply median: {payload['phase_timings_s']['apply']['median']:.3f}s")
    print(f"total rewrites:  {payload['total_rewrites']}")
    if payload.get("sg_apply_median_s"):
        print(f"sg apply median: {payload['sg_apply_median_s']:.3f}s")
        print(f"ratio (tg/sg):   {payload['ratio_tg_vs_sg']}")
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
