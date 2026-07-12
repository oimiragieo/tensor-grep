"""Tests for the RRF-fused (BM25 + dense) hybrid re-rank (`tg search --semantic`, Path B Stage 1).

The real model2vec/potion-code-16M dense leg is exercised end-to-end in
``test_retrieval_dense.py``; these tests use a small deterministic fake dense encoder so the
FUSION LOGIC (not embedding quality) is pinned exactly and the suite never depends on the model
being fetched.
"""

from __future__ import annotations

from pathlib import Path

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


# --- #128d (backlog cluster-1 P0-CORRECTNESS, MED-1): the hybrid twin of the total corpus-chunk
# cap tests in test_reranker.py. `rerank_hybrid`'s `bm25_index is None` build loop is "currently
# unreached from production after #527" (the semantic path always passes a prebuilt bm25_index --
# see cli/main.py's `_apply_semantic_rerank`), but the fix covers it too per the audit's explicit
# instruction: "any future caller re-opens the hole" otherwise.


def _write_three_line_file(tmp_path: Path, name: str) -> Path:
    """A 3-line file that produces exactly 3 chunks at chunk_size=1, overlap=0 (one chunk per
    line) -- gives fully deterministic, hand-countable chunk totals for the cap tests below."""
    path = tmp_path / name
    path.write_text(f"{name}_line_a\n{name}_line_b\n{name}_line_c\n", encoding="utf-8")
    return path


def _patch_counting_chunk_file(monkeypatch):  # type: ignore[no-untyped-def]
    """Wrap reranker.py's bound `chunk_file` name with a call-counting proxy, returning the list
    of paths it was invoked with (in call order)."""
    from tensor_grep.core import reranker as reranker_module
    from tensor_grep.core.retrieval_chunker import chunk_file as real_chunk_file

    calls: list[str] = []

    def _counting_chunk_file(path: str, **kwargs: object):  # type: ignore[no-untyped-def]
        calls.append(str(path))
        return real_chunk_file(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reranker_module, "chunk_file", _counting_chunk_file)
    return calls


def test_rerank_hybrid_corpus_cap_bounds_chunking_and_sets_reason(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The `bm25_index is None` build loop is bounded by the same total cap as rerank_by_bm25's,
    and never drops a match."""
    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "4")
    calls = _patch_counting_chunk_file(monkeypatch)

    files = [_write_three_line_file(tmp_path, f"f{i}.py") for i in range(3)]  # 3 chunks each -> 9
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="x", file=str(f)) for f in files],
        total_matches=3,
    )

    out = rerank_hybrid(result, "anything", [str(f) for f in files], chunk_size=1, overlap=0)

    assert calls == [str(files[0]), str(files[1])], f"unexpected chunking calls: {calls}"
    assert out.rank_fallback_reason is not None
    assert "corpus cap" in out.rank_fallback_reason
    assert len(out.matches) == len(result.matches) == 3
    assert {m.file for m in out.matches} == {m.file for m in result.matches}


def test_rerank_hybrid_small_corpus_under_cap_leaves_reason_none(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """A matched set comfortably under the (default) cap is unaffected: rank_fallback_reason stays
    None and every file is chunked -- byte-identical to the pre-cap behavior."""
    monkeypatch.delenv("TG_RANK_CORPUS_CHUNK_CAP", raising=False)
    calls = _patch_counting_chunk_file(monkeypatch)

    files = [_write_three_line_file(tmp_path, f"f{i}.py") for i in range(2)]
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="x", file=str(f)) for f in files],
        total_matches=2,
    )

    out = rerank_hybrid(result, "anything", [str(f) for f in files], chunk_size=1, overlap=0)

    assert calls == [str(files[0]), str(files[1])]
    assert out.rank_fallback_reason is None
    assert len(out.matches) == 2


def test_rerank_hybrid_corpus_cap_env_tunable(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Same override-respected proof as rerank_by_bm25's twin: the default cap never trips this
    small corpus, but TG_RANK_CORPUS_CHUNK_CAP set below the corpus total does."""
    files = [_write_three_line_file(tmp_path, f"f{i}.py") for i in range(2)]  # 6 chunks total
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="x", file=str(f)) for f in files],
        total_matches=2,
    )

    monkeypatch.delenv("TG_RANK_CORPUS_CHUNK_CAP", raising=False)
    default_out = rerank_hybrid(
        result, "anything", [str(f) for f in files], chunk_size=1, overlap=0
    )
    assert default_out.rank_fallback_reason is None

    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "2")
    capped_out = rerank_hybrid(result, "anything", [str(f) for f in files], chunk_size=1, overlap=0)
    assert capped_out.rank_fallback_reason is not None
    assert "2 chunks" in capped_out.rank_fallback_reason
    assert len(capped_out.matches) == 2


def test_rerank_hybrid_corpus_cap_appends_to_existing_and_late_rerank_reasons(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The 3-way reason combination (pre-existing + corpus-cap + late-rerank) preserves every
    source -- generalizing the existing 2-way late-rerank append test without regressing it."""
    from tensor_grep.core.retrieval_late import LateReranker, LateRerankUnavailableError

    monkeypatch.setenv("TG_RANK_CORPUS_CHUNK_CAP", "1")
    files = [_write_three_line_file(tmp_path, f"f{i}.py") for i in range(2)]
    result = SearchResult(
        matches=[MatchLine(line_number=1, text="x", file=str(f)) for f in files],
        total_matches=2,
        rank_fallback_reason="pre-existing upstream reason",
    )

    def _raising_encode(text: str) -> np.ndarray:
        raise LateRerankUnavailableError("late rerank unavailable: model not fetched")

    # Query MUST match a real token in the corpus ("line", present in every _write_three_line_file
    # chunk) so the BM25 leg's ranking -- and therefore the late-rerank pool `head` -- is non-empty.
    # Bm25Index.query() excludes zero-score chunks, and LateReranker.rerank() short-circuits an
    # EMPTY pool without ever invoking `encode` at all -- an unmatched query like "anything" would
    # never reach `_raising_encode`, silently defeating this test's whole premise.
    out = rerank_hybrid(
        result,
        "line",
        [str(f) for f in files],
        chunk_size=1,
        overlap=0,
        late_reranker=LateReranker(encode=_raising_encode),
    )

    assert out.rank_fallback_reason is not None
    assert "pre-existing upstream reason" in out.rank_fallback_reason
    assert "corpus cap" in out.rank_fallback_reason
    assert "model not fetched" in out.rank_fallback_reason
    assert len(out.matches) == 2  # matches still never dropped across all three degrade sources
