"""Tests for :func:`tensor_grep.core.reranker.rank_chunks` -- the pure, fail-closed rank core
extracted from :func:`~tensor_grep.core.reranker.rerank_hybrid` (`tg find` plan, Wave 2a, #189).

This module pins `rank_chunks` DIRECTLY (no ``SearchResult``/matches wrapper, no CLI) so both
`rerank_hybrid` and any future caller (e.g. `tg find`) share ONE tested fusion+late-rerank core.
The byte-identical-behavior claim for the extraction itself is proven by
`test_reranker_hybrid.py` and `test_search_semantic_rerank.py` continuing to pass UNCHANGED (they
pin `rerank_hybrid`'s observable behavior end-to-end); this file additionally pins a path those two
never reach: a genuine (non-``LateRerankUnavailableError``) fault raised BY THE INJECTED ENCODER
INSIDE THE DAEMON WORKER THREAD (reranker.py's ``raise exc`` branch) -- see
``test_rank_chunks_other_exception_from_worker_thread_propagates`` below.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from tensor_grep.core.reranker import rank_chunks
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_dense import DenseIndex
from tensor_grep.core.retrieval_fusion import DEFAULT_K
from tensor_grep.core.retrieval_late import LateReranker, LateRerankUnavailableError


def _build_chunks() -> tuple[list[Chunk], Bm25Index]:
    """Three single-line chunks; only "invoice" (chunk 0) matches the query "invoice" -- BM25
    excludes zero-score chunks, so this also gives a non-empty, single-item late-rerank pool
    without needing any tmp_path file I/O (rank_chunks operates on already-built chunks/indexes)."""
    chunks = [
        Chunk(file_path="f1.py", start_line=1, end_line=1, text="parse_invoice"),
        Chunk(file_path="f2.py", start_line=1, end_line=1, text="helper_one"),
        Chunk(file_path="f3.py", start_line=1, end_line=1, text="helper_two"),
    ]
    return chunks, Bm25Index(chunks)


def test_rank_chunks_bm25_only_identity_fuse_is_noop() -> None:
    """No dense leg, no path channel, no late reranker: RRF over a SINGLE ranking list is an
    identity permutation of that list -- the plain BM25 order comes back unchanged, and
    late_fallback_reason stays None (the late stage was never even attempted)."""
    chunks, bm25_index = _build_chunks()

    fused_order, late_fallback_reason = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=None,
        k=DEFAULT_K,
    )

    expected_bm25_order = [
        chunk_idx for chunk_idx, _ in bm25_index.query("invoice", top_k=len(chunks))
    ]
    assert fused_order == expected_bm25_order == [0]
    assert late_fallback_reason is None


def test_rank_chunks_path_channel_weights_change_fusion_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """TG_RRF_CHANNELS=1 is the ONLY branch inside the extracted region that builds an explicit
    `weights` list (equal 1.0 for the BM25 leg, PATH_CHANNEL_WEIGHT for a non-empty path-channel
    leg) and threads it into `reciprocal_rank_fusion` -- exercise that "weights path" directly,
    mirroring test_reranker_hybrid.py's test_path_channel_boosts_filename_match_under_flag but at
    the rank_chunks level. Both chunks tie on BM25 (identical text); only the second chunk's
    FILENAME overlaps the query token "invoice"."""
    chunks = [
        Chunk(file_path="other_helper.py", start_line=1, end_line=1, text="shared_content"),
        Chunk(file_path="invoice_parser.py", start_line=1, end_line=1, text="shared_content"),
    ]
    bm25_index = Bm25Index(chunks)

    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    baseline_order, baseline_reason = rank_chunks(
        "invoice shared",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=None,
        k=DEFAULT_K,
    )
    assert [chunks[i].file_path for i in baseline_order] == [
        "other_helper.py",
        "invoice_parser.py",
    ]
    assert baseline_reason is None

    monkeypatch.setenv("TG_RRF_CHANNELS", "1")
    boosted_order, boosted_reason = rank_chunks(
        "invoice shared",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=None,
        k=DEFAULT_K,
    )
    assert [chunks[i].file_path for i in boosted_order] == [
        "invoice_parser.py",
        "other_helper.py",
    ]
    assert boosted_reason is None


def test_rank_chunks_late_rerank_budget_exceeded_degrades(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6: a late reranker whose encode sleeps past TG_RERANK_BUDGET_MS must degrade to the plain
    RRF order (never apply a slow reorder, never crash) and set late_fallback_reason -- mirrors
    test_search_semantic_rerank.py's test_rerank_budget_exceeded_degrades_with_reason's
    `_slow_encode` pattern, exercised directly against rank_chunks instead of through the CLI."""
    monkeypatch.setenv("TG_RERANK_BUDGET_MS", "1")
    chunks, bm25_index = _build_chunks()

    def _slow_encode(text: str) -> np.ndarray:
        time.sleep(0.05)  # 50ms, comfortably over the 1ms budget
        return np.array([[1.0]], dtype=np.float32)

    fused_order, late_fallback_reason = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=LateReranker(encode=_slow_encode),
        k=DEFAULT_K,
    )

    assert fused_order == [0]  # unchanged: degrades to the pre-late RRF order
    assert late_fallback_reason is not None
    assert "budget exceeded" in late_fallback_reason


