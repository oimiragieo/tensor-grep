"""BM25 retrieval-quality benchmark + the v2 (dense-embedding) gate.

Establishes the BM25 baseline for hybrid semantic search on a small, offline, keyword-discriminating
synthetic corpus, and is the harness the v2 dense+RRF leg must beat before it ships user-visible.

Run:
    python benchmarks/eval_bm25_quality.py [--top-k 3]
Exit 0 if recall@k >= V2_GATE_RECALL, else 1.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import chunk_file
from tensor_grep.core.retrieval_scoring import (
    RetrievalMetrics,
    f1_score,
    mean_reciprocal_rank_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

V2_GATE_RECALL: float = 0.60

# A small topic-discriminating corpus: each file owns a vocabulary cluster a query should retrieve.
_CORPUS: dict[str, str] = {
    "invoice.py": "def create_invoice(amount):\n    invoice = {'total': amount}\n    return invoice\n",
    "auth.py": "def authenticate(user, token):\n    return verify_token(user, token)\n",
    "parser.py": "def parse_ast(source):\n    tree = build_syntax_tree(source)\n    return tree\n",
    "http_client.py": "def fetch_url(url):\n    response = http_request(url)\n    return response.body\n",
    "cache.py": "def cache_get(key):\n    return lru_cache_lookup(key)\n",
    "config.py": "def load_config(path):\n    settings = read_toml(path)\n    return settings\n",
    "logger.py": "def log_error(message):\n    write_log_line('ERROR', message)\n",
    "database.py": "def query_rows(sql):\n    cursor = db_connection.execute(sql)\n    return cursor.fetchall()\n",
    "renderer.py": "def render_template(name, context):\n    return template_engine.render(name, context)\n",
    "queue.py": "def enqueue_job(job):\n    task_queue.push(job)\n    return job.id\n",
}

_QUERIES: list[EvalQuery] = []  # populated after EvalQuery is defined


@dataclass(frozen=True)
class EvalQuery:
    query: str
    relevant_files: set[str]  # basenames expected in the top-k


_QUERIES = [
    EvalQuery("create invoice total amount", {"invoice.py"}),
    EvalQuery("authenticate user token", {"auth.py"}),
    EvalQuery("parse ast syntax tree", {"parser.py"}),
    EvalQuery("fetch url http request response", {"http_client.py"}),
    EvalQuery("cache lookup key", {"cache.py"}),
    EvalQuery("load config settings toml", {"config.py"}),
    EvalQuery("log error message", {"logger.py"}),
    EvalQuery("query database rows sql", {"database.py"}),
    EvalQuery("render template context", {"renderer.py"}),
    EvalQuery("enqueue job task queue", {"queue.py"}),
]


def build_default_corpus(dest: Path) -> list[EvalQuery]:
    """Write the synthetic corpus into ``dest`` and return its labelled queries."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, body in _CORPUS.items():
        (dest / name).write_text(body, encoding="utf-8")
    return list(_QUERIES)


def _ranked_basenames(index: Bm25Index, query: str) -> list[str]:
    ranked = index.query(query, top_k=max(1, len(index.chunks)))
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk_idx, _score in ranked:
        base = Path(index.chunks[chunk_idx].file_path).name
        if base not in seen:
            seen.add(base)
            ordered.append(base)
    return ordered


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def run_eval(corpus_dir: Path, queries: list[EvalQuery], *, top_k: int = 10) -> RetrievalMetrics:
    """Build a BM25 index over ``corpus_dir`` and average per-query metrics across ``queries``."""
    files = sorted(str(p) for p in Path(corpus_dir).rglob("*.py"))
    chunks = []
    for path in files:
        chunks.extend(chunk_file(path))
    index = Bm25Index(chunks)

    recalls, precisions, mrrs, ndcgs, file_f1s = [], [], [], [], []
    for query in queries:
        ranked = _ranked_basenames(index, query.query)
        rec = recall_at_k(ranked, query.relevant_files, top_k=top_k)
        prec = precision_at_k(ranked, query.relevant_files, top_k=top_k)
        recalls.append(rec)
        precisions.append(prec)
        mrrs.append(mean_reciprocal_rank_at_k(ranked, query.relevant_files, top_k=top_k))
        ndcgs.append(ndcg_at_k(ranked, query.relevant_files, top_k=top_k))
        file_f1s.append(f1_score(prec, rec))

    f1_mean = _mean(file_f1s)
    return RetrievalMetrics(
        recall_at_k=_mean(recalls),
        precision_at_k=_mean(precisions),
        mrr_at_k=_mean(mrrs),
        ndcg_at_k=_mean(ndcgs),
        file_f1=f1_mean,
        line_f1=f1_mean,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BM25 retrieval-quality benchmark + v2 gate")
    parser.add_argument("--top-k", type=int, default=3, help="rank cutoff for the metrics")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        corpus = Path(tmp)
        queries = build_default_corpus(corpus)
        metrics = run_eval(corpus, queries, top_k=args.top_k)

    print(f"BM25 baseline (top_k={args.top_k}, n={len(queries)} queries):")
    print(f"  recall@k   = {metrics.recall_at_k:.3f}")
    print(f"  precision  = {metrics.precision_at_k:.3f}")
    print(f"  mrr@k      = {metrics.mrr_at_k:.3f}")
    print(f"  ndcg@k     = {metrics.ndcg_at_k:.3f}")
    passed = metrics.recall_at_k >= V2_GATE_RECALL
    print(f"v2 gate (recall@k >= {V2_GATE_RECALL}): {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
