"""Tests for BM25 re-ranking of an existing SearchResult."""

import time
from pathlib import Path

import numpy as np
import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.reranker import rerank_by_bm25, rerank_hybrid
from tensor_grep.core.result import MatchLine, SearchResult
from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_late import LateReranker, LateRerankUnavailableError


def test_rerank_orders_by_bm25_score(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def parse_invoice():\n    return total\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("def helper():\n    return 0\n", encoding="utf-8")
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="def helper():", file=str(f2)),
            MatchLine(line_number=1, text="def parse_invoice():", file=str(f1)),
        ],
        total_matches=2,
    )

    out = rerank_by_bm25(result, "parse invoice", [str(f1), str(f2)])

    assert out.matches[0].file == str(f1)  # the parse_invoice match ranks first
    assert out.total_matches == 2  # non-match fields preserved


def test_rerank_unmatched_files_sink_to_end(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def parse_invoice(): pass\n", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("xyz = 1\n", encoding="utf-8")
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="def parse_invoice(): pass", file=str(f1)),
            MatchLine(line_number=1, text="xyz = 1", file=str(f2)),
        ],
        total_matches=2,
    )

    out = rerank_by_bm25(result, "invoice", [str(f1), str(f2)])

    assert out.matches[0].file == str(f1)
    assert out.matches[1].file == str(f2)  # zero-score match sinks to the end


def test_rerank_empty_result_is_safe() -> None:
    out = rerank_by_bm25(SearchResult(), "anything", [])
    assert out.matches == []


def test_rerank_preserves_other_fields(tmp_path: Path) -> None:
    f1 = tmp_path / "a.py"
    f1.write_text("def foo(): pass\n", encoding="utf-8")
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="def foo(): pass", file=str(f1))],
        total_matches=1,
        routing_backend="cpu",
    )

    out = rerank_by_bm25(result, "foo", [str(f1)])

    assert out.routing_backend == "cpu"
    assert out.total_matches == 1


# --- T5/T6: the late-interaction (MaxSim) rerank seam in `rerank_hybrid` --------------------
# (design doc docs/plans/design-tensor-grep-late-rerank-2026-07-09.md, "The seam" + "Fail-closed
# contract"). These tests inject a real `LateReranker` (core/retrieval_late.py, T0-T2) wired with
# a deterministic stub `encode` callable -- no ONNX model, no `rerank` extra required.


class _FixedBm25Index:
    """A minimal ``Bm25Index`` stand-in: a hardcoded query ranking + a chunk list, so a
    scenario's BM25/RRF order is fully predictable regardless of the real scorer's internals
    (mirrors ``_FixedVectorModel`` in test_reranker_hybrid.py, which stubs the dense leg the same
    way)."""

    def __init__(self, chunks: list[Chunk], ranking: list[tuple[int, float]]) -> None:
        self.chunks = chunks
        self._ranking = ranking

    def query(self, query: str, *, top_k: int = 10) -> list[tuple[int, float]]:
        del query  # the stub ignores the query text -- the ranking is hardcoded per scenario
        return self._ranking[:top_k]


def _four_chunk_scenario() -> tuple[SearchResult, _FixedBm25Index]:
    """4 chunks, a descending BM25 ranking [0, 1, 2, 3] (single leg -> RRF preserves it exactly),
    and matches supplied in a SCRAMBLED order so a passing test proves the sort actually ran."""
    chunks = [
        Chunk(file_path="c0.py", start_line=1, end_line=1, text="chunk zero"),
        Chunk(file_path="c1.py", start_line=1, end_line=1, text="chunk one"),
        Chunk(file_path="c2.py", start_line=1, end_line=1, text="chunk two"),
        Chunk(file_path="c3.py", start_line=1, end_line=1, text="chunk three"),
    ]
    bm25_index = _FixedBm25Index(chunks, [(0, 4.0), (1, 3.0), (2, 2.0), (3, 1.0)])
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="chunk three", file="c3.py"),
            MatchLine(line_number=1, text="chunk one", file="c1.py"),
            MatchLine(line_number=1, text="chunk zero", file="c0.py"),
            MatchLine(line_number=1, text="chunk two", file="c2.py"),
        ],
        total_matches=4,
    )
    return result, bm25_index


