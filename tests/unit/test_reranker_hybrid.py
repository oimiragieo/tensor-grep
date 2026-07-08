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


# --- PR-S2: channelized RRF (opt-in PATH channel, TG_RRF_CHANNELS=1) ---------------------------
# DEFAULT-OFF: every test above this marker never sets TG_RRF_CHANNELS and continues to pass
# UNMODIFIED after this change -- that is the byte-identical no-regression proof for the default
# (flag-unset) path.


def _path_channel_scenario() -> tuple[SearchResult, Bm25Index]:
    # Both chunks have IDENTICAL text -> BM25 tie for any query term they share. Only the second
    # chunk's FILENAME ("invoice_parser.py") overlaps the query token "invoice"; the first
    # chunk's filename ("other_helper.py") does not.
    chunks = [
        Chunk(file_path="other_helper.py", start_line=1, end_line=1, text="shared_content"),
        Chunk(file_path="invoice_parser.py", start_line=1, end_line=1, text="shared_content"),
    ]
    bm25_index = Bm25Index(chunks)
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="shared_content", file="other_helper.py"),
            MatchLine(line_number=1, text="shared_content", file="invoice_parser.py"),
        ],
        total_matches=2,
    )
    return result, bm25_index


def test_default_flag_unset_rerank_hybrid_byte_identical_to_bm25_only_rrf(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With TG_RRF_CHANNELS unset (the default), a BM25 score TIE breaks by ascending chunk
    index -- the path channel must NOT be consulted at all."""
    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    result, bm25_index = _path_channel_scenario()

    out = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)

    assert [m.file for m in out.matches] == ["other_helper.py", "invoice_parser.py"]


def test_path_channel_boosts_filename_match_under_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With TG_RRF_CHANNELS=1, the chunk whose FILENAME matches a query token ("invoice" ->
    invoice_parser.py) must outrank an equal-BM25-score chunk whose filename does not match."""
    result, bm25_index = _path_channel_scenario()

    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    baseline = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)
    assert [m.file for m in baseline.matches] == ["other_helper.py", "invoice_parser.py"]

    monkeypatch.setenv("TG_RRF_CHANNELS", "1")
    boosted = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)
    assert [m.file for m in boosted.matches] == ["invoice_parser.py", "other_helper.py"]


def test_path_channel_flag_requires_exact_value_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Any value other than the literal "1" leaves the flag OFF (mirrors the rest of the tg
    TG_* env-var convention: fail closed on ambiguous truthy strings)."""
    result, bm25_index = _path_channel_scenario()

    for off_value in ("0", "true", "TRUE", "yes", ""):
        monkeypatch.setenv("TG_RRF_CHANNELS", off_value)
        out = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)
        assert [m.file for m in out.matches] == ["other_helper.py", "invoice_parser.py"]


def test_hybrid_deterministic_repeated_calls_with_channels_enabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("TG_RRF_CHANNELS", "1")
    result, bm25_index = _path_channel_scenario()

    first = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)
    second = rerank_hybrid(result, "invoice shared", [], bm25_index=bm25_index)

    assert [m.file for m in first.matches] == [m.file for m in second.matches]


def test_path_channel_no_filename_overlap_falls_back_to_bm25_dense_only(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When TG_RRF_CHANNELS=1 but no filename overlaps the query at all, the path leg is simply
    omitted (not added as an empty/no-op leg) and the fused order matches the flag-off baseline."""
    monkeypatch.setenv("TG_RRF_CHANNELS", "1")
    result, bm25_index, dense_index = _build_scenario()

    hybrid_flag_on = rerank_hybrid(
        result, "invoice", [], bm25_index=bm25_index, dense_index=dense_index
    )

    monkeypatch.delenv("TG_RRF_CHANNELS", raising=False)
    hybrid_flag_off = rerank_hybrid(
        result, "invoice", [], bm25_index=bm25_index, dense_index=dense_index
    )

    assert [m.file for m in hybrid_flag_on.matches] == [m.file for m in hybrid_flag_off.matches]
