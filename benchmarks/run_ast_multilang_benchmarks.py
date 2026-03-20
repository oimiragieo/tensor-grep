from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
BENCHMARKS_DIR = Path(__file__).resolve().parent
for candidate in (SRC_DIR, BENCHMARKS_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gen_corpus import generate_ast_bench_corpus  # noqa: E402
from run_ast_benchmarks import (  # noqa: E402
    build_command_string,
    build_sg_ast_benchmark_cmd,
    build_tg_ast_benchmark_cmd,
    resolve_ast_grep_binary,
    resolve_hyperfine_binary,
    resolve_tg_binary,
    run_hyperfine,
)

LANGUAGE_BENCHMARKS: tuple[dict[str, str], ...] = (
    {
        "language": "python",
        "pattern": "def $F($$$ARGS): return $EXPR",
    },
    {
        "language": "javascript",
        "pattern": "function $F($$$ARGS) { return $EXPR; }",
    },
    {
        "language": "typescript",
        "pattern": "function $F($$$ARGS): $T { return $EXPR; }",
    },
    {
        "language": "rust",
        "pattern": "fn $F($$$ARGS) -> $RET { $BODY }",
    },
)


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_ast_multilang.json"


def resolve_ast_multilang_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_AST_MULTILANG_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_ast_multilang"


def ensure_multilang_ast_bench_corpus(
    output_dir: Path,
    *,
    lang: str,
    file_count: int,
    total_loc: int,
    seed: int,
) -> dict[str, object]:
    return generate_ast_bench_corpus(
        output_dir,
        lang=lang,
        file_count=file_count,
        total_loc=total_loc,
        seed=seed,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure tg run vs sg run cold-start AST performance across multiple languages."
    )
    parser.add_argument("--binary", default=None, help="Optional path to the tg binary.")
    parser.add_argument(
        "--output",
        default=str(default_output_path()),
        help="Machine-readable output artifact path.",
    )
    parser.add_argument("--runs", type=int, default=10, help="Number of hyperfine runs.")
    parser.add_argument("--warmup", type=int, default=0, help="Number of hyperfine warmup runs.")
    parser.add_argument("--files", type=int, default=1000, help="Synthetic corpus file count.")
    parser.add_argument("--loc", type=int, default=50000, help="Synthetic corpus total LOC.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic corpus seed.")
    parser.add_argument(
        "--python-max-ratio",
        type=float,
        default=1.1,
        help="Maximum allowed tg_median / sg_median ratio for the Python row.",
    )
    return parser.parse_args()


def benchmark_language(
    *,
    tg_binary: Path,
    sg_binary: Path,
    hyperfine_binary: Path,
    bench_dir: Path,
    language: str,
    pattern: str,
    file_count: int,
    total_loc: int,
    seed: int,
    runs: int,
    warmup: int,
    python_max_ratio: float,
) -> dict[str, object]:
    corpus_info = ensure_multilang_ast_bench_corpus(
        bench_dir / language,
        lang=language,
        file_count=file_count,
        total_loc=total_loc,
        seed=seed,
    )
    corpus_dir = Path(corpus_info["corpus_dir"])
    tg_command = build_tg_ast_benchmark_cmd(
        ["run", "--lang", language, pattern, str(corpus_dir)],
        binary=tg_binary,
    )
    sg_command = build_sg_ast_benchmark_cmd(
        binary=sg_binary,
        lang=language,
        pattern=pattern,
        corpus_dir=corpus_dir,
    )
    command_strings = [build_command_string(tg_command), build_command_string(sg_command)]
    hyperfine_data = run_hyperfine(
        hyperfine_binary,
        commands=command_strings,
        runs=runs,
        warmup=warmup,
    )

    results = hyperfine_data["results"]
    tg_median = float(results[0]["median"])
    sg_median = float(results[1]["median"])
    ratio = tg_median / sg_median if sg_median else float("inf")
    gated = language == "python"
    passed = ratio <= python_max_ratio if gated else True

    return {
        "language": language,
        "pattern": pattern,
        "file_count": corpus_info["file_count"],
        "total_loc": corpus_info["total_loc"],
        "corpus_dir": str(corpus_dir),
        "manifest_path": str(corpus_info["manifest_path"]),
        "tg_command": command_strings[0],
        "sg_command": command_strings[1],
        "tg_median_s": round(tg_median, 6),
        "sg_median_s": round(sg_median, 6),
        "ratio": round(ratio, 6),
        "gated": gated,
        "threshold": python_max_ratio if gated else None,
        "passed": passed,
        "hyperfine": hyperfine_data,
    }


def build_base_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "artifact": "bench_ast_multilang",
        "suite": "run_ast_multilang_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "runs": args.runs,
        "warmup": args.warmup,
        "file_count": args.files,
        "total_loc": args.loc,
        "seed": args.seed,
        "thresholds": {
            "python_max_ratio": args.python_max_ratio,
        },
        "rows": [],
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    payload = build_base_payload(args)
    tg_binary = resolve_tg_binary(args.binary)
    sg_binary = resolve_ast_grep_binary()
    hyperfine_binary = resolve_hyperfine_binary()

    errors: list[str] = []
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")
    if sg_binary is None:
        errors.append(
            "ast-grep binary not found. Install it via `cargo install ast-grep --version 0.41.1` or set AST_GREP_BINARY."
        )
    if hyperfine_binary is None:
        errors.append(
            "hyperfine was not found. Install it (for example `cargo install hyperfine --locked`) or set HYPERFINE_BINARY."
        )

    if errors:
        payload.update(
            {
                "passed": False,
                "python_ratio_gate_passed": False,
                "error": " ".join(errors),
            }
        )
        write_json(output_path, payload)
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    assert sg_binary is not None
    assert hyperfine_binary is not None
    bench_dir = resolve_ast_multilang_bench_dir()
    rows = [
        benchmark_language(
            tg_binary=tg_binary,
            sg_binary=sg_binary,
            hyperfine_binary=hyperfine_binary,
            bench_dir=bench_dir,
            language=entry["language"],
            pattern=entry["pattern"],
            file_count=args.files,
            total_loc=args.loc,
            seed=args.seed,
            runs=args.runs,
            warmup=args.warmup,
            python_max_ratio=args.python_max_ratio,
        )
        for entry in LANGUAGE_BENCHMARKS
    ]
    python_row = next(row for row in rows if row["language"] == "python")
    payload.update(
        {
            "tg_binary": str(tg_binary),
            "sg_binary": str(sg_binary),
            "hyperfine_binary": str(hyperfine_binary),
            "bench_dir": str(bench_dir),
            "rows": rows,
            "python_ratio_gate_passed": python_row["passed"],
            "passed": bool(python_row["passed"]),
        }
    )
    write_json(output_path, payload)

    for row in rows:
        gate_text = f" threshold<={args.python_max_ratio:.3f}" if row["gated"] else ""
        print(
            f"{row['language']}: tg_median={row['tg_median_s']:.3f}s "
            f"sg_median={row['sg_median_s']:.3f}s ratio={row['ratio']:.3f}{gate_text}"
        )

    return 0 if python_row["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
