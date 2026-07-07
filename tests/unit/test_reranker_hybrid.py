"""Tests for the RRF-fused (BM25 + dense) hybrid re-rank (`tg search --semantic`, Path B Stage 1).

The real model2vec/potion-code-16M dense leg is exercised end-to-end in
``test_retrieval_dense.py``; these tests use a small deterministic fake dense encoder so the
FUSION LOGIC (not embedding quality) is pinned exactly and the suite never depends on the model
being fetched.
"""

from __future__ import annotations

import numpy as np

from tensor_grep.core.reranker import rerank_by_bm25, rerank_hybrid
from tensor_grep.core.result import MatchLine, SearchResult
from tensor_grep.core.retrieval_bm25 import Bm25Index
from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_dense import DenseIndex


class _FixedVectorModel:
    """Deterministic stand-in dense encoder: maps each EXACT input string to a hand-picked
    vector, so a scenario's dense-leg cosine ranking is fully predictable."""

    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self._vectors_by_text = vectors_by_text

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.array([self._vectors_by_text[t] for t in texts], dtype=np.float32)


def _build_scenario() -> tuple[SearchResult, Bm25Index, DenseIndex]:
    # f1 ("parse_invoice") is the ONLY chunk the BM25 leg matches for query "invoice". The dense
    # leg is crafted to disagree: f3 is the closest cosine match, then f2, then f1 (orthogonal) --
    # deliberately the opposite emphasis, so fusing the two legs is observable in the output.
    chunks = [
        Chunk(file_path="f1.py", start_line=1, end_line=1, text="parse_invoice"),
        Chunk(file_path="f2.py", start_line=1, end_line=1, text="helper_one"),
        Chunk(file_path="f3.py", start_line=1, end_line=1, text="helper_two"),
    ]
    bm25_index = Bm25Index(chunks)
    dense_model = _FixedVectorModel({
        "parse_invoice": [0.0, 1.0],
        "helper_one": [1.0, 1.0],
        "helper_two": [1.0, 0.0],
        "invoice": [1.0, 0.0],
    })
    dense_index = DenseIndex(chunks, dense_model)
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="parse_invoice", file="f1.py"),
            MatchLine(line_number=1, text="helper_one", file="f2.py"),
            MatchLine(line_number=1, text="helper_two", file="f3.py"),
        ],
        total_matches=3,
    )
    return result, bm25_index, dense_index


def test_hybrid_same_matches_different_order_from_bm25_only() -> None:
    result, bm25_index, dense_index = _build_scenario()

    bm25_only = rerank_by_bm25(result, "invoice", [], index=bm25_index)
    hybrid = rerank_hybrid(result, "invoice", [], bm25_index=bm25_index, dense_index=dense_index)

    # SAME set of matches...
    assert {m.file for m in bm25_only.matches} == {m.file for m in hybrid.matches}
    assert len(bm25_only.matches) == len(hybrid.matches) == 3
    # ...but the dense leg's disagreement changes the f2/f3 relative order.
    assert [m.file for m in bm25_only.matches] == ["f1.py", "f2.py", "f3.py"]
    assert [m.file for m in hybrid.matches] == ["f1.py", "f3.py", "f2.py"]


def test_hybrid_without_dense_index_matches_bm25_only_order() -> None:
    """Fail-closed contract: dense_index=None must produce the SAME order as plain BM25 -- this
    is exactly what the CLI relies on when the dense leg is unavailable (extra absent / model not
    fetched / shape-mismatch degrade)."""
    result, bm25_index, _dense_index = _build_scenario()

    bm25_only = rerank_by_bm25(result, "invoice", [], index=bm25_index)
    hybrid_no_dense = rerank_hybrid(result, "invoice", [], bm25_index=bm25_index, dense_index=None)

    assert [m.file for m in hybrid_no_dense.matches] == [m.file for m in bm25_only.matches]


def test_hybrid_empty_result_is_safe() -> None:
    out = rerank_hybrid(SearchResult(), "anything", [])
    assert out.matches == []


def test_hybrid_preserves_other_fields() -> None:
    result, bm25_index, dense_index = _build_scenario()
    result.routing_backend = "cpu"

    out = rerank_hybrid(result, "invoice", [], bm25_index=bm25_index, dense_index=dense_index)

    assert out.routing_backend == "cpu"
    assert out.total_matches == 3
