from __future__ import annotations

import pytest

from tensor_grep.core.retrieval_scoring import (
    RetrievalMetrics,
    f1_score,
    mean_reciprocal_rank_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_ranked_retrieval_metrics_capture_recall_precision_mrr_and_ndcg() -> None:
    ranked = ["alpha.py", "beta.py", "gamma.py", "delta.py"]
    relevant = {"beta.py", "delta.py"}

    assert recall_at_k(ranked, relevant, top_k=3) == pytest.approx(0.5)
    assert precision_at_k(ranked, relevant, top_k=3) == pytest.approx(1.0 / 3.0)
    assert mean_reciprocal_rank_at_k(ranked, relevant, top_k=3) == pytest.approx(0.5)
    assert ndcg_at_k(ranked, relevant, top_k=3) == pytest.approx(0.386853, rel=1e-6)


def test_retrieval_metrics_bundle_exposes_file_and_line_quality() -> None:
    metrics = RetrievalMetrics.from_ranked_results(
        ranked_items=["a.py", "b.py", "c.py"],
        relevant_items={"a.py", "c.py"},
        ranked_line_hits=["a.py:1-4", "b.py:5-8", "c.py:9-12"],
        relevant_line_hits={"c.py:9-12"},
        top_k=3,
    )

    assert metrics.recall_at_k == pytest.approx(1.0)
    assert metrics.precision_at_k == pytest.approx(2.0 / 3.0)
    assert metrics.mrr_at_k == pytest.approx(1.0)
    assert metrics.ndcg_at_k == pytest.approx(0.919721, rel=1e-6)
    assert metrics.file_f1 == pytest.approx(0.8)
    assert metrics.line_f1 == pytest.approx(0.5)


def test_f1_score_handles_empty_precision_and_recall() -> None:
    assert f1_score(0.0, 0.0) == 0.0
