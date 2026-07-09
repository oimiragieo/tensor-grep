"""Tests for the late-interaction (MaxSim) rerank stage.

T0-T2 (design doc "T0-T2", docs/plans/design-tensor-grep-late-rerank-2026-07-09.md): pure MaxSim
math plus the ``LateReranker`` contract against an INJECTED stub encoder -- these tests never need
the ``rerank`` extra installed.

T3 (design doc "T3"): the real ONNX encoder behind the extra. Per the failure-archaeology
mock-green trap (a mocked FFI/bridge can pass green while the real thing is dead), the fail-closed
tests below (``TestLateAvailable``, ``TestLoadLateModel``) use monkeypatched imports / garbage
bytes and never need onnxruntime installed to prove the CONTRACT; ``TestRealFetchedModel`` at the
bottom exercises the ACTUAL installed onnxruntime + tokenizers against the ACTUAL fetched
LateOn-Code-edge model -- not a mock -- and skips (rather than fails) when the model has not been
fetched locally (not committed to the repo; CI does not fetch it).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import numpy as np
import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.retrieval_late import (
    LateModel,
    LateReranker,
    LateRerankUnavailableError,
    build_late_encoder,
    default_model_dir,
    late_available,
    load_late_model,
    load_late_reranker,
    maxsim_scores,
    rank_by_maxsim,
)


def test_maxsim_hand_computed_values() -> None:
    # 3 query tokens, D=2, all already unit-length and axis-aligned so every dot product is
    # trivially 0 or 1 by hand. doc_a shares an axis with every query token (perfect match each
    # time); doc_b only matches the middle query token.
    query_matrix = np.array(
        [
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 0.0],
        ],
        dtype=np.float32,
    )
    doc_a = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    doc_b = np.array([[0.0, 1.0], [0.0, 1.0]], dtype=np.float32)

    # doc_a: max_j(q0.d_j)=1 (d0) + max_j(q1.d_j)=1 (d1) + max_j(q2.d_j)=1 (d0) = 3.0
    # doc_b: max_j(q0.d_j)=0        + max_j(q1.d_j)=1 (d0 or d1) + max_j(q2.d_j)=0        = 1.0
    scores = maxsim_scores(query_matrix, [doc_a, doc_b])

    assert scores == pytest.approx([3.0, 1.0])


def test_maxsim_empty_doc_scores_zero() -> None:
    # A doc with zero tokens has nothing to compare against -- must score 0.0, not raise (numpy's
    # max(axis=1) on a zero-size reduction would otherwise blow up deep inside the module).
    query_matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    empty_doc = np.zeros((0, 2), dtype=np.float32)
    real_doc = np.array([[1.0, 0.0]], dtype=np.float32)

    scores = maxsim_scores(query_matrix, [empty_doc, real_doc])

    assert scores == pytest.approx([0.0, 1.0])


def test_maxsim_ties_break_by_ascending_index() -> None:
    # Two equal scores at indices 10 and 3 must resolve ascending by INDEX VALUE, not by position
    # in the input lists -- index 3 is listed second but must still rank before index 10.
    scores = [1.0, 1.0, 2.0]
    indices = [10, 3, 7]

    assert rank_by_maxsim(scores, indices) == [7, 3, 10]


def _stub_encoder(vectors: dict[str, np.ndarray]) -> Callable[[str], np.ndarray]:
    """A deterministic dict-lookup encoder: maps exact text -> a pre-built (T, D) token matrix."""

    def encode(text: str) -> np.ndarray:
        return vectors[text]

    return encode


def test_rerank_returns_permutation_never_drops() -> None:
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "chunk-a": np.array([[1.0, 0.0]], dtype=np.float32),
        "chunk-b": np.array([[0.0, 1.0]], dtype=np.float32),
        "chunk-c": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)
    indices = [42, 7, 100]

    result = reranker.rerank("query", ["chunk-a", "chunk-b", "chunk-c"], indices)

    # A permutation: same multiset of indices, no adds, no drops, no duplicates.
    assert sorted(result) == sorted(indices)
    assert len(result) == len(indices)


def test_rerank_orders_by_maxsim_desc() -> None:
    # chunk "match" (index 9) is parallel to the query -> MaxSim 1.0; chunk "nomatch" (index 5) is
    # orthogonal -> MaxSim 0.0. Indices are given in ASCENDING order (5 before 9), so a
    # passthrough (non-reordering) implementation would wrongly return [5, 9].
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "nomatch": np.array([[0.0, 1.0]], dtype=np.float32),
        "match": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)

    result = reranker.rerank("query", ["nomatch", "match"], [5, 9])

    assert result == [9, 5]


def test_rerank_ties_break_by_ascending_original_index() -> None:
    # Both chunks encode to the SAME vector -> tied MaxSim score. Indices are given
    # out-of-ascending-order (8 before 2), so the tie-break must still resolve to [2, 8] -- ascending
    # by the ORIGINAL index value, not by position in the input lists.
    encoder = _stub_encoder({
        "query": np.array([[1.0, 0.0]], dtype=np.float32),
        "same": np.array([[1.0, 0.0]], dtype=np.float32),
    })
    reranker = LateReranker(encoder)

    result = reranker.rerank("query", ["same", "same"], [8, 2])

    assert result == [2, 8]


def test_rerank_empty_pool_returns_empty() -> None:
    def _unreachable(text: str) -> np.ndarray:
        raise AssertionError("encode must not be called for an empty pool")

    reranker = LateReranker(_unreachable)

    assert reranker.rerank("query", [], []) == []


# -------------------------------------------------------------------------------------------
# T3 -- ONNX encoder behind the `rerank` extra. NO real model in these unit tests (fail-closed
# contract via monkeypatched imports / garbage bytes); see TestRealFetchedModel at the bottom
# for the real-model smoke test.
# -------------------------------------------------------------------------------------------


def test_late_available_false_without_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    available, reason = late_available()
    assert available is False
    assert reason is not None
    assert "onnxruntime not installed" in reason
    assert "tensor-grep[rerank]" in reason


def test_late_available_false_when_tokenizers_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Isolates the tokenizers-missing branch, which is only reached once onnxruntime succeeds.
    monkeypatch.setitem(sys.modules, "tokenizers", None)
    available, reason = late_available()
    assert available is False
    assert reason is not None
    assert "tokenizers not installed" in reason


def test_load_missing_dir_raises_unavailable(tmp_path) -> None:
    missing_dir = tmp_path / "does-not-exist"
    with pytest.raises(LateRerankUnavailableError, match="not fetched"):
        load_late_model(missing_dir)


def test_load_dir_missing_one_required_file_raises_unavailable(tmp_path) -> None:
    # The directory exists but is only PARTIALLY fetched (e.g. an interrupted/manual copy) --
    # must still be treated as "not fetched" (recoverable), not surfaced as a load crash.
    partial_dir = tmp_path / "partial-model"
    partial_dir.mkdir()
    (partial_dir / "model_int8.onnx").write_bytes(b"placeholder")
    (partial_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    # onnx_config.json deliberately absent.

    with pytest.raises(LateRerankUnavailableError, match="not fetched"):
        load_late_model(partial_dir)


def test_load_corrupt_dir_raises_backend_execution_error(tmp_path) -> None:
    corrupt_dir = tmp_path / "corrupt-model"
    corrupt_dir.mkdir()
    (corrupt_dir / "model_int8.onnx").write_bytes(b"not a real onnx model, just garbage bytes")
    (corrupt_dir / "tokenizer.json").write_text("not real tokenizer json {{{", encoding="utf-8")
    (corrupt_dir / "onnx_config.json").write_text("not real config json {{{", encoding="utf-8")

    with pytest.raises(BackendExecutionError):
        load_late_model(corrupt_dir)


class TestDefaultModelDir:
    def test_honors_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("TG_RERANK_MODEL_DIR", str(tmp_path / "custom-model"))
        assert default_model_dir() == tmp_path / "custom-model"

    def test_defaults_under_home_dotdir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TG_RERANK_MODEL_DIR", raising=False)
        result = default_model_dir()
        assert result.parts[-3:] == (".tensor-grep", "models", "LateOn-Code-edge")


class _FakeEncoding:
    def __init__(self, ids: list[int], attention_mask: list[int]) -> None:
        self.ids = ids
        self.attention_mask = attention_mask


class _FakeTokenizer:
    """Duck-typed stand-in for `tokenizers.Tokenizer`: records prefix/truncation calls and
    returns a deterministic fixed-length token-id sequence (independent of the truncation
    length actually passed -- the truncation-length ASSERTION is on `truncation_calls`, not on
    the returned sequence length)."""

    _FIXED_TOKEN_COUNT = 4

    def __init__(self) -> None:
        self.truncation_calls: list[int] = []
        self.encoded_texts: list[str] = []

    def enable_truncation(self, max_length: int) -> None:
        self.truncation_calls.append(max_length)

    def encode(self, text: str) -> _FakeEncoding:
        self.encoded_texts.append(text)
        n = self._FIXED_TOKEN_COUNT
        return _FakeEncoding(ids=list(range(n)), attention_mask=[1] * n)


class _FakeSession:
    """Duck-typed stand-in for `onnxruntime.InferenceSession`: returns a deterministic,
    non-uniform (1, T, D) float32 array shaped from the fed `input_ids`."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.run_calls: list[dict[str, np.ndarray]] = []

    def run(self, output_names: list[str] | None, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.run_calls.append(feed)
        t = feed["input_ids"].shape[1]
        raw = np.arange(1, t * self.dim + 1, dtype=np.float32).reshape(1, t, self.dim)
        return [raw]


def _fake_late_model(
    *, dim: int = 4, query_length: int = 8, document_length: int = 4000
) -> LateModel:
    return LateModel(
        session=_FakeSession(dim=dim),
        tokenizer=_FakeTokenizer(),
        query_prefix="[Q] ",
        document_prefix="[D] ",
        query_length=query_length,
        document_length=document_length,
        embedding_dim=dim,
    )


class TestBuildLateEncoder:
    """Exercises the encode mechanics (prefix routing, the 512-token guard, L2-normalization,
    fail-closed error mapping) against duck-typed fakes -- no real onnxruntime/tokenizers
    package required, so these run in every environment regardless of the `rerank` extra."""

    def test_query_role_uses_query_prefix_and_configured_length(self) -> None:
        model = _fake_late_model(query_length=8, document_length=4000)
        encode = build_late_encoder(model, is_query=True)

        encode("hello")

        assert model.tokenizer.encoded_texts == ["[Q] hello"]
        assert model.tokenizer.truncation_calls == [8]  # min(8, 512) == 8

    def test_document_role_uses_document_prefix_and_512_token_guard(self) -> None:
        # document_length=4000 exceeds the 512-token safety guard -- the EFFECTIVE truncation
        # length passed to the tokenizer must be capped at 512, not the raw configured value.
        model = _fake_late_model(query_length=8, document_length=4000)
        encode = build_late_encoder(model, is_query=False)

        encode("world")

        assert model.tokenizer.encoded_texts == ["[D] world"]
        assert model.tokenizer.truncation_calls == [512]

    def test_output_is_l2_normalized_per_token_row(self) -> None:
        model = _fake_late_model(dim=4)
        encode = build_late_encoder(model, is_query=True)

        matrix = encode("hello")

        assert matrix.shape == (4, 4)  # _FakeTokenizer always returns 4 tokens
        row_norms = np.linalg.norm(matrix, axis=1)
        np.testing.assert_allclose(row_norms, 1.0, rtol=1e-5)

    def test_encode_raw_exception_becomes_backend_execution_error(self) -> None:
        class _RaisingSession:
            def run(self, output_names: list[str] | None, feed: dict[str, np.ndarray]) -> None:
                raise RuntimeError("onnxruntime exploded")

        model = _fake_late_model()
        model.session = _RaisingSession()
        encode = build_late_encoder(model, is_query=True)

        with pytest.raises(BackendExecutionError, match="onnxruntime exploded"):
            encode("hello")

    def test_encode_malformed_output_dim_raises_unavailable(self) -> None:
        class _WrongDimSession:
            def run(
                self, output_names: list[str] | None, feed: dict[str, np.ndarray]
            ) -> list[np.ndarray]:
                t = feed["input_ids"].shape[1]
                return [np.ones((1, t, 999), dtype=np.float32)]  # wrong embedding dim

        model = _fake_late_model(dim=4)
        model.session = _WrongDimSession()
        encode = build_late_encoder(model, is_query=True)

        with pytest.raises(LateRerankUnavailableError, match="malformed embedding shape"):
            encode("hello")


def _real_late_model_dir():
    candidate = default_model_dir()
    return candidate if candidate.is_dir() else None


@pytest.mark.skipif(
    _real_late_model_dir() is None,
    reason="requires the real fetched lightonai/LateOn-Code-edge model (local dev only, not "
    "committed; CI does not fetch it -- run `python -m tensor_grep.core.retrieval_late --fetch` "
    "first; this test exercises the ACTUAL model when present)",
)
class TestRealFetchedModel:
    """Exercises the ACTUAL installed onnxruntime + tokenizers against the ACTUAL fetched
    LateOn-Code-edge model -- not a mock."""

    def test_real_model_loads_with_expected_dimensionality(self) -> None:
        model = load_late_model(_real_late_model_dir())
        assert model.embedding_dim == 48
        assert model.query_prefix == "[Q] "
        assert model.document_prefix == "[D] "

    def test_real_model_encode_and_maxsim_smoke(self) -> None:
        model = load_late_model(_real_late_model_dir())
        encode_query = build_late_encoder(model, is_query=True)
        encode_doc = build_late_encoder(model, is_query=False)

        query_matrix = encode_query("how do I open a file for reading")
        doc_matrix = encode_doc("def open_file(path):\n    return open(path, 'r')")

        assert query_matrix.ndim == 2
        assert query_matrix.shape[1] == model.embedding_dim
        assert doc_matrix.ndim == 2
        assert doc_matrix.shape[1] == model.embedding_dim

        scores = maxsim_scores(query_matrix, [doc_matrix])
        assert len(scores) == 1
        assert scores[0] > 0.0

    def test_load_late_reranker_produces_a_permutation(self) -> None:
        reranker = load_late_reranker(_real_late_model_dir())
        indices = [11, 4, 7]
        chunks = [
            "def authenticate_user(username, password):\n    return check(username, password)\n",
            "def close_connection(conn):\n    conn.dispose()\n",
            "def open_file(path):\n    return open(path, 'r')\n",
        ]

        result = reranker.rerank("how do I open a file", chunks, indices)

        assert sorted(result) == sorted(indices)
