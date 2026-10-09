"""Paired opt-in cross-encoder evaluation over the committed retrieval corpora.

Run with explicitly installed assets. No downloads occur here. The baseline is
the actual find candidate pipeline, including its disclosed optional dense state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tensor_grep import rust_core
from tensor_grep.cli.main import _execute_find
from tensor_grep.core.cross_encoder_assets import default_asset_dir, verified_assets
from tensor_grep.core.retrieval_scoring import ndcg_at_k, recall_at_k


def evaluate(corpus: Path, golden: Path) -> dict[str, Any]:
    model, _, _ = verified_assets(default_asset_dir())
    rows = []
    for line in golden.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        relevant = {entry["file"] for entry in item["relevant"]}
        if not relevant:
            raise ValueError("golden query has no relevant files")
        baseline = _execute_find(
            item["query"],
            str(corpus),
            limit=20,
            max_repo_files=1000,
            max_tokens=0,
            deadline=60,
            grounding="off",
            rerank="off",
        )
        if baseline.result_incomplete:
            raise ValueError(f"incomplete baseline for {item['id']}")
        native_scorer = rust_core.cross_encoder_scores
        model_scores: list[float] = []

        def capture_scores(
            *args: Any, _native_scorer: Any = native_scorer, _scores: list[float] = model_scores
        ) -> list[float]:
            scores: list[float] = _native_scorer(*args)
            _scores.extend(scores)
            return scores

        try:
            rust_core.cross_encoder_scores = capture_scores
            ranked = _execute_find(
                item["query"],
                str(corpus),
                limit=20,
                max_repo_files=1000,
                max_tokens=0,
                deadline=60,
                grounding="off",
                rerank="cross-encoder",
            )
        finally:
            rust_core.cross_encoder_scores = native_scorer

        def files(result: Any) -> list[str]:
            # First occurrence per file; oracle labels are file-level evidence.
            return list(
                dict.fromkeys(
                    Path(row.file).relative_to(corpus).as_posix() for row in result.matches
                )
            )

        before, after = files(baseline), files(ranked)
        if set(before) != set(after):
            raise ValueError("cross-encoder changed candidate membership")
        rows.append({
            "id": item["id"],
            "before": before,
            "after": after,
            "baseline_ndcg10": ndcg_at_k(before, relevant, top_k=10),
            "reranked_ndcg10": ndcg_at_k(after, relevant, top_k=10),
            "baseline_recall10": recall_at_k(before, relevant, top_k=10),
            "reranked_recall10": recall_at_k(after, relevant, top_k=10),
            "baseline_fallback": baseline.rank_fallback_reason,
            "candidate_files": [
                Path(row.file).relative_to(corpus).as_posix() for row in baseline.matches
            ],
            "model_scores": model_scores,
        })
    if not rows:
        raise ValueError("empty golden set")
    delta = sum(row["reranked_ndcg10"] - row["baseline_ndcg10"] for row in rows) / len(rows)
    with model.open("rb") as stream:
        model_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "corpus": str(corpus),
        "golden": str(golden),
        "model_sha256": model_digest,
        "queries": len(rows),
        "paired_mean_ndcg10_delta": delta,
        "regressed_queries": [
            row["id"] for row in rows if row["reranked_ndcg10"] < row["baseline_ndcg10"]
        ],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent / "datasets"
    parser.add_argument("--corpus", type=Path, default=base / "find_golden_corpus")
    parser.add_argument("--golden", type=Path, default=base / "late_rerank_golden.jsonl")
    parser.add_argument("--require-no-regression", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.corpus.resolve(), args.golden.resolve())
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return int(args.require_no_regression and bool(report["regressed_queries"]))


if __name__ == "__main__":
    raise SystemExit(main())
