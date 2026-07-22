"""T8: LIVE retrieval-quality golden-set harness (design doc
docs/plans/design-tensor-grep-late-rerank-2026-07-09.md:48, "Golden-set gate").

This is the oracle that gates every future `tg find` / late-rerank promotion decision, so
trustworthiness is the whole point -- see the 4 mandatory must-fixes (E1-E4) called out below,
each baked into `tests/unit/test_eval_late_rerank_quality.py`, not just asserted in prose.

Pipeline (LIVE, not replayed): chunk the committed corpus -> build a BM25 index (always) and a
dense index (if the `semantic` extra is installed and the model is fetched) -> query each leg ->
fuse with Reciprocal Rank Fusion -> optionally MaxSim-rerank the fused head (if the `rerank`
extra is installed and the model is fetched) -> score every arm's ranking with the SAME four
binary-relevance functions from ``tensor_grep.core.retrieval_scoring`` (recall_at_k, precision_at_k,
mean_reciprocal_rank_at_k, ndcg_at_k) -- no new metric is invented here (verification report
Correction #7: golden labels are relevant-FILE sets; scoring is file-granularity, matching this
repo's existing ``eval_bm25_quality.py`` precedent).

Arms: ``bm25`` (always scored), ``dense``/``rrf``/``rrf_shipped``/``rrf+maxsim`` (scored when their
leg is available, else SKIPPED with a loud, specific reason -- never silently degraded into a
vacuous comparison), ``find``/``find+stack`` (ALWAYS skipped -- see
:data:`SKIP_FIND_ARM_NOT_WIRED`: ``tg find``'s own CLI/MCP pipeline is built and shipped
(v1.77.0/v1.78.0, docs/BACKLOG.md #189's "tg find campaign" history), this harness has simply never
been wired to invoke it end-to-end as a scored arm). A comparison/verdict is REFUSED
(``GoldenSetError``) whenever either side of the pair is not ``scored`` (E1's "never a vacuous
comparison" discipline generalized to the arm-skip mechanism, per the review ledger's WAVE 1 E1/E4
items and plan Correction #2).

``rrf_shipped`` (accuracy-leg regression protection, added after the dense-weight flip -- #191/#634
-- landed in production and shipped live in v1.93.2): the SAME bm25+dense RRF fusion as ``rrf``,
but weighted with :data:`SHIPPED_DENSE_WEIGHT` -- the value ``tg find`` actually uses by default
for a multi-word NL query once ``TG_FIND_DENSE_WEIGHT`` is unset (``cli/main.py``'s
``_find_dense_weight``). Before this arm existed, the harness could only ever measure the OLD 1:1
fusion (``rrf``, kept exactly as-is below as a clearly-labeled comparison baseline, never removed)
-- meaning the shipped behavior itself had zero regression protection. See
:func:`run_rrf_arm`'s own docstring for the byte-identical-at-default-weight contract.

The 4 mandatory must-fixes from the adversarial review (tg_find_review_ledger.md, "WAVE 1"):

- **E1 (oracle blind spot):** ``recall_at_k``/``ndcg_at_k`` return a vacuous 1.0 for ANY ranking
  when ``relevant`` is empty (retrieval_scoring.py:8-9,29-30). :func:`load_golden_queries` asserts
  every query has a NON-EMPTY ``relevant`` set at load time -- a loud :class:`GoldenSetError`, not
  a silent perfect score. :func:`validate_oracle` then proves the METRIC itself behaves: a "gold"
  ranking (every relevant file first) must score ndcg@k == 1.0 EXACTLY; a "reversed" ranking (every
  relevant file placed last) and an "empty" ranking must score AT OR BELOW a documented ceiling
  (:func:`_reversed_ceiling`, ported from the discipline in ``scratchpad/bench/validate_oracle.py:56-72``
  + ``score.py:45-53`` -- compute the ACHIEVABLE floor from the corpus/relevant-set sizes rather
  than a blanket hardcoded number).
- **E2 (corpus-hardness gate, machine-checked):** the committed corpus + golden queries are
  constructed so BM25 alone should score near-floor on them (unlike ``eval_bm25_quality.py``'s own
  corpus, which SATURATES BM25 at recall 1.0 -- the cautionary example named in the review). This
  is VERIFIED by ``test_corpus_hardness_bm25_near_floor``, not merely asserted in a docstring.
- **E3 (drop the fig leaf):** ``--corpus <path>`` is an OPTIONAL, NON-GATING manual override (loud
  error if the path is given but missing). It is never wired into CI and the external
  express/click corpora are never claimed as a gate -- the committed synthetic corpus + E2 is the
  sole blocking discriminator.
- **E4 (bar power floor):** :func:`paired_comparison` (and the report's PAIRED COMPARISON section)
  gives a per-query win/loss/tie breakdown between any two SCORED arms, not just an aggregate mean
  -- so a promotion decision can be checked for a few outlier queries dragging a mean around before
  it gates a ship decision.

Usage (always via ``uv run --no-sync``, this repo's convention):

    uv run --no-sync python benchmarks/eval_late_rerank_quality.py
    uv run --no-sync python benchmarks/eval_late_rerank_quality.py --validate-oracle
    uv run --no-sync python benchmarks/eval_late_rerank_quality.py --runs 3
    uv run --no-sync python benchmarks/eval_late_rerank_quality.py --corpus /path/to/express-4.21.1

Exit 0 on success (including "every arm skipped but ran cleanly"); exit 1 on a loud config/oracle
error (missing corpus, malformed golden set, non-deterministic --runs); exit 2 is not used here
(this is a benchmarks script, not a `tg` command -- no rg-parity exit-code contract applies).

Regression protection: ``tests/eval/test_retrieval_quality_regression.py`` asserts the
``rrf_shipped`` arm's ndcg@10 stays above a floor on the committed golden set -- opt-in, ``eval``
marker (excluded from the default ``-m "not eval"`` CI sweep, no CI workflow currently installs
the `semantic` extra + fetches the model). Run it explicitly:

    uv run --no-sync pytest tests/eval/test_retrieval_quality_regression.py -m eval -v -s
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk, chunk_file
from tensor_grep.core.retrieval_dense import (
    DenseIndex,
    DenseUnavailableError,
    dense_available,
    load_dense_model,
)
from tensor_grep.core.retrieval_dense import (
    default_model_dir as default_dense_model_dir,
)
from tensor_grep.core.retrieval_fusion import DEFAULT_K, reciprocal_rank_fusion
from tensor_grep.core.retrieval_late import (
    LateReranker,
    LateRerankUnavailableError,
    late_available,
    load_late_reranker,
)
from tensor_grep.core.retrieval_late import (
    default_model_dir as default_late_model_dir,
)
from tensor_grep.core.retrieval_scoring import (
    mean_reciprocal_rank_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

DEFAULT_CORPUS_DIR = Path(__file__).resolve().parent / "datasets" / "find_golden_corpus"
DEFAULT_GOLDEN_PATH = Path(__file__).resolve().parent / "datasets" / "late_rerank_golden.jsonl"

DEFAULT_TOP_KS: tuple[int, ...] = (5, 10)
# Mirrors reranker.py's _DEFAULT_RERANK_POOL_K (50) -- a fresh LOCAL constant, not an import: that
# name is a private module constant of reranker.py, not part of its public contract.
DEFAULT_POOL_K = 50

# Accuracy-leg regression protection (2026-07-22): mirrors cli/main.py's
# `_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT` (main.py ~:4171) -- the `dense_weight` `tg find` actually
# passes to `rank_chunks` for a genuinely multi-word NL query once `TG_FIND_DENSE_WEIGHT` is
# unset, since the #191/#634 dense-weight flip went live (shipped in v1.93.2). Every golden query
# in this harness's corpus is multi-word NL (see the module docstring's "NL 40-query set"), so this
# one weight reproduces the shipped default across the whole set. A fresh LOCAL constant, not an
# import, for the exact reason `DEFAULT_POOL_K` above is one: `_FIND_DENSE_WEIGHT_ADAPTIVE_DEFAULT`
# is a private module constant of `cli/main.py`, not part of its public contract -- and importing
# it would drag a ~17k-line Typer CLI module (with its own heavyweight dataclass/typer import
# surface) into a benchmarks script that needs exactly one float. If `cli/main.py`'s shipped
# default ever changes, update this constant to match and re-run the golden-set report to confirm
# the new number, rather than importing across the module boundary.
SHIPPED_DENSE_WEIGHT = 5.0

# STALE-CLAIM FIX (accuracy-leg blind-spot audit, 2026-07-22): this constant used to read
# "awaiting-wave-2 (tg find pipeline not built yet)" -- true when this harness was first written
# (Wave 1, #625) but FALSE since v1.77.0: `tg find`'s CLI pipeline shipped that release (Wave
# 2b/2c, #626) and its MCP tool followed in v1.78.0 (Wave 2d, #627) -- see docs/BACKLOG.md #189's
# "tg find campaign" history. find/find+stack are still skipped below, but for the HONEST reason:
# nobody has wired THIS golden-set harness to invoke the real `tg find` pipeline end-to-end and
# score its output yet -- a distinct, not-yet-scheduled integration task, not a missing dependency.
# Reported as an explicit, permanent skip stub until that wiring lands; never silently omitted from
# the arm list.
SKIP_FIND_ARM_NOT_WIRED = (
    "skipped: tg find's CLI/MCP pipeline is built and shipped (v1.77.0/v1.78.0) -- this harness "
    "has not been wired to invoke it end-to-end as a scored arm yet (a separate integration task, "
    "not a missing dependency)"
)

_METRIC_NAMES: tuple[str, ...] = (
    "recall@5",
    "recall@10",
    "precision@10",
    "ndcg@5",
    "ndcg@10",
    "mrr",
)


class GoldenSetError(ValueError):
    """A loud, non-silent failure for a malformed golden dataset, missing corpus, or an attempt
    to compare a SKIPPED arm as though it were scored. Never caught and downgraded to a warning --
    this is the harness refusing to fabricate a result (E1/E4 discipline)."""


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    query: str
    category: str
    relevant_files: frozenset[str]


@dataclass
class ArmResult:
    name: str
    status: str  # "scored" or "skipped"
    reason: str | None = None
    # query id -> {metric_name: value}; empty for a skipped arm.
    per_query: dict[str, dict[str, float]] = field(default_factory=dict)

    def mean(self, metric: str) -> float:
        values = [row[metric] for row in self.per_query.values()]
        return sum(values) / len(values) if values else 0.0


@dataclass(frozen=True)
class PairedComparison:
    arm_a: str
    arm_b: str
    metric: str
    wins_a: int
    wins_b: int
    ties: int
    per_query: tuple[tuple[str, float, float], ...]


@dataclass
class Report:
    corpus_dir: str
    file_count: int
    chunk_count: int
    queries: list[GoldenQuery]
    arms: dict[str, ArmResult]


# ---------------------------------------------------------------------------------------
# Loading + loud validation (E1, E3)
# ---------------------------------------------------------------------------------------


def load_golden_queries(path: Path) -> list[GoldenQuery]:
    """Load the golden JSONL, refusing (loudly) any entry whose ``relevant`` set is empty.

    E1: ``recall_at_k``/``ndcg_at_k`` treat an empty ``relevant`` set as vacuously perfect
    (retrieval_scoring.py:8-9,29-30) -- a mislabeled query pointing at a renamed/deleted file
    would otherwise silently inject a perfect score into every arm's mean. This is the single
    choke point that guarantees that can never happen: every ``GoldenQuery`` this function returns
    is guaranteed to have a non-empty ``relevant_files`` set, or loading raises.
    """
    if not path.is_file():
        raise GoldenSetError(f"golden query file not found: {path}")

    queries: list[GoldenQuery] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenSetError(f"{path}:{line_number}: invalid JSON: {exc}") from exc

        query_id = row.get("id")
        if not query_id:
            raise GoldenSetError(f"{path}:{line_number}: missing required field 'id'")
        if query_id in seen_ids:
            raise GoldenSetError(f"{path}:{line_number}: duplicate query id {query_id!r}")
        seen_ids.add(query_id)

        query_text = row.get("query")
        if not query_text:
            raise GoldenSetError(
                f"{path}:{line_number} ({query_id}): missing required field 'query'"
            )

        category = row.get("category")
        if not category:
            raise GoldenSetError(
                f"{path}:{line_number} ({query_id}): missing required field 'category'"
            )

        relevant_entries = row.get("relevant")
        if not relevant_entries:
            # THE E1 GUARD: a query with an empty (or missing) `relevant` set is a broken golden
            # label, not a legitimate "nothing is relevant" case -- refuse it loudly rather than
            # silently handing recall_at_k/ndcg_at_k's vacuous-truth branch a query it will score
            # as a perfect 1.0 for every arm, forever, with no visible signal that anything is
            # wrong. There is no "documented convention" exemption here (unlike the scratchpad
            # P4/deps scorer): a `tg find`-style location query ALWAYS has a real, non-empty
            # answer by construction.
            raise GoldenSetError(
                f"{path}:{line_number} ({query_id}): 'relevant' must be a non-empty list -- an "
                "empty gold-label set would let recall_at_k/ndcg_at_k's vacuous-truth branch "
                "silently score this query as PERFECT for every arm (retrieval_scoring.py:8-9,"
                "29-30). Fix the golden label instead of leaving it empty."
            )

        relevant_files = frozenset(entry["file"] for entry in relevant_entries)
        queries.append(
            GoldenQuery(
                id=query_id, query=query_text, category=category, relevant_files=relevant_files
            )
        )

    if not queries:
        raise GoldenSetError(f"{path}: contained zero golden queries")
    return queries


def load_corpus_files(corpus_dir: Path) -> list[str]:
    """Return every file under ``corpus_dir``, sorted for determinism, as absolute path strings.

    E3: this is the ONLY corpus-loading path -- ``--corpus`` (main()) swaps ``corpus_dir`` for a
    manual override, but the loading contract (loud failure on a missing/empty directory) is
    identical either way. A missing directory is a configuration error, never a silently-empty
    corpus.
    """
    if not corpus_dir.is_dir():
        raise GoldenSetError(f"corpus directory not found: {corpus_dir}")
    files = sorted(str(p) for p in corpus_dir.rglob("*") if p.is_file())
    if not files:
        raise GoldenSetError(f"corpus directory is empty: {corpus_dir}")
    return files


def _relative_posix(path_str: str, corpus_dir: Path) -> str:
    return Path(path_str).resolve().relative_to(corpus_dir.resolve()).as_posix()


def validate_golden_against_corpus(queries: list[GoldenQuery], corpus_dir: Path) -> None:
    """Loudly refuse a golden query whose relevant file does not actually exist in the corpus --
    a stale label (the file was renamed/removed) must never silently score as an unreachable 0
    forever; it is a dataset bug, not a hard query."""
    corpus_root = corpus_dir.resolve()
    for query in queries:
        for relevant_file in query.relevant_files:
            if not (corpus_root / relevant_file).is_file():
                raise GoldenSetError(
                    f"{query.id}: relevant file {relevant_file!r} does not exist under {corpus_dir}"
                )


# ---------------------------------------------------------------------------------------
# Ranking -> deduped file order, and scoring (retrieval_scoring.py functions ONLY)
# ---------------------------------------------------------------------------------------


def _dedupe_ranked_files(
    ranked_chunk_indices: list[int], chunks: list[Chunk], corpus_dir: Path
) -> list[str]:
    """Collapse a chunk-index ranking to a file-path ranking, keeping first occurrence only --
    mirrors ``benchmarks/eval_bm25_quality.py``'s ``_ranked_basenames`` (Correction #7: score at
    file granularity, the existing precedent, no new graded-relevance metric invented)."""
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
    metrics: dict[str, float] = {}
    for k in top_ks:
        metrics[f"recall@{k}"] = recall_at_k(ranked_files, relevant_files, top_k=k)
        metrics[f"ndcg@{k}"] = ndcg_at_k(ranked_files, relevant_files, top_k=k)
    max_k = max(top_ks)
    metrics["precision@10"] = precision_at_k(ranked_files, relevant_files, top_k=10)
    metrics["mrr"] = mean_reciprocal_rank_at_k(ranked_files, relevant_files, top_k=max_k)
    return metrics


# ---------------------------------------------------------------------------------------
# Arms
# ---------------------------------------------------------------------------------------


def run_bm25_arm(
    chunks: list[Chunk], corpus_dir: Path, queries: list[GoldenQuery], top_ks: tuple[int, ...]
) -> ArmResult:
    index = Bm25Index(chunks)
    per_query: dict[str, dict[str, float]] = {}
    for query in queries:
        ranked_idx = [i for i, _score in index.query(query.query, top_k=max(1, len(index.chunks)))]
        ranked_files = _dedupe_ranked_files(ranked_idx, chunks, corpus_dir)
        per_query[query.id] = _score_ranking(ranked_files, query.relevant_files, top_ks)
    return ArmResult(name="bm25", status="scored", per_query=per_query)


def build_dense_index(chunks: list[Chunk]) -> tuple[DenseIndex | None, str | None]:
    """Attempt to build the dense leg via the REAL production probes -- ``dense_available()``
    (extra installed?) then ``load_dense_model()`` (model fetched?). Returns ``(index, None)`` on
    success or ``(None, reason)`` on any RECOVERABLE unavailability. A genuine
    :class:`BackendExecutionError` (corrupt model, encode fault) is NOT caught here -- it
    propagates per the Backend Fail-Closed Contract (AGENTS.md): a real backend fault must never
    be swallowed into a plausible-looking "skipped" arm.
    """
    available, reason = dense_available()
    if not available:
        return None, reason
    try:
        model = load_dense_model(default_dense_model_dir())
    except DenseUnavailableError as exc:
        return None, str(exc)
    return DenseIndex(chunks, model), None


def run_dense_arm(
    chunks: list[Chunk],
    corpus_dir: Path,
    queries: list[GoldenQuery],
    top_ks: tuple[int, ...],
    dense_index: DenseIndex,
) -> ArmResult:
    per_query: dict[str, dict[str, float]] = {}
    for query in queries:
        ranked_idx = [i for i, _score in dense_index.query(query.query, top_k=max(1, len(chunks)))]
        ranked_files = _dedupe_ranked_files(ranked_idx, chunks, corpus_dir)
        per_query[query.id] = _score_ranking(ranked_files, query.relevant_files, top_ks)
    return ArmResult(name="dense", status="scored", per_query=per_query)


def run_rrf_arm(
    chunks: list[Chunk],
    corpus_dir: Path,
    queries: list[GoldenQuery],
    top_ks: tuple[int, ...],
    bm25_index: Bm25Index,
    dense_index: DenseIndex,
    *,
    dense_weight: float = 1.0,
    name: str = "rrf",
) -> ArmResult:
    """Fuse the bm25 + dense leg via plain RRF -- mirrors ``rank_chunks``'s own fusion step
    (reranker.py:258-267), minus the late-rerank/path-channel extras this harness either scores
    separately (``rrf+maxsim``) or never exercises (the ``TG_RRF_CHANNELS`` path channel).

    ``dense_weight`` (accuracy-leg regression protection): a per-leg RRF weight, identical in
    meaning to ``rank_chunks``'s own parameter of the same name. ``dense_weight=1.0`` (the
    default, and every call site that predates this parameter) is a BYTE-IDENTICAL no-op:
    ``reciprocal_rank_fusion`` is called with ``weights=None`` exactly as before, so the original
    unweighted ``rrf`` arm this function has always produced is unchanged by this parameter's
    existence. A non-default value builds ``weights=[1.0, dense_weight]``, the same two-entry
    shape ``rank_chunks`` builds internally for the identical reason.

    ``name`` lets a caller build a second, differently-weighted ``ArmResult`` from this same
    function without a second copy-pasted implementation (see ``build_report``'s ``rrf_shipped``);
    the returned ``ArmResult`` is otherwise identical in shape to the un-parameterized original.
    """
    per_query: dict[str, dict[str, float]] = {}
    total = max(1, len(chunks))
    weights: list[float] | None = [1.0, dense_weight] if dense_weight != 1.0 else None
    for query in queries:
        bm25_ranking = [i for i, _score in bm25_index.query(query.query, top_k=total)]
        dense_ranking = [i for i, _score in dense_index.query(query.query, top_k=total)]
        fused = reciprocal_rank_fusion([bm25_ranking, dense_ranking], k=DEFAULT_K, weights=weights)
        ranked_files = _dedupe_ranked_files(fused, chunks, corpus_dir)
        per_query[query.id] = _score_ranking(ranked_files, query.relevant_files, top_ks)
    return ArmResult(name=name, status="scored", per_query=per_query)


def build_late_reranker() -> tuple[LateReranker | None, str | None]:
    """Mirrors :func:`build_dense_index`: real production probes
    (``late_available()``/``load_late_reranker()``), recoverable unavailability returns
    ``(None, reason)``; a :class:`BackendExecutionError` propagates uncaught."""
    available, reason = late_available()
    if not available:
        return None, reason
    try:
        return load_late_reranker(default_late_model_dir()), None
    except LateRerankUnavailableError as exc:
        return None, str(exc)


def run_rrf_maxsim_arm(
    chunks: list[Chunk],
    corpus_dir: Path,
    queries: list[GoldenQuery],
    top_ks: tuple[int, ...],
    bm25_index: Bm25Index,
    dense_index: DenseIndex,
    late_reranker: LateReranker,
    *,
    pool_k: int = DEFAULT_POOL_K,
    dense_weight: float = 1.0,
) -> ArmResult:
    """Composes the SAME leg functions ``rrf`` uses, then MaxSim-reranks the fused head.

    ``dense_weight`` (accuracy-leg regression protection): threaded through for the same reason,
    and with the same byte-identical-at-1.0 contract, as :func:`run_rrf_arm` above -- it closes
    the identical latent no-weights gap in this arm's own ``reciprocal_rank_fusion`` call.
    ``build_report`` does not currently build a weighted ``rrf+maxsim`` variant by default: the
    unweighted arm already scores BELOW bm25 on this golden set (ndcg@10 0.068 vs 0.109 -- see the
    optimization queue's KILL LIST item F8.4, "late-rerank... twice-confirmed dead at the current
    model tier"), so a second maxsim variant is not a useful regression signal yet. The parameter
    exists so a future caller can measure a weighted maxsim arm without another signature change.

    Deliberately does NOT call ``reranker.rerank_hybrid`` -- that function operates over
    ``SearchResult.matches`` (grep-hit reordering) and wraps its late-interaction splice in a
    wall-clock-budgeted daemon thread (a CLI-latency safety valve, reranker.py:296-351). Both are
    the wrong shape for a golden-set harness that ranks a whole chunk corpus directly and MUST be
    deterministic across ``--runs N`` -- a real wall-clock budget is, by construction, a timing
    dependency. This calls :meth:`LateReranker.rerank` directly (a synchronous, non-threaded,
    pure computation), so the result is exactly reproducible.
    """
    per_query: dict[str, dict[str, float]] = {}
    total = max(1, len(chunks))
    weights: list[float] | None = [1.0, dense_weight] if dense_weight != 1.0 else None
    for query in queries:
        bm25_ranking = [i for i, _score in bm25_index.query(query.query, top_k=total)]
        dense_ranking = [i for i, _score in dense_index.query(query.query, top_k=total)]
        fused = reciprocal_rank_fusion([bm25_ranking, dense_ranking], k=DEFAULT_K, weights=weights)
        head = fused[:pool_k]
        tail = fused[pool_k:]
        if head:
            reordered_head = late_reranker.rerank(query.query, [chunks[i].text for i in head], head)
        else:
            reordered_head = head
        ranked_files = _dedupe_ranked_files(reordered_head + tail, chunks, corpus_dir)
        per_query[query.id] = _score_ranking(ranked_files, query.relevant_files, top_ks)
    return ArmResult(name="rrf+maxsim", status="scored", per_query=per_query)


def skipped_arm(name: str, reason: str) -> ArmResult:
    return ArmResult(name=name, status="skipped", reason=reason)


# ---------------------------------------------------------------------------------------
# E4: per-query paired win/loss/tie report
# ---------------------------------------------------------------------------------------


def paired_comparison(arm_a: ArmResult, arm_b: ArmResult, metric: str) -> PairedComparison:
    """Per-query win/loss/tie between two arms on ``metric`` -- E4: a promotion decision must
    never rely on a bare aggregate mean, which a handful of outlier queries can dominate.

    Refuses (raises :class:`GoldenSetError`) if EITHER arm is not ``scored`` -- pairing a skipped
    arm would either crash on a missing key or silently compare against an empty/fabricated
    ranking, exactly the "comparison verdict over a skipped arm" the review ledger forbids.
    """
    if arm_a.status != "scored" or arm_b.status != "scored":
        raise GoldenSetError(
            f"cannot pair-compare a skipped arm: {arm_a.name}={arm_a.status!r}, "
            f"{arm_b.name}={arm_b.status!r} -- a comparison verdict over a skipped arm is refused"
        )
    if arm_a.per_query.keys() != arm_b.per_query.keys():
        raise GoldenSetError(
            f"cannot pair-compare {arm_a.name} vs {arm_b.name}: scored different query sets"
        )

    wins_a = wins_b = ties = 0
    rows: list[tuple[str, float, float]] = []
    for query_id in sorted(arm_a.per_query):
        score_a = arm_a.per_query[query_id][metric]
        score_b = arm_b.per_query[query_id][metric]
        rows.append((query_id, score_a, score_b))
        if score_a > score_b:
            wins_a += 1
        elif score_b > score_a:
            wins_b += 1
        else:
            ties += 1
    return PairedComparison(
        arm_a=arm_a.name,
        arm_b=arm_b.name,
        metric=metric,
        wins_a=wins_a,
        wins_b=wins_b,
        ties=ties,
        per_query=tuple(rows),
    )


# ---------------------------------------------------------------------------------------
# E1: bidirectional oracle validation
# ---------------------------------------------------------------------------------------


def _reversed_ceiling(relevant_count: int, corpus_size: int, top_k: int) -> float:
    """The ACHIEVABLE best-case ndcg@top_k for a ranking that places every relevant item as late
    as possible ("reversed"), given the corpus/relevant-set sizes -- ported from the "ceiling"
    discipline in ``scratchpad/bench/validate_oracle.py:56-72`` + ``score.py:45-53``: compute the
    honestly-achievable floor instead of asserting a blind hardcoded threshold.

    When the corpus has room to push every relevant item outside the top_k window
    (``corpus_size - relevant_count >= top_k``), the true worst case is an EXACT 0.0 -- none of
    the first ``top_k`` ranked slots can contain a relevant item. If the corpus is too small for
    that (a pathological golden-set/corpus mismatch), some relevant items are unavoidably forced
    into the top_k window even in the "worst" placement; this returns the ndcg of that unavoidable
    best-case-for-BM25 arrangement instead of a value that could never actually be violated
    (which would make the assertion vacuous) or one that could never be met (flaky).
    """
    if corpus_size - relevant_count >= top_k:
        return 0.0
    # Fewer than top_k irrelevant slots exist, so `top_k - (corpus_size - relevant_count)`
    # relevant items are forced into the ranked window even in the worst arrangement. Compute the
    # ndcg of THAT forced placement (those items pushed as late as possible within the window).
    import math

    forced_count = top_k - (corpus_size - relevant_count)
    forced_count = max(0, min(forced_count, relevant_count))
    if forced_count == 0:
        return 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank in range(top_k - forced_count + 1, top_k + 1))
    ideal_hits = min(relevant_count, top_k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def validate_oracle(
    queries: list[GoldenQuery], corpus_files_relative: list[str], *, top_k: int = 10
) -> list[str]:
    """Bidirectional oracle validation (E1): for every golden query, a GOLD ranking (every
    relevant file first) must score ndcg@top_k EXACTLY 1.0, and a REVERSED ranking (every
    relevant file last) plus an EMPTY ranking must score AT OR BELOW the documented achievable
    ceiling (:func:`_reversed_ceiling`). Returns a list of human-readable failure strings (empty
    == pass) rather than raising, so callers can print every failure instead of stopping at the
    first one.
    """
    all_files = set(corpus_files_relative)
    failures: list[str] = []

    for query in queries:
        relevant = query.relevant_files
        others = sorted(all_files - relevant)

        gold_ranking = sorted(relevant) + others
        gold_ndcg = ndcg_at_k(gold_ranking, relevant, top_k=top_k)
        if gold_ndcg != 1.0:
            failures.append(
                f"{query.id}: GOLD ranking scored ndcg@{top_k}={gold_ndcg!r}, expected exactly 1.0"
            )

        ceiling = _reversed_ceiling(len(relevant), len(all_files), top_k)

        reversed_ranking = others + sorted(relevant)
        reversed_ndcg = ndcg_at_k(reversed_ranking, relevant, top_k=top_k)
        if reversed_ndcg > ceiling + 1e-9:
            failures.append(
                f"{query.id}: REVERSED ranking scored ndcg@{top_k}={reversed_ndcg!r}, above the "
                f"documented achievable ceiling {ceiling!r} -- broken oracle"
            )

        empty_ndcg = ndcg_at_k([], relevant, top_k=top_k)
        if empty_ndcg > ceiling + 1e-9:
            failures.append(
                f"{query.id}: EMPTY ranking scored ndcg@{top_k}={empty_ndcg!r}, above the "
                f"documented achievable ceiling {ceiling!r} -- broken oracle"
            )

    return failures


# ---------------------------------------------------------------------------------------
# Orchestration + rendering
# ---------------------------------------------------------------------------------------


def build_report(
    queries: list[GoldenQuery],
    corpus_dir: Path,
    top_ks: tuple[int, ...],
    *,
    pool_k: int = DEFAULT_POOL_K,
    dense_index_override: DenseIndex | None = None,
    dense_reason_override: str | None = None,
    late_reranker_override: LateReranker | None = None,
    late_reason_override: str | None = None,
) -> Report:
    """Run every arm once and return a fully-populated :class:`Report`.

    ``*_override`` parameters exist ONLY for tests (dependency injection mirroring
    ``test_search_semantic_rerank.py``'s ``_stub_dense_clean``/``_FakeDenseModel`` and
    ``test_reranker_hybrid.py``'s ``_FixedVectorModel`` conventions) -- they let a test exercise
    the "dense/rrf/rrf_shipped/rrf+maxsim actually SCORED" path deterministically without the
    `semantic`/`rerank` extras or a fetched model being present in CI. When both are ``None``
    (the default, used by the real CLI), availability is probed for real via
    :func:`build_dense_index` / :func:`build_late_reranker`.
    """
    corpus_files = load_corpus_files(corpus_dir)
    chunks: list[Chunk] = []
    for path in corpus_files:
        chunks.extend(chunk_file(path))

    arms: dict[str, ArmResult] = {}
    bm25_index = Bm25Index(chunks)
    arms["bm25"] = run_bm25_arm(chunks, corpus_dir, queries, top_ks)

    if dense_index_override is not None:
        dense_index, dense_reason = dense_index_override, None
    elif dense_reason_override is not None:
        dense_index, dense_reason = None, dense_reason_override
    else:
        dense_index, dense_reason = build_dense_index(chunks)

    if dense_index is None:
        arms["dense"] = skipped_arm("dense", dense_reason or "dense leg unavailable")
        arms["rrf"] = skipped_arm("rrf", f"dense leg unavailable: {dense_reason}")
        arms["rrf_shipped"] = skipped_arm("rrf_shipped", f"dense leg unavailable: {dense_reason}")
        arms["rrf+maxsim"] = skipped_arm("rrf+maxsim", f"dense leg unavailable: {dense_reason}")
    else:
        arms["dense"] = run_dense_arm(chunks, corpus_dir, queries, top_ks, dense_index)
        arms["rrf"] = run_rrf_arm(chunks, corpus_dir, queries, top_ks, bm25_index, dense_index)
        # Accuracy-leg regression protection: the SAME rrf fusion, weighted to match what `tg
        # find` actually ships by default (SHIPPED_DENSE_WEIGHT) -- this is the arm
        # tests/eval/test_retrieval_quality_regression.py's ndcg@10 floor measures. `rrf` above is
        # untouched (still the old 1:1 comparison baseline), so this is purely additive.
        arms["rrf_shipped"] = run_rrf_arm(
            chunks,
            corpus_dir,
            queries,
            top_ks,
            bm25_index,
            dense_index,
            dense_weight=SHIPPED_DENSE_WEIGHT,
            name="rrf_shipped",
        )

        if late_reranker_override is not None:
            late_reranker, late_reason = late_reranker_override, None
        elif late_reason_override is not None:
            late_reranker, late_reason = None, late_reason_override
        else:
            late_reranker, late_reason = build_late_reranker()

        if late_reranker is None:
            arms["rrf+maxsim"] = skipped_arm(
                "rrf+maxsim", f"late rerank leg unavailable: {late_reason}"
            )
        else:
            arms["rrf+maxsim"] = run_rrf_maxsim_arm(
                chunks,
                corpus_dir,
                queries,
                top_ks,
                bm25_index,
                dense_index,
                late_reranker,
                pool_k=pool_k,
            )

    # find/find+stack: `tg find`'s own CLI/MCP pipeline is built and shipped (v1.77.0/v1.78.0);
    # this harness just has not been wired to invoke it end-to-end as a scored arm -- see
    # SKIP_FIND_ARM_NOT_WIRED's own comment for the full history. Never silently omitted from the
    # arm list.
    arms["find"] = skipped_arm("find", SKIP_FIND_ARM_NOT_WIRED)
    arms["find+stack"] = skipped_arm("find+stack", SKIP_FIND_ARM_NOT_WIRED)

    return Report(
        corpus_dir=str(corpus_dir),
        file_count=len(corpus_files),
        chunk_count=len(chunks),
        queries=queries,
        arms=arms,
    )


def _scored_arm_names(report: Report) -> list[str]:
    return [name for name, arm in report.arms.items() if arm.status == "scored"]


def render_report(report: Report, top_ks: tuple[int, ...]) -> str:
    lines: list[str] = []
    lines.append("tg find Wave 1 golden-set eval (T8)")
    lines.append(
        f"corpus: {report.corpus_dir} ({report.file_count} files, {report.chunk_count} chunks)"
    )

    by_category: dict[str, int] = {}
    for query in report.queries:
        by_category[query.category] = by_category.get(query.category, 0) + 1
    category_summary = " ".join(f"{cat}={count}" for cat, count in sorted(by_category.items()))
    lines.append(f"queries: {len(report.queries)} ({category_summary})")
    lines.append("")

    lines.append("ARM STATUS")
    for name, arm in report.arms.items():
        if arm.status == "scored":
            lines.append(f"  {name:12} scored")
        else:
            # `arm.reason` may already carry its own "skipped: ..." framing (e.g. find/find+stack's
            # SKIP_FIND_ARM_NOT_WIRED) or a bare unavailability message (e.g. dense's
            # dense_available() reason) -- a single "--" separator reads cleanly either way,
            # unlike wrapping every reason in a second, possibly-redundant "skipped (...)".
            lines.append(f"  {name:12} skipped -- {arm.reason}")
    lines.append("")

    scored_names = _scored_arm_names(report)
    lines.append(f"METRICS (scored arms only, mean over {len(report.queries)} queries)")
    header = "  arm          " + "  ".join(f"{m:>12}" for m in _METRIC_NAMES)
    lines.append(header)
    for name in scored_names:
        arm = report.arms[name]
        row = "  " + f"{name:12} " + "  ".join(f"{arm.mean(m):12.4f}" for m in _METRIC_NAMES)
        lines.append(row)
    if not scored_names:
        lines.append("  (no scored arms)")
    lines.append("")

    lines.append("PAIRED COMPARISON (scored arms only, metric=ndcg@10)")
    if len(scored_names) < 2:
        lines.append("  fewer than 2 scored arms -- no pair available")
    else:
        baseline = "bm25" if "bm25" in scored_names else scored_names[0]
        for name in scored_names:
            if name == baseline:
                continue
            comparison = paired_comparison(report.arms[baseline], report.arms[name], "ndcg@10")
            lines.append(
                f"  {baseline} vs {name}: wins({name})={comparison.wins_b} "
                f"wins({baseline})={comparison.wins_a} ties={comparison.ties}"
            )
    lines.append("")

    lines.append("VERDICT")
    if len(scored_names) >= 2 and "bm25" in scored_names:
        for name in scored_names:
            if name == "bm25":
                continue
            bm25_ndcg10 = report.arms["bm25"].mean("ndcg@10")
            arm_ndcg10 = report.arms[name].mean("ndcg@10")
            delta = arm_ndcg10 - bm25_ndcg10
            lines.append(f"  {name} vs bm25: ndcg@10 delta = {delta:+.4f}")
    else:
        lines.append(
            "  not computed -- need >=2 scored arms (bm25 + at least one other) for a comparison"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="T8 golden-set retrieval-quality harness (tg find Wave 1)."
    )
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help=(
            "OPTIONAL manual corpus override (e.g. a real external repo checkout). Loud error if "
            "given but missing. Never wired into CI -- the committed synthetic corpus is the gate "
            f"(default: {DEFAULT_CORPUS_DIR})."
        ),
    )
    parser.add_argument(
        "--golden", type=Path, default=DEFAULT_GOLDEN_PATH, help="golden query JSONL path"
    )
    parser.add_argument(
        "--top-k",
        type=int,
        action="append",
        dest="top_ks",
        default=None,
        help="rank cutoff(s) to report (repeatable; default: 5 and 10)",
    )
    parser.add_argument(
        "--pool-k", type=int, default=DEFAULT_POOL_K, help="MaxSim pool size for rrf+maxsim"
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="run the full pipeline this many times and require byte-identical rendered output",
    )
    parser.add_argument(
        "--validate-oracle",
        action="store_true",
        help="run ONLY the bidirectional oracle validation (E1) and exit; no arms are scored",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    top_ks = tuple(args.top_ks) if args.top_ks else DEFAULT_TOP_KS

    corpus_dir = args.corpus if args.corpus is not None else DEFAULT_CORPUS_DIR
    if args.corpus is not None and not args.corpus.is_dir():
        print(
            f"tg-eval: ERROR: --corpus path does not exist or is not a directory: {args.corpus}",
            file=sys.stderr,
        )
        return 1

    try:
        queries = load_golden_queries(args.golden)
        corpus_files = load_corpus_files(corpus_dir)
        validate_golden_against_corpus(queries, corpus_dir)
    except GoldenSetError as exc:
        print(f"tg-eval: ERROR: {exc}", file=sys.stderr)
        return 1

    if args.validate_oracle:
        corpus_relative = [_relative_posix(p, corpus_dir) for p in corpus_files]
        failures = validate_oracle(queries, corpus_relative, top_k=max(top_ks))
        if failures:
            print(f"ORACLE VALIDATION FAILED ({len(failures)} issue(s)):")
            for failure in failures:
                print(f"  - {failure}")
            return 1
        print(
            f"ORACLE VALIDATION PASSED: {len(queries)} queries, gold ranking scores ndcg@{max(top_ks)}=1.0 "
            "exactly; reversed and empty rankings score at or below the documented achievable ceiling."
        )
        return 0

    runs = max(1, args.runs)
    rendered_runs: list[str] = []
    for _ in range(runs):
        report = build_report(queries, corpus_dir, top_ks, pool_k=args.pool_k)
        rendered_runs.append(render_report(report, top_ks))

    if any(text != rendered_runs[0] for text in rendered_runs[1:]):
        print(
            "tg-eval: ERROR: --runs produced non-identical output across runs (pipeline is not deterministic)",
            file=sys.stderr,
        )
        for index, text in enumerate(rendered_runs):
            print(f"--- run {index + 1} ---", file=sys.stderr)
            print(text, file=sys.stderr)
        return 1

    print(rendered_runs[0])
    if runs > 1:
        print(f"\n{runs}/{runs} runs byte-identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
