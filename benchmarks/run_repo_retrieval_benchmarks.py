from __future__ import annotations

import argparse
import json
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


def default_output_path() -> Path:
    return ROOT_DIR / "artifacts" / "bench_repo_retrieval_benchmarks.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit a deterministic repository retrieval benchmark artifact."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default=str(default_output_path()))
    parser.add_argument("--top-k", type=int, default=5)
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


def benchmark_row(sample: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    from tensor_grep.core.retrieval_scoring import RetrievalMetrics

    ranked_paths = [str(item) for item in sample.get("ranked_paths", [])]
    relevant_paths = {str(item) for item in sample.get("relevant_paths", [])}
    ranked_line_hits = [str(item) for item in sample.get("ranked_line_hits", [])]
    relevant_line_hits = {str(item) for item in sample.get("relevant_line_hits", [])}
    metrics = RetrievalMetrics.from_ranked_results(
        ranked_items=ranked_paths,
        relevant_items=relevant_paths,
        ranked_line_hits=ranked_line_hits,
        relevant_line_hits=relevant_line_hits,
        top_k=top_k,
    )

    return {
        "name": str(sample.get("name", "unknown")),
        "query": str(sample.get("query", "")),
        "top_k": top_k,
        "recall_at_k": round(metrics.recall_at_k, 6),
        "precision_at_k": round(metrics.precision_at_k, 6),
        "mrr_at_k": round(metrics.mrr_at_k, 6),
        "ndcg_at_k": round(metrics.ndcg_at_k, 6),
        "file_f1": round(metrics.file_f1, 6),
        "line_f1": round(metrics.line_f1, 6),
        "latency_ms": float(sample.get("latency_ms", 0.0)),
        "token_estimate": int(sample.get("token_estimate", 0)),
    }


def main() -> int:
    from tensor_grep.perf_guard import write_json

    args = parse_args()
    dataset_path = Path(args.dataset).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    samples = _load_jsonl(dataset_path)
    rows = [benchmark_row(sample, top_k=args.top_k) for sample in samples]

    metric_suffix = f"_at_{args.top_k}"
    payload = {
        "artifact": "bench_repo_retrieval_benchmarks",
        "suite": "run_repo_retrieval_benchmarks",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "dataset": str(dataset_path),
        "top_k": args.top_k,
        "metrics": {
            f"recall{metric_suffix}": round(
                float(statistics.mean(row["recall_at_k"] for row in rows)), 6
            ),
            f"precision{metric_suffix}": round(
                float(statistics.mean(row["precision_at_k"] for row in rows)), 6
            ),
            f"mrr{metric_suffix}": round(
                float(statistics.mean(row["mrr_at_k"] for row in rows)), 6
            ),
            f"ndcg{metric_suffix}": round(
                float(statistics.mean(row["ndcg_at_k"] for row in rows)), 6
            ),
            "file_f1": round(float(statistics.mean(row["file_f1"] for row in rows)), 6),
            "line_f1": round(float(statistics.mean(row["line_f1"] for row in rows)), 6),
            "p50_latency_ms": round(float(statistics.median(row["latency_ms"] for row in rows)), 6),
            "token_budget_mean": round(
                float(statistics.mean(row["token_estimate"] for row in rows)), 6
            ),
        },
        "rows": rows,
    }
    write_json(output_path, payload)

    for row in rows:
        print(
            f"{row['name']}: recall={row['recall_at_k']:.3f} "
            f"precision={row['precision_at_k']:.3f} mrr={row['mrr_at_k']:.3f}"
        )
    print(f"Results written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
