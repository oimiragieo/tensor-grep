"""Dense-leg compression quality/latency/footprint benchmark (tensor-grep-semantic-search-campaign,
dense-leg compression wave).

Compares the fp32 dense-leg baseline against every compression variant from
``core/retrieval_dense.py``'s ``DenseCompressionConfig`` (int8, binary+int8-rescore,
truncate-{128,96,64}, and the truncate+int8 combo) on the SAME realistic, vocabulary-mismatch
golden corpus + queries already used to gate the late-rerank promotion decision
(``benchmarks/datasets/find_golden_corpus/`` + ``benchmarks/datasets/late_rerank_golden.jsonl``,
74 files / 40 queries across 4 categories). That corpus was purpose-built so BM25 scores near the
floor on it (see ``eval_late_rerank_quality.py``'s E2 corpus-hardness gate) -- i.e. it is a REAL
discriminator of dense-leg quality, unlike the toy ``eval_bm25_quality.py`` corpus which saturates
BM25 at recall 1.0 and would prove nothing about a compression regression (campaign skill GATE 0b).

For EACH variant, reports:

- quality: recall@5, recall@10, ndcg@5, ndcg@10, mrr -- via ``retrieval_scoring.py``'s exact
  functions (no reimplemented metric), scored identically across >=2 repeated runs to PROVE
  determinism (a mismatch is a loud failure, not a warning).
- latency: mean per-query ``DenseIndex.query`` wall-clock time (ms) for EACH repeat separately
  (so the noise floor between runs is visible, never collapsed into a single number), plus index
  BUILD time.
- footprint: the REAL measured ``DenseIndex.index_nbytes`` (not an assumed theoretical
  multiplier).

An OPTIONAL ``--extra-corpus`` (e.g. this repo's own ``src/tensor_grep`` tree) additionally
measures latency + footprint scaling at a much larger N with no golden labels required (quality is
never claimed for that corpus -- only latency/footprint rows are emitted for it).

Usage (real fetched potion-code-16M model + the `semantic` extra required; a loud, specific
`EvalError` on either being absent -- never a silent empty comparison):

    uv run --no-sync python benchmarks/eval_dense_compression_quality.py
    uv run --no-sync python benchmarks/eval_dense_compression_quality.py --repeats 3
    uv run --no-sync python benchmarks/eval_dense_compression_quality.py --extra-corpus src/tensor_grep/core

Exit 0 when the comparison completes (regardless of which variant wins -- this is a measurement
harness, not a gate; the promotion decision is made by a human reading the table per
tensor-grep-semantic-search-campaign Phase 5). Exit 1 on a loud config/data error or an
unavailable dense leg.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent / "datasets" / "find_golden_corpus"
DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parent / "datasets" / "late_rerank_golden.jsonl"
DEFAULT_OUTPUT_PATH = ROOT_DIR / "artifacts" / "bench_dense_compression_quality.json"

DEFAULT_TOP_KS: tuple[int, ...] = (5, 10)
DEFAULT_REPEATS = 2
_METRIC_NAMES: tuple[str, ...] = ("recall@5", "recall@10", "ndcg@5", "ndcg@10", "mrr")


class EvalError(ValueError):
    """A loud, non-silent failure -- malformed golden set, missing corpus, or an unavailable dense
    leg. Never caught and downgraded to a warning: this harness refuses to fabricate a comparison
    (mirrors ``eval_late_rerank_quality.py``'s ``GoldenSetError`` discipline)."""


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    query: str
    category: str
    relevant_files: frozenset[str]


# ---------------------------------------------------------------------------------------
# Loading (self-contained -- deliberately does not import eval_late_rerank_quality.py, to keep
# this script's ownership boundary independent of that campaign's harness).
# ---------------------------------------------------------------------------------------


def load_golden_queries(path: Path) -> list[GoldenQuery]:
    if not path.is_file():
        raise EvalError(f"golden query file not found: {path}")

    queries: list[GoldenQuery] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalError(f"{path}:{line_number}: invalid JSON: {exc}") from exc

        query_id = row.get("id")
        if not query_id:
            raise EvalError(f"{path}:{line_number}: missing required field 'id'")
        if query_id in seen_ids:
            raise EvalError(f"{path}:{line_number}: duplicate query id {query_id!r}")
        seen_ids.add(query_id)

        query_text = row.get("query")
        if not query_text:
            raise EvalError(f"{path}:{line_number} ({query_id}): missing required field 'query'")

        category = row.get("category")
        if not category:
            raise EvalError(f"{path}:{line_number} ({query_id}): missing required field 'category'")

        relevant_entries = row.get("relevant")
        if not relevant_entries:
            # E1 guard (mirrors eval_late_rerank_quality.py): an empty `relevant` set would let
            # recall_at_k/ndcg_at_k's vacuous-truth branch silently score this query as a PERFECT
            # 1.0 for every variant, forever, with no visible signal anything is wrong.
            raise EvalError(
                f"{path}:{line_number} ({query_id}): 'relevant' must be a non-empty list"
            )

        relevant_files = frozenset(entry["file"] for entry in relevant_entries)
        queries.append(
            GoldenQuery(
                id=query_id, query=query_text, category=category, relevant_files=relevant_files
            )
        )

    if not queries:
        raise EvalError(f"{path}: contained zero golden queries")
    return queries


def load_corpus_files(corpus_dir: Path) -> list[str]:
    if not corpus_dir.is_dir():
        raise EvalError(f"corpus directory not found: {corpus_dir}")
    files = sorted(str(p) for p in corpus_dir.rglob("*") if p.is_file())
    if not files:
        raise EvalError(f"corpus directory is empty: {corpus_dir}")
    return files


def _relative_posix(path_str: str, corpus_dir: Path) -> str:
    return Path(path_str).resolve().relative_to(corpus_dir.resolve()).as_posix()


def validate_golden_against_corpus(queries: list[GoldenQuery], corpus_dir: Path) -> None:
    corpus_root = corpus_dir.resolve()
    for query in queries:
        for relevant_file in query.relevant_files:
            if not (corpus_root / relevant_file).is_file():
                raise EvalError(
                    f"{query.id}: relevant file {relevant_file!r} does not exist under {corpus_dir}"
                )


def _dedupe_ranked_files(
    ranked_chunk_indices: list[int], chunks: list[Any], corpus_dir: Path
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk_index in ranked_chunk_indices:
        rel = _relative_posix(chunks[chunk_index].file_path, corpus_dir)
        if rel not in seen:
            seen.add(rel)
            ordered.append(rel)
    return ordered


def _score_ranking(
    ranked_files: list[str], relevant_files: frozenset[str], top_ks: tuple[int, ...]
) -> dict[str, float]:
    from tensor_grep.core.retrieval_scoring import (
        mean_reciprocal_rank_at_k,
        ndcg_at_k,
        recall_at_k,
    )

    metrics: dict[str, float] = {}
    for k in top_ks:
        metrics[f"recall@{k}"] = recall_at_k(ranked_files, relevant_files, top_k=k)
        metrics[f"ndcg@{k}"] = ndcg_at_k(ranked_files, relevant_files, top_k=k)
    metrics["mrr"] = mean_reciprocal_rank_at_k(ranked_files, relevant_files, top_k=max(top_ks))
    return metrics


# ---------------------------------------------------------------------------------------
# Variants under comparison.
# ---------------------------------------------------------------------------------------


def build_variants(rescore_candidates: int) -> dict[str, Any]:
    from tensor_grep.core.retrieval_dense import DenseCompressionConfig, DenseQuantizationMode

    return {
        "fp32_baseline": DenseCompressionConfig(),
        "int8": DenseCompressionConfig(quantization=DenseQuantizationMode.INT8),
        f"binary_rescore_k{rescore_candidates}": DenseCompressionConfig(
            quantization=DenseQuantizationMode.BINARY_RESCORE,
            rescore_candidates=rescore_candidates,
        ),
        "truncate_128": DenseCompressionConfig(truncate_dims=128),
        "truncate_96": DenseCompressionConfig(truncate_dims=96),
        "truncate_64": DenseCompressionConfig(truncate_dims=64),
        "truncate_128_int8": DenseCompressionConfig(
            quantization=DenseQuantizationMode.INT8, truncate_dims=128
        ),
    }


def build_dense_model() -> Any:
    """Real production probes (mirrors ``eval_late_rerank_quality.py::build_dense_index``): a
    RECOVERABLE unavailability (extra not installed / model not fetched) raises the loud
    :class:`EvalError` this harness uses everywhere; a genuine
    :class:`~tensor_grep.backends.base.BackendExecutionError` is NOT caught here and propagates
    per the Backend Fail-Closed Contract."""
    from tensor_grep.core.retrieval_dense import (
        DenseUnavailableError,
        default_model_dir,
        dense_available,
        load_dense_model,
    )

    available, reason = dense_available()
    if not available:
        raise EvalError(f"dense leg unavailable: {reason}")
    try:
        return load_dense_model(default_model_dir())
    except DenseUnavailableError as exc:
        raise EvalError(f"dense leg unavailable: {exc}") from exc


# ---------------------------------------------------------------------------------------
# Measurement.
# ---------------------------------------------------------------------------------------


@dataclass
class VariantResult:
    name: str
    dim: int
    index_nbytes: int
    index_build_ms: float
    per_query_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    latency_ms_by_run: list[list[float]] = field(default_factory=list)
    determinism_ok: bool = True

    def mean_metric(self, metric: str) -> float:
        values = [row[metric] for row in self.per_query_metrics.values()]
        return statistics.mean(values) if values else 0.0

    def mean_latency_ms(self, run_index: int) -> float:
        return statistics.mean(self.latency_ms_by_run[run_index])


def run_variant(
    name: str,
    config: Any,
    chunks: list[Any],
    corpus_dir: Path,
    queries: list[GoldenQuery],
    top_ks: tuple[int, ...],
    model: Any,
    *,
    repeats: int,
) -> VariantResult:
    from tensor_grep.core.retrieval_dense import DenseIndex

    build_start = time.perf_counter()
    index = DenseIndex(chunks, model, compression=config)
    build_ms = (time.perf_counter() - build_start) * 1000.0

    total = max(1, len(chunks))
    per_query_runs: list[dict[str, dict[str, float]]] = []
    latency_runs: list[list[float]] = []

    for _run in range(repeats):
        per_query: dict[str, dict[str, float]] = {}
        latencies: list[float] = []
        for query in queries:
            t0 = time.perf_counter()
            ranked_idx = [chunk_idx for chunk_idx, _score in index.query(query.query, top_k=total)]
            latencies.append((time.perf_counter() - t0) * 1000.0)
            ranked_files = _dedupe_ranked_files(ranked_idx, chunks, corpus_dir)
            per_query[query.id] = _score_ranking(ranked_files, query.relevant_files, top_ks)
        per_query_runs.append(per_query)
        latency_runs.append(latencies)

    determinism_ok = all(run == per_query_runs[0] for run in per_query_runs[1:])

    return VariantResult(
        name=name,
        dim=index.dim,
        index_nbytes=index.index_nbytes,
        index_build_ms=build_ms,
        per_query_metrics=per_query_runs[0],
        latency_ms_by_run=latency_runs,
        determinism_ok=determinism_ok,
    )


@dataclass
class ExtraCorpusResult:
    name: str
    dim: int
    index_nbytes: int
    index_build_ms: float
    mean_query_latency_ms_by_run: list[float]


def run_extra_corpus_variant(
    name: str,
    config: Any,
    chunks: list[Any],
    probe_queries: list[str],
    model: Any,
    *,
    repeats: int,
) -> ExtraCorpusResult:
    """Latency + footprint ONLY (no golden labels) -- used for the optional ``--extra-corpus``
    scaling check at a much larger N than the 74-file golden corpus."""
    from tensor_grep.core.retrieval_dense import DenseIndex

    build_start = time.perf_counter()
    index = DenseIndex(chunks, model, compression=config)
    build_ms = (time.perf_counter() - build_start) * 1000.0

    total = max(1, len(chunks))
    means: list[float] = []
    for _run in range(repeats):
        latencies: list[float] = []
        for query_text in probe_queries:
            t0 = time.perf_counter()
            index.query(query_text, top_k=total)
            latencies.append((time.perf_counter() - t0) * 1000.0)
        means.append(statistics.mean(latencies))

    return ExtraCorpusResult(
        name=name,
        dim=index.dim,
        index_nbytes=index.index_nbytes,
        index_build_ms=build_ms,
        mean_query_latency_ms_by_run=means,
    )


# ---------------------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------------------


def render_report(
    results: dict[str, VariantResult],
    top_ks: tuple[int, ...],
    corpus_dir: Path,
    file_count: int,
    chunk_count: int,
    query_count: int,
    repeats: int,
    extra: dict[str, ExtraCorpusResult] | None,
    extra_corpus_dir: Path | None,
) -> str:
    lines: list[str] = []
    lines.append("tg dense-leg compression benchmark")
    lines.append(f"corpus: {corpus_dir} ({file_count} files, {chunk_count} chunks)")
    lines.append(f"queries: {query_count} (vocabulary-mismatch golden set)")
    lines.append(f"repeats: {repeats}")
    lines.append("")

    lines.append("QUALITY (mean over all queries; IDENTICAL across all repeats == deterministic)")
    header = (
        "  variant                " + "  ".join(f"{m:>10}" for m in _METRIC_NAMES) + "  determinism"
    )
    lines.append(header)
    for name, result in results.items():
        row = "  " + f"{name:22} "
        row += "  ".join(f"{result.mean_metric(m):10.4f}" for m in _METRIC_NAMES)
        row += f"  {'OK' if result.determinism_ok else 'NON-DETERMINISTIC!'}"
        lines.append(row)
    lines.append("")

    lines.append(
        "LATENCY (mean per-query DenseIndex.query wall-clock, ms -- one column per repeat)"
    )
    repeat_header = "  ".join(f"run{i + 1:>7}" for i in range(repeats))
    lines.append(f"  variant                {repeat_header}      mean   build_ms")
    for name, result in results.items():
        per_run = [result.mean_latency_ms(i) for i in range(repeats)]
        row = "  " + f"{name:22} "
        row += "  ".join(f"{v:10.4f}" for v in per_run)
        row += f"  {statistics.mean(per_run):8.4f}  {result.index_build_ms:9.2f}"
        lines.append(row)
    lines.append("")

    lines.append(
        "FOOTPRINT (DenseIndex.index_nbytes, real measured; dim = active scoring dimensionality)"
    )
    baseline_nbytes = results["fp32_baseline"].index_nbytes
    for name, result in results.items():
        ratio = baseline_nbytes / result.index_nbytes if result.index_nbytes else float("inf")
        lines.append(
            f"  {name:22} dim={result.dim:4d}  bytes={result.index_nbytes:10d}  "
            f"({ratio:6.2f}x smaller than fp32_baseline)"
        )
    lines.append("")

    lines.append(
        "VERDICT (ndcg@10 delta vs fp32_baseline; quality gate is recall@k/ndcg@k >= baseline"
    )
    lines.append("         within noise floor AND a real latency/footprint win)")
    baseline_ndcg10 = results["fp32_baseline"].mean_metric("ndcg@10")
    baseline_recall10 = results["fp32_baseline"].mean_metric("recall@10")
    for name, result in results.items():
        if name == "fp32_baseline":
            continue
        ndcg_delta = result.mean_metric("ndcg@10") - baseline_ndcg10
        recall_delta = result.mean_metric("recall@10") - baseline_recall10
        lines.append(
            f"  {name:22} ndcg@10 delta={ndcg_delta:+.4f}  recall@10 delta={recall_delta:+.4f}"
        )
    lines.append("")

    if extra is not None and extra_corpus_dir is not None:
        lines.append(
            f"EXTRA CORPUS SCALING (latency/footprint only, no golden labels): {extra_corpus_dir}"
        )
        for name, extra_result in extra.items():
            per_run = extra_result.mean_query_latency_ms_by_run
            row = (
                f"  {name:22} dim={extra_result.dim:4d}  bytes={extra_result.index_nbytes:10d}  "
                f"latency_ms_per_run=" + ",".join(f"{v:.4f}" for v in per_run)
            )
            lines.append(row)
        lines.append("")

    return "\n".join(lines)


def _report_to_json(
    results: dict[str, VariantResult],
    corpus_dir: Path,
    file_count: int,
    chunk_count: int,
    queries: list[GoldenQuery],
    top_ks: tuple[int, ...],
    repeats: int,
    extra: dict[str, ExtraCorpusResult] | None,
) -> dict[str, Any]:
    return {
        "artifact": "bench_dense_compression_quality",
        "suite": "eval_dense_compression_quality",
        "generated_at_epoch_s": time.time(),
        "environment": {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "python_version": platform.python_version(),
        },
        "corpus": str(corpus_dir),
        "file_count": file_count,
        "chunk_count": chunk_count,
        "query_count": len(queries),
        "top_ks": list(top_ks),
        "repeats": repeats,
        "variants": {
            name: {
                "dim": result.dim,
                "index_nbytes": result.index_nbytes,
                "index_build_ms": round(result.index_build_ms, 4),
                "determinism_ok": result.determinism_ok,
                "quality": {m: round(result.mean_metric(m), 6) for m in _METRIC_NAMES},
                "mean_latency_ms_by_run": [
                    round(result.mean_latency_ms(i), 6) for i in range(repeats)
                ],
            }
            for name, result in results.items()
        },
        "extra_corpus": (
            None
            if extra is None
            else {
                name: {
                    "dim": r.dim,
                    "index_nbytes": r.index_nbytes,
                    "index_build_ms": round(r.index_build_ms, 4),
                    "mean_latency_ms_by_run": [round(v, 6) for v in r.mean_query_latency_ms_by_run],
                }
                for name, r in extra.items()
            }
        ),
    }


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dense-leg compression quality/latency/footprint benchmark."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN_PATH)
    parser.add_argument(
        "--top-k",
        type=int,
        action="append",
        dest="top_ks",
        default=None,
        help="rank cutoff(s) to report (repeatable; default: 5 and 10)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="repeat the full query loop this many times (noise-floor discipline; default 2)",
    )
    parser.add_argument(
        "--rescore-candidates",
        type=int,
        default=50,
        help="binary+rescore shortlist size (default 50, mirrors DenseCompressionConfig's default)",
    )
    parser.add_argument(
        "--extra-corpus",
        type=Path,
        default=None,
        help="OPTIONAL second corpus (e.g. a real large source tree) for a latency/footprint-only "
        "scaling check at a much larger N -- no golden labels required or used.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    top_ks = tuple(args.top_ks) if args.top_ks else DEFAULT_TOP_KS
    repeats = max(2, args.repeats)  # noise-floor discipline: never fewer than 2 repeats

    try:
        from tensor_grep.core.retrieval_chunker import chunk_file

        queries = load_golden_queries(args.golden)
        corpus_files = load_corpus_files(args.corpus)
        validate_golden_against_corpus(queries, args.corpus)

        chunks = []
        for path in corpus_files:
            chunks.extend(chunk_file(path))

        model = build_dense_model()
        variants = build_variants(args.rescore_candidates)

        results: dict[str, VariantResult] = {}
        for name, config in variants.items():
            results[name] = run_variant(
                name, config, chunks, args.corpus, queries, top_ks, model, repeats=repeats
            )

        extra_results: dict[str, ExtraCorpusResult] | None = None
        if args.extra_corpus is not None:
            if not args.extra_corpus.is_dir():
                raise EvalError(f"--extra-corpus path does not exist: {args.extra_corpus}")
            extra_files = sorted(str(p) for p in args.extra_corpus.rglob("*.py") if p.is_file())
            if not extra_files:
                raise EvalError(f"--extra-corpus contains no .py files: {args.extra_corpus}")
            extra_chunks = []
            for path in extra_files:
                extra_chunks.extend(chunk_file(path))
            probe_queries = [q.query for q in queries]  # reuse the same vocab-mismatch prompts
            extra_results = {}
            for name, config in variants.items():
                extra_results[name] = run_extra_corpus_variant(
                    name, config, extra_chunks, probe_queries, model, repeats=repeats
                )
    except EvalError as exc:
        print(f"tg-eval: ERROR: {exc}", file=sys.stderr)
        return 1

    report_text = render_report(
        results,
        top_ks,
        args.corpus,
        len(corpus_files),
        len(chunks),
        len(queries),
        repeats,
        extra_results,
        args.extra_corpus,
    )
    print(report_text)

    for name, result in results.items():
        if not result.determinism_ok:
            print(
                f"tg-eval: ERROR: variant {name!r} produced non-identical quality metrics across "
                f"{repeats} repeats (non-deterministic)",
                file=sys.stderr,
            )
            return 1

    payload = _report_to_json(
        results,
        args.corpus,
        len(corpus_files),
        len(chunks),
        queries,
        top_ks,
        repeats,
        extra_results,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nResults written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