def test_rank_chunks_late_rerank_unavailable_error_degrades() -> None:
    """A RECOVERABLE LateRerankUnavailableError from the injected encoder (e.g. a malformed
    embedding shape, or -- as here -- the model simply not being fetched) must degrade to the
    plain RRF order and surface the reason, never propagate as a crash."""
    chunks, bm25_index = _build_chunks()

    def _raising_encode(text: str) -> np.ndarray:
        raise LateRerankUnavailableError("late rerank unavailable: model not fetched")

    fused_order, late_fallback_reason = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=LateReranker(encode=_raising_encode),
        k=DEFAULT_K,
    )

    assert fused_order == [0]  # unchanged: degrades to the pre-late RRF order
    assert late_fallback_reason is not None
    assert "model not fetched" in late_fallback_reason


def test_rank_chunks_other_exception_from_worker_thread_propagates() -> None:
    """C-plan-2 (adversarial review must-fix, #189): reranker.py's `raise exc` branch classifies
    and re-raises any encode-time fault that is NOT a LateRerankUnavailableError -- e.g. a genuine
    BackendExecutionError, or any other non-recoverable exception. This branch is CURRENTLY
    UNPINNED through `rerank_hybrid`: the existing corrupt-model tests raise from
    `load_late_reranker` in the CLI (a different path, before the worker thread ever starts), and
    `test_retrieval_late.py` exercises `LateReranker` in isolation, never through the thread
    splice. This test injects the fault directly in the `encode` callable -- which
    `LateReranker.rerank` invokes from INSIDE the daemon worker thread (`_run_late_rerank`) -- so
    it proves the propagation genuinely crosses the thread boundary (via the
    `rerank_error`/`worker.join()` handoff), not merely that the exception type would have been
    raised had the call been synchronous.
    """

    class _EncodeBoom(RuntimeError):
        """A stand-in for a genuine, non-recoverable encode-time fault."""

    def _raising_other(text: str) -> np.ndarray:
        raise _EncodeBoom("genuine encode-time fault, not recoverable")

    chunks, bm25_index = _build_chunks()

    with pytest.raises(_EncodeBoom, match="genuine encode-time fault"):
        rank_chunks(
            "invoice",
            chunks,
            bm25_index=bm25_index,
            dense_index=None,
            late_reranker=LateReranker(encode=_raising_other),
            k=DEFAULT_K,
        )


class _FixedVectorModel:
    """Deterministic stand-in dense encoder: maps each EXACT input string to a hand-picked
    vector, mirroring ``test_reranker_hybrid.py``'s identical fixture -- a scenario's dense-leg
    cosine ranking is fully predictable."""

    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self._vectors_by_text = vectors_by_text

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.array([self._vectors_by_text[t] for t in texts], dtype=np.float32)


