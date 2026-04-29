from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_run_repo_retrieval_benchmarks_emits_expected_metrics(tmp_path: Path) -> None:
    output_path = tmp_path / "retrieval.json"
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            str(root / "benchmarks" / "run_repo_retrieval_benchmarks.py"),
            "--dataset",
            str(root / "tests" / "fixtures" / "retrieval" / "sample_eval.jsonl"),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["artifact"] == "bench_repo_retrieval_benchmarks"
    assert payload["suite"] == "run_repo_retrieval_benchmarks"
    assert {
        "recall_at_5",
        "precision_at_5",
        "mrr_at_5",
        "ndcg_at_5",
        "file_f1",
        "line_f1",
        "p50_latency_ms",
        "token_budget_mean",
    } <= set(payload["metrics"])
    assert [row["name"] for row in payload["rows"]] == ["create-invoice", "session-store"]


def test_run_repo_retrieval_benchmarks_uses_committed_default_dataset(tmp_path: Path) -> None:
    output_path = tmp_path / "retrieval-default.json"
    root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            str(root / "benchmarks" / "run_repo_retrieval_benchmarks.py"),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )

    assert result.returncode == 0, result.stderr

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    dataset_path = Path(payload["dataset"])
    assert dataset_path.parts[-3:] == ("benchmarks", "datasets", "repo_retrieval_eval.jsonl")
    assert payload["rows"], "default dataset should keep the benchmark runnable"