def test_late_reranker_none_is_byte_identical() -> None:
    """The zero-risk-additive proof (T5): `late_reranker=None` (the default) must be
    byte-identical to calling `rerank_hybrid` without the kwarg at all -- adding the parameter
    changes nothing about today's behavior."""
    result, bm25_index = _four_chunk_scenario()

    without_kwarg = rerank_hybrid(result, "q", [], bm25_index=bm25_index)
    with_explicit_none = rerank_hybrid(result, "q", [], bm25_index=bm25_index, late_reranker=None)

    assert with_explicit_none == without_kwarg
    assert with_explicit_none.rank_fallback_reason is None


def test_late_reranker_reorders_head_only_tail_stable(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T5 "The seam": the late reranker reorders ONLY the head (size TG_RERANK_POOL_K) of the
    RRF-fused pool; the tail keeps its RRF order untouched."""
    monkeypatch.setenv("TG_RERANK_POOL_K", "2")
    result, bm25_index = _four_chunk_scenario()

    baseline = rerank_hybrid(result, "q", [], bm25_index=bm25_index)
    assert [m.file for m in baseline.matches] == ["c0.py", "c1.py", "c2.py", "c3.py"]

    # Deterministic stub: "chunk one" scores higher against the query than "chunk zero" ->
    # MaxSim reverses the [0, 1] head to [1, 0]. Only chunks 0/1 are ever encoded (the head at
    # pool_k=2), so the stub does not need entries for chunks 2/3.
    vectors = {"q": [1.0, 0.0], "chunk zero": [0.0, 1.0], "chunk one": [0.7, 0.7]}

    def _encode(text: str) -> np.ndarray:
        return np.array([vectors[text]], dtype=np.float32)

    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_encode)
    )

    assert [m.file for m in out.matches[:2]] == ["c1.py", "c0.py"]  # head: reordered
    assert [m.file for m in out.matches[2:]] == ["c2.py", "c3.py"]  # tail: untouched RRF order
    assert out.rank_fallback_reason is None  # order changed -> reason stays untouched (T6 XOR)


def test_late_rerank_same_match_membership() -> None:
    """T5: the late stage never adds or drops a match -- only reorders (design doc "The seam":
    "same matches, same membership, same JSON shape")."""
    chunks = [
        Chunk(file_path="c0.py", start_line=1, end_line=1, text="chunk zero"),
        Chunk(file_path="c1.py", start_line=1, end_line=1, text="chunk one"),
        Chunk(file_path="c2.py", start_line=1, end_line=1, text="chunk two"),
    ]
    bm25_index = _FixedBm25Index(chunks, [(0, 3.0), (1, 2.0), (2, 1.0)])
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="chunk zero", file="c0.py"),
            MatchLine(line_number=1, text="chunk one", file="c1.py"),
            MatchLine(line_number=1, text="chunk two", file="c2.py"),
        ],
        total_matches=3,
    )

    def _encode(text: str) -> np.ndarray:
        # Arbitrary but deterministic -- this test only cares about membership, not order.
        return np.array([[float(len(text)), 1.0]], dtype=np.float32)

    baseline = rerank_hybrid(result, "q", [], bm25_index=bm25_index)
    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_encode)
    )

    assert {m.file for m in out.matches} == {m.file for m in baseline.matches}
    assert len(out.matches) == len(baseline.matches) == 3
    assert out.total_matches == baseline.total_matches == 3


def test_late_reranker_shape_mismatch_degrades_to_rrf_order_with_reason() -> None:
    """T6 fail-closed contract: a RECOVERABLE `LateRerankUnavailableError` raised from the
    injected encoder (e.g. a malformed embedding shape) must degrade to the plain RRF order,
    never crash, and set `rank_fallback_reason` (the other side of the bidirectional XOR)."""
    result, bm25_index = _four_chunk_scenario()
    baseline = rerank_hybrid(result, "q", [], bm25_index=bm25_index)

    def _raising_encode(text: str) -> np.ndarray:
        raise LateRerankUnavailableError(
            "late rerank unavailable: malformed embedding shape (test stub)"
        )

    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_raising_encode)
    )

    assert [m.file for m in out.matches] == [m.file for m in baseline.matches]
    assert out.rank_fallback_reason is not None
    assert "malformed embedding shape" in out.rank_fallback_reason


def test_late_reranker_budget_exceeded_degrades_to_rrf_order_with_reason(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T6 fail-closed contract: exceeding TG_RERANK_BUDGET_MS degrades to the plain RRF order
    (the reorder is computed but DISCARDED, never applied) and sets `rank_fallback_reason`."""
    monkeypatch.setenv("TG_RERANK_BUDGET_MS", "1")
    result, bm25_index = _four_chunk_scenario()
    baseline = rerank_hybrid(result, "q", [], bm25_index=bm25_index)

    def _slow_encode(text: str) -> np.ndarray:
        time.sleep(0.05)  # 50ms, comfortably over the 1ms budget
        return np.array([[1.0, 0.0]], dtype=np.float32)

    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_slow_encode)
    )

    assert [m.file for m in out.matches] == [m.file for m in baseline.matches]
    assert out.rank_fallback_reason is not None
    assert "budget exceeded" in out.rank_fallback_reason


