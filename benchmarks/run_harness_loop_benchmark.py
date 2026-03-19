from __future__ import annotations

import argparse
import json
import os
import platform
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

from gen_corpus import generate_python_ast_bench_corpus  # noqa: E402

DEFAULT_PATTERN = "def $F($$$ARGS): return $EXPR"
DEFAULT_REPLACEMENT = "lambda $$$ARGS: $EXPR"


def default_binary_path() -> Path:
    binary_name = "tg.exe" if os.name == "nt" else "tg"
    return ROOT_DIR / "rust_core" / "target" / "release" / binary_name


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_harness_loop.json"


def resolve_tg_binary(binary: str | None = None) -> Path:
    return Path(binary).expanduser().resolve() if binary else default_binary_path()


def resolve_harness_loop_bench_dir() -> Path:
    override = os.environ.get("TENSOR_GREP_HARNESS_LOOP_BENCH_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return ROOT_DIR / "artifacts" / "bench_harness_loop"


def ensure_harness_loop_bench_corpus(
    output_dir: Path,
    *,
    file_count: int,
    total_loc: int,
    seed: int,
) -> dict[str, object]:
    return generate_python_ast_bench_corpus(
        output_dir,
        file_count=file_count,
        total_loc=total_loc,
        seed=seed,
    )


def copy_corpus(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="tg_harness_loop_"))
    shutil.copytree(src, tmp / "corpus")
    return tmp / "corpus"


def build_tg_ast_search_cmd(*, tg_binary: Path, pattern: str, corpus_dir: Path) -> list[str]:
    return [
        str(tg_binary),
        "run",
        "--lang",
        "python",
        "--json",
        pattern,
        str(corpus_dir),
    ]


def build_tg_rewrite_plan_cmd(
    *,
    tg_binary: Path,
    replacement: str,
    pattern: str,
    corpus_dir: Path,
) -> list[str]:
    return [
        str(tg_binary),
        "run",
        "--lang",
        "python",
        "--rewrite",
        replacement,
        "--json",
        pattern,
        str(corpus_dir),
    ]


def build_tg_rewrite_apply_cmd(
    *,
    tg_binary: Path,
    replacement: str,
    pattern: str,
    corpus_dir: Path,
) -> list[str]:
    return [
        str(tg_binary),
        "run",
        "--lang",
        "python",
        "--rewrite",
        replacement,
        "--apply",
        "--json",
        pattern,
        str(corpus_dir),
    ]


