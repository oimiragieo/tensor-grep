"""Benchmark AST rewrite: tg run --rewrite vs sg run --rewrite."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BENCHMARKS_DIR = Path(__file__).resolve().parent
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

from gen_corpus import generate_python_ast_bench_corpus  # noqa: E402

DEFAULT_PATTERN = "def $F($$$ARGS): return $EXPR"
DEFAULT_REPLACEMENT = "lambda $$$ARGS: $EXPR"


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def resolve_corpus_dir() -> Path:
    return ROOT_DIR / "artifacts" / "bench_ast_data"


def ensure_corpus(output_dir: Path) -> dict[str, object]:
    return generate_python_ast_bench_corpus(
        output_dir,
        file_count=1000,
        total_loc=50000,
        seed=42,
    )


def copy_corpus(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tg_rewrite_bench_"))
    shutil.copytree(src, tmp / "corpus")
    return tmp / "corpus"


def run_rewrite_benchmark(
    tg_binary: Path,
    corpus_dir: Path,
    pattern: str,
    replacement: str,
    runs: int,
) -> dict[str, object]:
    results: dict[str, object] = {
        "pattern": pattern,
        "replacement": replacement,
        "corpus_dir": str(corpus_dir),
        "runs": runs,
    }

    # --- tg plan-only (dry-run) ---
    tg_plan_times = []
    for _ in range(runs):
        work = copy_corpus(corpus_dir)
        try:
            start = time.perf_counter()
            subprocess.run(
                [str(tg_binary), "run", "--lang", "python", "--rewrite", replacement, pattern, str(work)],
                capture_output=True,
                check=True,
            )
            tg_plan_times.append(time.perf_counter() - start)
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)

    results["tg_plan_median_s"] = sorted(tg_plan_times)[len(tg_plan_times) // 2]
    results["tg_plan_times"] = tg_plan_times

    # --- tg apply ---
    tg_apply_times = []
    for _ in range(runs):
        work = copy_corpus(corpus_dir)
        try:
            start = time.perf_counter()
            subprocess.run(
                [str(tg_binary), "run", "--lang", "python", "--rewrite", replacement, "--apply", pattern, str(work)],
                capture_output=True,
                check=True,
            )
            tg_apply_times.append(time.perf_counter() - start)
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)

    results["tg_apply_median_s"] = sorted(tg_apply_times)[len(tg_apply_times) // 2]
    results["tg_apply_times"] = tg_apply_times

    # --- sg rewrite (--update-all for non-interactive) ---
    sg_cmd = shutil.which("sg") or shutil.which("sg.CMD")
    if sg_cmd:
        sg_times = []
        for _ in range(runs):
            work = copy_corpus(corpus_dir)
            try:
                start = time.perf_counter()
                subprocess.run(
                    [sg_cmd, "run", "--lang", "python", "-p", pattern, "-r", replacement, "--update-all", str(work)],
                    capture_output=True,
                    check=True,
                )
                sg_times.append(time.perf_counter() - start)
            finally:
                shutil.rmtree(work.parent, ignore_errors=True)

        results["sg_apply_median_s"] = sorted(sg_times)[len(sg_times) // 2]
        results["sg_apply_times"] = sg_times

        tg_med = results["tg_apply_median_s"]
        sg_med = results["sg_apply_median_s"]
        results["ratio_tg_vs_sg"] = round(tg_med / sg_med, 3) if sg_med > 0 else None
    else:
        results["sg_apply_median_s"] = None
        results["sg_apply_times"] = []
        results["ratio_tg_vs_sg"] = None

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="AST rewrite benchmark")
    parser.add_argument("--binary", type=Path, default=default_binary_path())
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "artifacts" / "bench_ast_rewrite.json")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--replacement", default=DEFAULT_REPLACEMENT)
    args = parser.parse_args()

    corpus_dir = resolve_corpus_dir()
    ensure_corpus(corpus_dir)

    results = run_rewrite_benchmark(args.binary, corpus_dir, args.pattern, args.replacement, args.runs)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2))

    print(f"tg plan  median: {results['tg_plan_median_s']:.3f}s")
    print(f"tg apply median: {results['tg_apply_median_s']:.3f}s")
    if results.get("sg_apply_median_s"):
        print(f"sg apply median: {results['sg_apply_median_s']:.3f}s")
        print(f"ratio (tg/sg):   {results['ratio_tg_vs_sg']}")
    print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