def test_late_rerank_appends_to_existing_fallback_reason() -> None:
    """T6: a late-stage degrade must APPEND to (never clobber) a fallback reason the caller
    already set (e.g. the dense leg's) -- both signals must survive on the returned envelope."""
    result, bm25_index = _four_chunk_scenario()
    result.rank_fallback_reason = "semantic ranking unavailable: model2vec not installed"

    def _raising_encode(text: str) -> np.ndarray:
        raise LateRerankUnavailableError("late rerank unavailable: model not fetched")

    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_raising_encode)
    )

    assert out.rank_fallback_reason is not None
    assert "model2vec not installed" in out.rank_fallback_reason
    assert "model not fetched" in out.rank_fallback_reason


def test_late_reranker_hung_encoder_degrades_within_budget_not_blocked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A3 real wall-clock deadline (external audit 2026-07-11): a genuinely HUNG encoder must NOT
    block `tg search --rank` indefinitely. The old post-hoc `elapsed > budget` check could only
    DISCARD a rerank that had already returned; a wedged encoder never returns, so it hung forever.
    The daemon-thread `join(budget)` bounds it: the call returns near the budget and degrades to the
    plain RRF order, never after the (here 10s) encode."""
    monkeypatch.setenv("TG_RERANK_BUDGET_MS", "100")
    result, bm25_index = _four_chunk_scenario()
    baseline = rerank_hybrid(result, "q", [], bm25_index=bm25_index)

    def _hung_encode(text: str) -> np.ndarray:
        time.sleep(10.0)  # simulate a wedged encoder; the join(0.1s) MUST abandon it
        return np.array([[1.0, 0.0]], dtype=np.float32)

    start = time.perf_counter()
    out = rerank_hybrid(
        result, "q", [], bm25_index=bm25_index, late_reranker=LateReranker(encode=_hung_encode)
    )
    elapsed = time.perf_counter() - start

    # The pre-A3 code would have blocked ~10s (per hung encode); the deadline must bound it well
    # under that. Generous margin for Windows thread-spawn contention, still far below 10s.
    assert elapsed < 4.0, f"late rerank ignored the wall-clock deadline (took {elapsed:.1f}s)"
    assert [m.file for m in out.matches] == [m.file for m in baseline.matches]
    assert out.rank_fallback_reason is not None
    assert "budget exceeded" in out.rank_fallback_reason


def test_late_reranker_backend_execution_error_propagates_not_degrades() -> None:
    """A3 Fail-Closed Contract ACROSS the daemon-thread boundary: a genuine BackendExecutionError
    from the encoder (a real encode-time fault) must PROPAGATE to the caller, never be silently
    degraded into a plausible-but-wrong ranking. Only the RECOVERABLE LateRerankUnavailableError
    degrades; every other exception is re-raised on the worker thread's behalf."""
    result, bm25_index = _four_chunk_scenario()

    def _faulting_encode(text: str) -> np.ndarray:
        raise BackendExecutionError("native encode fault (test stub)")

    with pytest.raises(BackendExecutionError, match="native encode fault"):
        rerank_hybrid(
            result,
            "q",
            [],
            bm25_index=bm25_index,
            late_reranker=LateReranker(encode=_faulting_encode),
        )


def test_late_reranker_base_exception_user_abort_propagates_not_swallowed() -> None:
    """A3 hardening: a BaseException (a KeyboardInterrupt user-abort / SystemExit) raised on the
    worker thread must PROPAGATE, never be silently swallowed into an RRF degrade -- and the empty
    result holder must never IndexError. Only LateRerankUnavailableError degrades; a Ctrl-C aborts."""
    result, bm25_index = _four_chunk_scenario()

    def _aborting_encode(text: str) -> np.ndarray:
        raise KeyboardInterrupt("user abort (test stub)")

    with pytest.raises(KeyboardInterrupt):
        rerank_hybrid(
            result,
            "q",
            [],
            bm25_index=bm25_index,
            late_reranker=LateReranker(encode=_aborting_encode),
        )