def run_json_command(command: list[str]) -> tuple[float, dict[str, object]]:
    start = time.perf_counter()
    completed = subprocess.run(
        command,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    elapsed = round(time.perf_counter() - start, 6)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}\n{stderr}"
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise RuntimeError(f"command produced no JSON output: {' '.join(command)}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command produced invalid JSON: {' '.join(command)}\n{stdout}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"command produced non-object JSON: {' '.join(command)}")

    return elapsed, payload


def extract_total_matches(payload: dict[str, object]) -> int:
    value = payload.get("total_matches")
    if not isinstance(value, int):
        raise RuntimeError(f"expected integer total_matches, saw: {value!r}")
    return value


def extract_total_edits(payload: dict[str, object]) -> int:
    if isinstance(payload.get("plan"), dict):
        value = payload["plan"].get("total_edits")
    else:
        value = payload.get("total_edits")
    if not isinstance(value, int):
        raise RuntimeError(f"expected integer total_edits, saw: {value!r}")
    return value


def run_harness_loop_iteration(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    iteration_index: int,
    pattern: str,
    replacement: str,
) -> dict[str, object]:
    search_s, search_payload = run_json_command(
        build_tg_ast_search_cmd(tg_binary=tg_binary, pattern=pattern, corpus_dir=corpus_dir)
    )
    initial_matches = extract_total_matches(search_payload)
    if initial_matches <= 0:
        raise RuntimeError(f"iteration {iteration_index} search found no matches")

    plan_s, plan_payload = run_json_command(
        build_tg_rewrite_plan_cmd(
            tg_binary=tg_binary,
            replacement=replacement,
            pattern=pattern,
            corpus_dir=corpus_dir,
        )
    )
    planned_edits = extract_total_edits(plan_payload)
    if planned_edits <= 0:
        raise RuntimeError(f"iteration {iteration_index} rewrite plan produced no edits")

    apply_s, apply_payload = run_json_command(
        build_tg_rewrite_apply_cmd(
            tg_binary=tg_binary,
            replacement=replacement,
            pattern=pattern,
            corpus_dir=corpus_dir,
        )
    )
    applied_edits = extract_total_edits(apply_payload)
    if applied_edits <= 0:
        raise RuntimeError(f"iteration {iteration_index} apply produced no edits")

    verify_s, verify_payload = run_json_command(
        build_tg_ast_search_cmd(tg_binary=tg_binary, pattern=pattern, corpus_dir=corpus_dir)
    )
    remaining_matches = extract_total_matches(verify_payload)

    return {
        "iteration": iteration_index,
        "search_s": search_s,
        "plan_s": plan_s,
        "apply_s": apply_s,
        "verify_s": verify_s,
        "initial_matches": initial_matches,
        "planned_edits": planned_edits,
        "applied_edits": applied_edits,
        "remaining_matches": remaining_matches,
        "passed": remaining_matches == 0 and applied_edits == planned_edits,
    }


def build_phase_summaries(
    rows: list[dict[str, object]],
) -> tuple[dict[str, float], dict[str, float]]:
    phase_keys = ("search_s", "plan_s", "apply_s", "verify_s")
    medians: dict[str, float] = {}
    totals: dict[str, float] = {}
    for key in phase_keys:
        values = [float(row[key]) for row in rows]
        medians[key] = round(float(statistics.median(values)), 6)
        totals[key] = round(float(sum(values)), 6)
    return medians, totals


def run_harness_loop_benchmark(
    *,
    tg_binary: Path,
    corpus_dir: Path,
    iterations: int,
    pattern: str,
    replacement: str,
) -> dict[str, object]:
    rows: list[dict[str, object]] = []

    for iteration_index in range(1, iterations + 1):
        work_dir = copy_corpus(corpus_dir)
        try:
            rows.append(
                run_harness_loop_iteration(
                    tg_binary=tg_binary,
                    corpus_dir=work_dir,
                    iteration_index=iteration_index,
                    pattern=pattern,
                    replacement=replacement,
                )
            )
        finally:
            shutil.rmtree(work_dir.parent, ignore_errors=True)

    phase_medians_s, phase_totals_s = build_phase_summaries(rows)
    return {
        "iterations": iterations,
        "all_passed": all(bool(row["passed"]) for row in rows),
        "rows": rows,
        "phase_medians_s": phase_medians_s,
        "phase_totals_s": phase_totals_s,
    }


def build_base_payload(args: argparse.Namespace) -> dict[str, object]:
    return {
        "artifact": "bench_harness_loop",
        "suite": "run_harness_loop_benchmark",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "pattern": args.pattern,
        "replacement": args.replacement,
        "iterations": args.iterations,
        "file_count": args.files,
        "total_loc": args.loc,
        "seed": args.seed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the full AST search -> rewrite -> verify harness loop."
    )
    parser.add_argument("--binary", default=str(default_binary_path()))
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument(
        "--iterations", type=int, default=5, help="Number of full harness-loop iterations."
    )
    parser.add_argument("--files", type=int, default=250, help="Synthetic corpus file count.")
    parser.add_argument("--loc", type=int, default=12500, help="Synthetic corpus total LOC.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic corpus seed.")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN)
    parser.add_argument("--replacement", default=DEFAULT_REPLACEMENT)
    return parser.parse_args()


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    payload = build_base_payload(args)
    tg_binary = resolve_tg_binary(args.binary)

    errors: list[str] = []
    if args.iterations < 1:
        errors.append("iterations must be >= 1")
    if args.files < 1:
        errors.append("files must be >= 1")
    if args.loc < args.files:
        errors.append("loc must be >= files so every generated file contains at least one line")
    if not tg_binary.exists():
        errors.append(f"tg binary not found: {tg_binary}")

    if errors:
        payload.update({
            "passed": False,
            "all_passed": False,
            "error": " ".join(errors),
            "rows": [],
        })
        write_json(output_path, payload)
        for error in errors:
            print(error, file=sys.stderr)
        return 2

    try:
        corpus_info = ensure_harness_loop_bench_corpus(
            resolve_harness_loop_bench_dir(),
            file_count=args.files,
            total_loc=args.loc,
            seed=args.seed,
        )
        corpus_dir = Path(corpus_info["corpus_dir"])
        results = run_harness_loop_benchmark(
            tg_binary=tg_binary,
            corpus_dir=corpus_dir,
            iterations=args.iterations,
            pattern=args.pattern,
            replacement=args.replacement,
        )
    except RuntimeError as exc:
        payload.update({"passed": False, "all_passed": False, "error": str(exc), "rows": []})
        write_json(output_path, payload)
        print(str(exc), file=sys.stderr)
        return 2

    payload.update({
        "tg_binary": str(tg_binary),
        "corpus_dir": str(corpus_dir),
        "manifest_path": str(corpus_info["manifest_path"]),
        "file_count": corpus_info["file_count"],
        "total_loc": corpus_info["total_loc"],
        **results,
    })
    if payload.get("rows") and (
        not isinstance(payload.get("phase_medians_s"), dict)
        or not isinstance(payload.get("phase_totals_s"), dict)
    ):
        phase_medians_s, phase_totals_s = build_phase_summaries(payload["rows"])
        payload["phase_medians_s"] = phase_medians_s
        payload["phase_totals_s"] = phase_totals_s
    payload["passed"] = bool(payload["all_passed"])
    write_json(output_path, payload)

    print(f"iterations:      {payload['iterations']}")
    print(f"all passed:      {payload['all_passed']}")
    print(f"search median:   {payload['phase_medians_s']['search_s']:.3f}s")
    print(f"plan median:     {payload['phase_medians_s']['plan_s']:.3f}s")
    print(f"apply median:    {payload['phase_medians_s']['apply_s']:.3f}s")
    print(f"verify median:   {payload['phase_medians_s']['verify_s']:.3f}s")
    print(f"Results written to {output_path}")
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