def test_rank_chunks_dense_weight(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """``dense_weight`` (#189, ledger DENSE-WEIGHT SWEEP): a per-call RRF weight multiplier on the
    dense leg, relative to the BM25 leg's fixed 1.0.

    - ``dense_weight=1.0`` (the default, and the value an omitted kwarg implies) MUST be a
      byte-identical no-op: ``reciprocal_rank_fusion`` must be called with ``weights=None``,
      exactly as it always was before this kwarg existed -- proven here by spying on
      ``reranker.reciprocal_rank_fusion`` and asserting the captured ``weights`` arg is ``None``,
      not merely that the fused order happens to match (a coincidental match would not catch e.g.
      an accidental ``weights=[1.0, 1.0]`` substitution, which -- per
      ``retrieval_fusion.reciprocal_rank_fusion``'s own docstring -- IS numerically identical to
      ``None`` for THIS scenario, but is not the same call shape ``rerank_hybrid`` has always made).
    - ``dense_weight=5.0`` must thread ``weights=[1.0, 5.0]`` into fusion (bm25 leg at 1.0, dense
      leg boosted 5x), and this must be OBSERVABLE: a scenario where the BM25-preferred chunk and
      the dense-preferred chunk differ enough that boosting the dense leg flips which one fuses to
      the TOP rank. ``k=1`` (rather than the production ``DEFAULT_K=60``) is used deliberately to
      keep the scenario to 4 small, hand-computable chunks -- RRF's ``1/(k+rank)`` term is far more
      rank-sensitive at a small ``k``, so a clean top-rank flip needs neither a large corpus nor an
      extreme weight to demonstrate.
    """
    from tensor_grep.core import reranker as reranker_module

    real_fusion = reranker_module.reciprocal_rank_fusion
    captured_weights: list[list[float] | None] = []

    def _spy_fusion(rankings, *, k=DEFAULT_K, weights=None):  # type: ignore[no-untyped-def]
        captured_weights.append(list(weights) if weights is not None else None)
        return real_fusion(rankings, k=k, weights=weights)

    monkeypatch.setattr(reranker_module, "reciprocal_rank_fusion", _spy_fusion)

    # chunk 0 is the ONLY bm25 match for "invoice" and the WORST (last, rank 4) dense match;
    # chunk 1 is the BEST (rank 1) dense match and absent from bm25 entirely; chunks 2/3 are
    # dense-only filler at ranks 2/3 so chunk 1's dense rank is unambiguously "1st of 4", not "1st
    # of 2" (a degenerate 2-chunk scenario RRF would not meaningfully distinguish from bm25 alone).
    chunks = [
        Chunk(file_path="bm25_pick.py", start_line=1, end_line=1, text="invoice request"),
        Chunk(file_path="dense_pick.py", start_line=1, end_line=1, text="filler_a"),
        Chunk(file_path="filler_b.py", start_line=1, end_line=1, text="filler_b"),
        Chunk(file_path="filler_c.py", start_line=1, end_line=1, text="filler_c"),
    ]
    bm25_index = Bm25Index(chunks)
    dense_model = _FixedVectorModel({
        "invoice": [1.0, 0.0],  # the QUERY's own vector
        "invoice request": [
            0.0,
            1.0,
        ],  # chunk 0: orthogonal to the query -> cosine 0, rank 4 (last)
        "filler_a": [1.0, 0.0],  # chunk 1: cosine 1.0, rank 1 (best)
        "filler_b": [1.0, 0.1],  # chunk 2: cosine ~0.995, rank 2
        "filler_c": [1.0, 0.2],  # chunk 3: cosine ~0.981, rank 3
    })
    dense_index = DenseIndex(chunks, dense_model)

    # Sanity-check the scenario setup itself before trusting the fused assertions below.
    assert [i for i, _ in bm25_index.query("invoice", top_k=4)] == [0]
    assert [i for i, _ in dense_index.query("invoice", top_k=4)] == [1, 2, 3, 0]

    order_default, _ = rank_chunks(
        "invoice", chunks, bm25_index=bm25_index, dense_index=dense_index, late_reranker=None, k=1
    )
    assert captured_weights[-1] is None, (
        "dense_weight defaulting to 1.0 must not build a weights list at all"
    )
    assert order_default[0] == 0, "equal weight: the bm25-favored chunk fuses to rank 1"

    order_explicit_default, _ = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=dense_index,
        late_reranker=None,
        k=1,
        dense_weight=1.0,
    )
    assert captured_weights[-1] is None, (
        "an EXPLICIT dense_weight=1.0 must also skip the weights list entirely"
    )
    assert order_explicit_default == order_default, (
        "dense_weight=1.0 must be byte-identical to omitting the kwarg"
    )

    order_boosted, _ = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=dense_index,
        late_reranker=None,
        k=1,
        dense_weight=5.0,
    )
    assert captured_weights[-1] == [1.0, 5.0]
    assert order_boosted[0] == 1, "boosted 5x: the dense-favored chunk overtakes the bm25 favorite"
    assert order_boosted != order_default, "the weight change must be OBSERVABLE in the fused order"


def test_rank_chunks_dense_weight_ignored_without_dense_index() -> None:
    """``dense_weight`` has nothing to weight when there is no dense leg at all -- passing a
    non-default value with ``dense_index=None`` must be silently inert, not an error, mirroring
    `tg find`'s own F1 BM25-only re-run (main.py), which passes ``dense_weight=`` unconditionally
    even on the ``dense_index=None`` degrade path."""
    chunks, bm25_index = _build_chunks()

    baseline, _ = rank_chunks(
        "invoice", chunks, bm25_index=bm25_index, dense_index=None, late_reranker=None
    )
    with_weight, _ = rank_chunks(
        "invoice",
        chunks,
        bm25_index=bm25_index,
        dense_index=None,
        late_reranker=None,
        dense_weight=5.0,
    )
    assert with_weight == baseline == [0]
