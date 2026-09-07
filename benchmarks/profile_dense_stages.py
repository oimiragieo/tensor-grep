"""P12 (docs/plans/2026-09-07-agentic-quality-simplification.md Task 12) measurement harness.

Separately times model load, corpus encode, query encode, score, and sort for the CPU
dense-embedding retrieval leg (`tensor_grep.core.retrieval_dense`) -- the per-phase evidence the
prior P12 proposal ("ONNX int8 + AVX-512 for 8x speedup") never had. This script produces NO
speed claim and does NOT implement ONNX/int8; per Task 12's own acceptance bar, "NO BENEFIT is a
valid P12 research result."

Requires the optional `model2vec` extra AND the fetched `minishlab/potion-code-16M` model
(`python -m tensor_grep.core.retrieval_dense --fetch`) -- both are dev-machine-local, not CI
artifacts, so this script exits 0 with a skip message when either is absent rather than failing.

Run: `uv run python benchmarks/profile_dense_stages.py [--queries N] [--corpus-size N]`

Noise-floor discipline (tensor-grep-benchmark-and-proof-toolkit): reports MIN across repeats
(the floor an optimization must beat), not mean -- a mean is inflated by GC pauses and OS
scheduling noise on a shared box; min is the closest single-run proxy for "the code's own cost."
Iteration counts are deliberately small (this dev box is a shared server, not a dedicated bench
rig) -- this is a phase-shape probe, not a publishable latency number.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_dense import (
    DenseIndex,
    default_model_dir,
    dense_available,
    load_dense_model,
)

_SYNTHETIC_TEXTS = [
    "def authenticate_user(username, password): return check_credentials(username, password)",
    "class ConnectionPool: def close_connection(self, conn): conn.close()",
    "def parse_config(path): return json.loads(Path(path).read_text())",
    "async function fetchUserProfile(id) { return await api.get(`/users/${id}`); }",
    "func ValidateToken(token string) (bool, error) { return jwt.Verify(token) }",
    "SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC",
    "def compute_checksum(data: bytes) -> str: return hashlib.sha256(data).hexdigest()",
    "class RateLimiter: def allow_request(self, key): return self.bucket.consume(key)",
]

_SAMPLE_QUERIES = [
    "verify login credentials",
    "tear down a network connection",
    "load application settings from disk",
]


def _synthetic_corpus(size: int) -> list[Chunk]:
    return [
        Chunk(
            file_path=f"synthetic_{i}.py",
            start_line=1,
            end_line=1,
            text=_SYNTHETIC_TEXTS[i % len(_SYNTHETIC_TEXTS)],
        )
        for i in range(size)
    ]


def _min_of(fn, repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return min(samples)


def profile(*, corpus_size: int, queries: int, repeats: int) -> dict[str, object] | None:
    available, reason = dense_available()
    if not available:
        print(f"SKIP: dense leg unavailable ({reason})", file=sys.stderr)
        return None

    model_dir = default_model_dir()
    if not model_dir.is_dir():
        print(f"SKIP: model not fetched at {model_dir}", file=sys.stderr)
        return None

    load_s = _min_of(lambda: load_dense_model(model_dir), repeats=3)
    model = load_dense_model(model_dir)

    corpus = _synthetic_corpus(corpus_size)
    encode_start = time.perf_counter()
    index = DenseIndex(corpus, model)
    corpus_encode_s = time.perf_counter() - encode_start

    per_query_samples: list[dict[str, float]] = []
    for i in range(queries):
        query_text = _SAMPLE_QUERIES[i % len(_SAMPLE_QUERIES)]
        _ranked, timings = index.query_with_timings(query_text, top_k=10)
        per_query_samples.append({
            "query_encode_s": timings.query_encode_s or 0.0,
            "score_s": timings.score_s or 0.0,
            "sort_s": timings.sort_s or 0.0,
        })

    query_encode_vals = [s["query_encode_s"] for s in per_query_samples]
    score_vals = [s["score_s"] for s in per_query_samples]
    sort_vals = [s["sort_s"] for s in per_query_samples]

    return {
        "corpus_size": corpus_size,
        "query_count": queries,
        "model_load_s": {"min": load_s},
        "corpus_encode_s": {"min": corpus_encode_s},
        "query_encode_s": {
            "min": min(query_encode_vals),
            "median": statistics.median(query_encode_vals),
        },
        "score_s": {"min": min(score_vals), "median": statistics.median(score_vals)},
        "sort_s": {"min": min(sort_vals), "median": statistics.median(sort_vals)},
        "note": "min-of-N floor per tensor-grep-benchmark-and-proof-toolkit; NOT a publishable "
        "latency claim -- see module docstring. Confirms/refutes NOTHING about ONNX/int8 by "
        "itself; that comparison is Task 12's later checkbox, deliberately not done here.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-size", type=int, default=64)
    parser.add_argument("--queries", type=int, default=5)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    result = profile(corpus_size=args.corpus_size, queries=args.queries, repeats=args.repeats)
    if result is None:
        return 0  # skip, not failure -- see module docstring
    text = json.dumps(result, indent=2)
    print(text)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
