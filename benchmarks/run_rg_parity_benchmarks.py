from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT_DIR / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers.rg_parity import (  # noqa: E402
    build_case_commands,
    build_command_env,
    build_rg_parity_cases,
    create_rg_parity_corpus,
    normalize_output,
    normalize_stderr,
    resolve_pinned_rg_binary,
    run_parity_case,
)

from tensor_grep.cli.rg_contract import RG_CONTRACT_ROWS  # noqa: E402

TIMING_SAMPLES_PER_CASE = 3
WARMUP_RUNS = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark tg against pinned rg across the validated rg parity contract."
    )
    parser.add_argument(
        "--output",
        default=str(ROOT_DIR / "artifacts" / "bench_run_rg_parity_benchmarks.json"),
        help="Path to the JSON benchmark artifact.",
    )
    return parser.parse_args()


def benchmarkable_cases():
    return tuple(
        case for case in build_rg_parity_cases(RG_CONTRACT_ROWS) if case.row["benchmarkable"]
    )


def _run_timed_command(argv: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> float:
    started = time.perf_counter()
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode not in {0, 1}:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {' '.join(argv)}"
        )
    return elapsed


def _timing_status(*, rg_seconds: float, tg_seconds: float) -> str:
    if rg_seconds <= 0:
        return "parity"

    ratio = tg_seconds / rg_seconds
    if 0.95 <= ratio <= 1.05:
        return "parity"
    if ratio < 1.0:
        return "improvement"
    return "regression"


def run_contract_benchmark(case, *, corpus, rg_binary: Path) -> dict[str, Any]:
    env = build_command_env(rg_binary)
    rg_argv, tg_argv = build_case_commands(case=case, corpus=corpus, rg_binary=rg_binary)

    for _ in range(WARMUP_RUNS):
        _run_timed_command(rg_argv, cwd=corpus.root, env=env)
        _run_timed_command(tg_argv, cwd=corpus.root, env=env)

    rg_samples = [
        _run_timed_command(rg_argv, cwd=corpus.root, env=env)
        for _ in range(TIMING_SAMPLES_PER_CASE)
    ]
    tg_samples = [
        _run_timed_command(tg_argv, cwd=corpus.root, env=env)
        for _ in range(TIMING_SAMPLES_PER_CASE)
    ]

    parity_result = run_parity_case(case=case, corpus=corpus, rg_binary=rg_binary)
    semantic_parity = (
        parity_result.tg.returncode == parity_result.rg.returncode
        and normalize_stderr(parity_result.tg.stderr, corpus=corpus)
        == normalize_stderr(parity_result.rg.stderr, corpus=corpus)
        and normalize_output(parity_result.tg.stdout, case=case, tool="tg", corpus=corpus)
        == normalize_output(parity_result.rg.stdout, case=case, tool="rg", corpus=corpus)
    )

    rg_seconds = statistics.median(rg_samples)
    tg_seconds = statistics.median(tg_samples)

    return {
        "id": case.row["id"],
        "rg_command": list(rg_argv),
        "tg_command": list(tg_argv),
        "rg_samples_s": rg_samples,
        "tg_samples_s": tg_samples,
        "rg_seconds": rg_seconds,
        "tg_seconds": tg_seconds,
        "ratio_vs_rg": (tg_seconds / rg_seconds) if rg_seconds else None,
        "semantic_parity": semantic_parity,
        "status": "mismatch"
        if not semantic_parity
        else _timing_status(rg_seconds=rg_seconds, tg_seconds=tg_seconds),
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rg_binary = resolve_pinned_rg_binary()
    if rg_binary is None:
        raise SystemExit("ripgrep binary not found for rg contract benchmark")

    corpus = create_rg_parity_corpus(ROOT_DIR / "artifacts" / "rg_parity_corpus")
    rows = [
        run_contract_benchmark(case, corpus=corpus, rg_binary=rg_binary)
        for case in benchmarkable_cases()
    ]

    payload = {
        "artifact": "bench_run_rg_parity_benchmarks",
        "suite": "run_rg_parity_benchmarks",
        "generated_at_epoch_s": time.time(),
        "timing_samples_per_case": TIMING_SAMPLES_PER_CASE,
        "warmup_runs": WARMUP_RUNS,
        "rg_binary": str(rg_binary),
        "cases": rows,
        "semantic_failures": sum(1 for row in rows if not row["semantic_parity"]),
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("RG parity benchmark summary")
    for row in rows:
        print(
            f"{row['id']:<20} rg={row['rg_seconds']:.4f}s tg={row['tg_seconds']:.4f}s "
            f"status={row['status']} semantic_parity={row['semantic_parity']}"
        )
    print(f"Wrote {output_path}")
    return 0 if payload["semantic_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
