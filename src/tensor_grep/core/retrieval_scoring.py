from __future__ import annotations

import math
from dataclasses import dataclass


def recall_at_k(ranked: list[str], relevant: set[str], *, top_k: int) -> float:
    if not relevant:
        return 1.0
    hits = len(set(ranked[:top_k]) & relevant)
    return hits / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], *, top_k: int) -> float:
    if top_k <= 0:
        return 0.0
    hits = len(set(ranked[:top_k]) & relevant)
    return hits / top_k


def mean_reciprocal_rank_at_k(ranked: list[str], relevant: set[str], *, top_k: int) -> float:
    for index, item in enumerate(ranked[:top_k], start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: list[str], relevant: set[str], *, top_k: int) -> float:
    if top_k <= 0 or not relevant:
        return 1.0 if not relevant else 0.0

    dcg = 0.0
    for index, item in enumerate(ranked[:top_k], start=1):
        if item in relevant:
            dcg += 1.0 / math.log2(index + 1)

    ideal_hits = min(len(relevant), top_k)
    if ideal_hits == 0:
        return 0.0

    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def f1_score(precision: float, recall: float) -> float:
    if precision == 0.0 and recall == 0.0:
        return 0.0
    return (2.0 * precision * recall) / (precision + recall)


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr_at_k: float
    ndcg_at_k: float
    file_f1: float
    line_f1: float

    @classmethod
    def from_ranked_results(
        cls,
        *,
        ranked_items: list[str],
        relevant_items: set[str],
        ranked_line_hits: list[str],
        relevant_line_hits: set[str],
        top_k: int,
    ) -> RetrievalMetrics:
        file_recall = recall_at_k(ranked_items, relevant_items, top_k=top_k)
        file_precision = precision_at_k(ranked_items, relevant_items, top_k=top_k)
        line_recall = recall_at_k(ranked_line_hits, relevant_line_hits, top_k=top_k)
        line_precision = precision_at_k(ranked_line_hits, relevant_line_hits, top_k=top_k)

        return cls(
            recall_at_k=file_recall,
            precision_at_k=file_precision,
            mrr_at_k=mean_reciprocal_rank_at_k(ranked_items, relevant_items, top_k=top_k),
            ndcg_at_k=ndcg_at_k(ranked_items, relevant_items, top_k=top_k),
            file_f1=f1_score(file_precision, file_recall),
            line_f1=f1_score(line_precision, line_recall),
        )
