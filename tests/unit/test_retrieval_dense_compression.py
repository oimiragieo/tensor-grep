"""Tests for the dense-leg compression levers (int8 scalar quantization, binary+int8-rescore,
post-hoc dim-truncation) -- tensor-grep-semantic-search-campaign, dense-leg compression.

Every lever is opt-in via :class:`~tensor_grep.core.retrieval_dense.DenseCompressionConfig`,
default OFF (``DenseCompressionConfig()`` is a no-op). This file proves:

1. The no-op default changes nothing (existing callers, and existing tests in
   ``test_retrieval_dense.py``, are unaffected).
2. Config validation is loud/fail-closed (bad env value, non-positive truncate_dims/
   rescore_candidates, truncate_dims >= the model's real dimensionality).
3. Each compression mode is internally consistent (round-trip quality bound for int8,
   Hamming-distance-zero-for-identical-vectors for binary, dimensionality for truncate) and
   fully deterministic (repeat queries produce byte-identical rankings).
4. The fail-closed dim-mismatch contract from the original ``DenseIndex.query`` still holds under
   every compression mode.
5. ``index_nbytes`` reports a real, measured footprint ordering (int8 < fp32; truncated fp32 <
   full fp32), not an assumed theoretical multiplier.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pytest

from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_dense import (
    DenseCompressionConfig,
    DenseIndex,
    DenseQuantizationMode,
    DenseUnavailableError,
    _hamming_distances,
    _pack_binary,
    _quantize_int8,
    _truncate_and_renormalize,
)


class _DeterministicModel:
    """A tiny duck-typed encoder over a fixed vocabulary of hand-picked unit-ish vectors, so every
    test in this file is fully deterministic and independent of the real (large, non-committed)
    potion-code-16M model."""

    dim = 8

    # Each text maps to a fixed raw (pre-normalization) vector. Unknown text -> a zero vector
    # (edge case some tests exercise deliberately).
    _VOCAB: ClassVar[dict[str, list[float]]] = {
        "alpha": [3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        "beta": [0.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0],
        "gamma": [0.0, 0.0, 3.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        "near_alpha": [2.7, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    }

    def encode(self, texts: list[str]) -> np.ndarray:
        rows = [self._VOCAB.get(text, [0.0] * self.dim) for text in texts]
        return np.asarray(rows, dtype=np.float32)


def _make_chunks(names: list[str]) -> list[Chunk]:
    return [Chunk(file_path=f"{name}.py", start_line=1, end_line=1, text=name) for name in names]


# ---------------------------------------------------------------------------------------
# 1. No-op default -- byte-identical to the pre-compression path.
# ---------------------------------------------------------------------------------------


class TestCompressionDefaultIsNoop:
    def test_default_config_is_noop(self) -> None:
        config = DenseCompressionConfig()
        assert config.is_noop is True
        assert config.quantization == DenseQuantizationMode.NONE
        assert config.truncate_dims is None

    def test_omitting_compression_kwarg_matches_explicit_default(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        implicit = DenseIndex(chunks, _DeterministicModel())
        explicit = DenseIndex(chunks, _DeterministicModel(), compression=DenseCompressionConfig())

        assert implicit.dim == explicit.dim
        assert implicit.query("alpha") == explicit.query("alpha")
        assert implicit.query("near_alpha") == explicit.query("near_alpha")

    def test_none_mode_ranking_unchanged_from_baseline_matmul(self) -> None:
        # Hand-verify against a direct L2-normalize + dot product, independent of DenseIndex
        # internals, so this test would fail if compression plumbing silently altered the
        # NONE-mode scoring path.
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        index = DenseIndex(chunks, _DeterministicModel())
        ranked = index.query("near_alpha", top_k=3)

        model = _DeterministicModel()
        raw = model.encode([c.text for c in chunks])
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        normalized = raw / norms
        query_raw = model.encode(["near_alpha"])[0]
        query_norm = query_raw / np.linalg.norm(query_raw)
        expected_scores = normalized @ query_norm
        expected_ranked = sorted(
            enumerate(expected_scores.tolist()), key=lambda item: (-item[1], item[0])
        )
        assert ranked == expected_ranked


# ---------------------------------------------------------------------------------------
# 2. Config validation -- loud, fail-closed.
# ---------------------------------------------------------------------------------------


class TestCompressionConfigValidation:
    def test_negative_truncate_dims_rejected(self) -> None:
        with pytest.raises(ValueError, match="truncate_dims"):
            DenseCompressionConfig(truncate_dims=0)

    def test_negative_rescore_candidates_rejected(self) -> None:
        with pytest.raises(ValueError, match="rescore_candidates"):
            DenseCompressionConfig(rescore_candidates=0)

    def test_truncate_dims_at_or_above_model_dim_rejected_at_index_build(self) -> None:
        chunks = _make_chunks(["alpha", "beta"])
        config = DenseCompressionConfig(truncate_dims=8)  # == _DeterministicModel.dim
        with pytest.raises(ValueError, match="truncate_dims"):
            DenseIndex(chunks, _DeterministicModel(), compression=config)

    def test_from_env_defaults_to_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TG_SEMANTIC_DENSE_QUANTIZATION", raising=False)
        monkeypatch.delenv("TG_SEMANTIC_DENSE_TRUNCATE_DIMS", raising=False)
        monkeypatch.delenv("TG_SEMANTIC_DENSE_RESCORE_CANDIDATES", raising=False)
        config = DenseCompressionConfig.from_env()
        assert config.is_noop is True

    def test_from_env_reads_int8(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TG_SEMANTIC_DENSE_QUANTIZATION", "int8")
        config = DenseCompressionConfig.from_env()
        assert config.quantization == DenseQuantizationMode.INT8

    def test_from_env_reads_truncate_dims(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TG_SEMANTIC_DENSE_QUANTIZATION", raising=False)
        monkeypatch.setenv("TG_SEMANTIC_DENSE_TRUNCATE_DIMS", "128")
        config = DenseCompressionConfig.from_env()
        assert config.truncate_dims == 128

    def test_from_env_unrecognized_value_raises_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TG_SEMANTIC_DENSE_QUANTIZATION", "banana")
        with pytest.raises(ValueError, match="banana"):
            DenseCompressionConfig.from_env()

    def test_from_env_non_integer_truncate_dims_raises_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TG_SEMANTIC_DENSE_QUANTIZATION", raising=False)
        monkeypatch.setenv("TG_SEMANTIC_DENSE_TRUNCATE_DIMS", "not-a-number")
        with pytest.raises(ValueError, match="TG_SEMANTIC_DENSE_TRUNCATE_DIMS"):
            DenseCompressionConfig.from_env()

    def test_from_env_non_integer_rescore_candidates_raises_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TG_SEMANTIC_DENSE_QUANTIZATION", raising=False)
        monkeypatch.delenv("TG_SEMANTIC_DENSE_TRUNCATE_DIMS", raising=False)
        monkeypatch.setenv("TG_SEMANTIC_DENSE_RESCORE_CANDIDATES", "not-a-number")
        with pytest.raises(ValueError, match="TG_SEMANTIC_DENSE_RESCORE_CANDIDATES"):
            DenseCompressionConfig.from_env()


# ---------------------------------------------------------------------------------------
# 3a. int8 quantization -- round-trip bound + determinism + ranking sanity.
# ---------------------------------------------------------------------------------------


class TestInt8Quantization:
    def test_round_trip_within_half_scale_bound(self) -> None:
        rng = np.random.default_rng(1234)
        matrix = rng.normal(size=(20, 16)).astype(np.float32)
        codes, scale = _quantize_int8(matrix)
        dequantized = codes.astype(np.float32) * scale
        assert np.all(np.abs(matrix - dequantized) <= (scale / 2.0) + 1e-6)

    def test_all_zero_column_round_trips_exactly(self) -> None:
        matrix = np.zeros((5, 4), dtype=np.float32)
        matrix[:, 1] = [1.0, -1.0, 0.5, -0.5, 0.0]
        codes, scale = _quantize_int8(matrix)
        assert not np.any(np.isnan(scale))
        dequantized = codes.astype(np.float32) * scale
        assert np.allclose(dequantized[:, 0], 0.0)
        assert np.allclose(dequantized[:, 2], 0.0)
        assert np.allclose(dequantized[:, 3], 0.0)

    def test_codes_never_overflow_int8_range(self) -> None:
        rng = np.random.default_rng(99)
        matrix = rng.normal(scale=5.0, size=(50, 32)).astype(np.float32)
        codes, _scale = _quantize_int8(matrix)
        assert codes.dtype == np.int8
        assert int(codes.max()) <= 127
        assert int(codes.min()) >= -127

    def test_query_is_deterministic_across_repeated_calls(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.INT8)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        first = index.query("near_alpha")
        second = index.query("near_alpha")
        assert first == second

    def test_ranks_nearest_vector_first(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.INT8)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        ranked = index.query("near_alpha")
        assert ranked[0][0] == 0  # "alpha" chunk index 0 is nearest to "near_alpha"

    def test_dim_mismatch_still_degrades_visibly(self) -> None:
        chunks = _make_chunks(["alpha", "beta"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.INT8)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)

        class _WrongDimModel:
            dim = 3

            def encode(self, texts: list[str]) -> np.ndarray:
                return np.ones((len(texts), 3), dtype=np.float32)

        index.model = _WrongDimModel()
        with pytest.raises(DenseUnavailableError, match="dim mismatch"):
            index.query("alpha")

    def test_index_nbytes_smaller_than_fp32(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma", "near_alpha"])
        fp32_index = DenseIndex(chunks, _DeterministicModel())
        int8_index = DenseIndex(
            chunks,
            _DeterministicModel(),
            compression=DenseCompressionConfig(quantization=DenseQuantizationMode.INT8),
        )
        assert int8_index.index_nbytes < fp32_index.index_nbytes


# ---------------------------------------------------------------------------------------
# 3b. Binary + int8-rescore -- Hamming shortlist correctness + determinism.
# ---------------------------------------------------------------------------------------


class TestBinaryRescore:
    def test_hamming_distance_zero_for_identical_packed_codes(self) -> None:
        matrix = np.array([[1.0, -1.0, 0.5, -0.5]], dtype=np.float32)
        packed = _pack_binary(matrix)
        distances = _hamming_distances(packed, packed[0])
        assert distances.tolist() == [0]

    def test_hamming_distance_counts_differing_sign_bits(self) -> None:
        a = np.array([[1.0, 1.0, 1.0, 1.0]], dtype=np.float32)
        b = np.array([[-1.0, -1.0, 1.0, 1.0]], dtype=np.float32)
        packed_a = _pack_binary(a)
        packed_b = _pack_binary(b)
        distances = _hamming_distances(packed_a, packed_b[0])
        assert distances.tolist() == [2]

    def test_query_is_deterministic_across_repeated_calls(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.BINARY_RESCORE)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        first = index.query("near_alpha")
        second = index.query("near_alpha")
        assert first == second

    def test_ranks_nearest_vector_first(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.BINARY_RESCORE)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        ranked = index.query("near_alpha")
        assert ranked[0][0] == 0

    def test_result_count_bounded_by_rescore_candidates(self) -> None:
        names = [f"chunk{i}" for i in range(20)]
        chunks = _make_chunks(names)

        class _RandomUnitModel:
            dim = 8

            def encode(self, texts: list[str]) -> np.ndarray:
                rng = np.random.default_rng(abs(hash(tuple(texts))) % (2**31))
                return rng.normal(size=(len(texts), 8)).astype(np.float32)

        config = DenseCompressionConfig(
            quantization=DenseQuantizationMode.BINARY_RESCORE, rescore_candidates=5
        )
        index = DenseIndex(chunks, _RandomUnitModel(), compression=config)
        ranked = index.query("anything", top_k=20)
        assert len(ranked) <= 5

    def test_dim_mismatch_still_degrades_visibly(self) -> None:
        chunks = _make_chunks(["alpha", "beta"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.BINARY_RESCORE)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)

        class _WrongDimModel:
            dim = 3

            def encode(self, texts: list[str]) -> np.ndarray:
                return np.ones((len(texts), 3), dtype=np.float32)

        index.model = _WrongDimModel()
        with pytest.raises(DenseUnavailableError, match="dim mismatch"):
            index.query("alpha")

    def test_index_nbytes_much_smaller_than_fp32(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma", "near_alpha"])
        fp32_index = DenseIndex(chunks, _DeterministicModel())
        binary_index = DenseIndex(
            chunks,
            _DeterministicModel(),
            compression=DenseCompressionConfig(quantization=DenseQuantizationMode.BINARY_RESCORE),
        )
        assert binary_index.index_nbytes < fp32_index.index_nbytes


# ---------------------------------------------------------------------------------------
# 3c. Post-hoc dim-truncation -- dimensionality + math-identity + determinism.
# ---------------------------------------------------------------------------------------


class TestTruncation:
    def test_truncate_and_renormalize_matches_manual_computation(self) -> None:
        matrix = np.array([[3.0, 4.0, 0.0, 12.0]], dtype=np.float32)  # norm = 13
        truncated = _truncate_and_renormalize(matrix, 2)  # keep [3, 4], norm=5
        assert truncated.shape == (1, 2)
        np.testing.assert_allclose(truncated, [[0.6, 0.8]], atol=1e-6)

    def test_truncate_commutes_with_prior_l2_normalize(self) -> None:
        # Truncating a RAW vector then normalizing must equal normalizing-then-truncating-then-
        # renormalizing (the module's documented commutativity claim) -- verified directly here,
        # independent of DenseIndex, on a non-trivial random vector.
        rng = np.random.default_rng(7)
        raw = rng.normal(size=(1, 10)).astype(np.float32)

        direct = raw[:, :4] / np.linalg.norm(raw[:, :4], axis=1, keepdims=True)

        normalized_first = raw / np.linalg.norm(raw, axis=1, keepdims=True)
        via_normalize_first = _truncate_and_renormalize(normalized_first, 4)

        np.testing.assert_allclose(direct, via_normalize_first, atol=1e-6)

    def test_index_dim_reflects_truncation(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(truncate_dims=4)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        assert index.dim == 4

    def test_query_is_deterministic_across_repeated_calls(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(truncate_dims=4)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        first = index.query("near_alpha")
        second = index.query("near_alpha")
        assert first == second

    def test_ranks_nearest_vector_first(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(truncate_dims=4)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        ranked = index.query("near_alpha")
        assert ranked[0][0] == 0

    def test_index_nbytes_smaller_than_full_dim(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma", "near_alpha"])
        fp32_index = DenseIndex(chunks, _DeterministicModel())
        truncated_index = DenseIndex(
            chunks, _DeterministicModel(), compression=DenseCompressionConfig(truncate_dims=4)
        )
        assert truncated_index.index_nbytes < fp32_index.index_nbytes

    def test_dim_mismatch_still_degrades_visibly(self) -> None:
        chunks = _make_chunks(["alpha", "beta"])
        config = DenseCompressionConfig(truncate_dims=4)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)

        class _WrongDimModel:
            dim = 2

            def encode(self, texts: list[str]) -> np.ndarray:
                return np.ones((len(texts), 2), dtype=np.float32)

        index.model = _WrongDimModel()
        with pytest.raises(DenseUnavailableError, match="dim mismatch"):
            index.query("alpha")


# ---------------------------------------------------------------------------------------
# 3d. Combo: truncate + int8 together.
# ---------------------------------------------------------------------------------------


class TestTruncateInt8Combo:
    def test_combo_builds_and_queries(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma"])
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.INT8, truncate_dims=4)
        index = DenseIndex(chunks, _DeterministicModel(), compression=config)
        assert index.dim == 4
        ranked = index.query("near_alpha")
        assert ranked[0][0] == 0

    def test_combo_index_nbytes_smaller_than_int8_alone(self) -> None:
        chunks = _make_chunks(["alpha", "beta", "gamma", "near_alpha"])
        int8_only = DenseIndex(
            chunks,
            _DeterministicModel(),
            compression=DenseCompressionConfig(quantization=DenseQuantizationMode.INT8),
        )
        combo = DenseIndex(
            chunks,
            _DeterministicModel(),
            compression=DenseCompressionConfig(
                quantization=DenseQuantizationMode.INT8, truncate_dims=4
            ),
        )
        assert combo.index_nbytes < int8_only.index_nbytes


# ---------------------------------------------------------------------------------------
# 4. Empty-corpus edge case under every mode -- never crashes, never a fabricated result.
# ---------------------------------------------------------------------------------------


class TestEmptyCorpusUnderCompression:
    @pytest.mark.parametrize(
        "config",
        [
            DenseCompressionConfig(),
            DenseCompressionConfig(quantization=DenseQuantizationMode.INT8),
            DenseCompressionConfig(quantization=DenseQuantizationMode.BINARY_RESCORE),
            DenseCompressionConfig(truncate_dims=4),
        ],
    )
    def test_empty_chunks_query_returns_empty(self, config: DenseCompressionConfig) -> None:
        index = DenseIndex([], _DeterministicModel(), compression=config)
        assert index.dim == 0
        assert index.query("anything") == []


# ---------------------------------------------------------------------------------------
# 5. Real fetched model (not mocked) -- mirrors test_retrieval_dense.py's TestRealFetchedModel:
# per the failure-archaeology mock-green trap, prove every compression mode against the ACTUAL
# installed model2vec + fetched potion-code-16M model, not just synthetic vectors.
# ---------------------------------------------------------------------------------------


def _real_dense_model_dir():
    from tensor_grep.core.retrieval_dense import default_model_dir

    candidate = default_model_dir()
    return candidate if candidate.is_dir() else None


@pytest.mark.skipif(
    _real_dense_model_dir() is None,
    reason="requires the real fetched minishlab/potion-code-16M model (local dev only, not "
    "committed; CI does not fetch it -- this test exercises the ACTUAL model + every "
    "compression mode when present)",
)
class TestRealFetchedModelCompression:
    """Exercises the ACTUAL installed model2vec + fetched potion-code-16M model under each
    compression mode -- not a mock. Same vocab-mismatch sanity case
    ``test_retrieval_dense.py::TestRealFetchedModel`` uses for the uncompressed leg."""

    def _chunks(self) -> list[Chunk]:
        return [
            Chunk(
                file_path="auth.py",
                start_line=1,
                end_line=2,
                text=(
                    "def authenticate_user(username, password):\n"
                    "    return check_credentials(username, password)\n"
                ),
            ),
            Chunk(
                file_path="conn.py",
                start_line=1,
                end_line=2,
                text="def close_connection(conn):\n    conn.dispose()\n",
            ),
        ]

    def test_int8_ranks_vocab_mismatch_queries_correctly(self) -> None:
        from tensor_grep.core.retrieval_dense import load_dense_model

        model = load_dense_model(_real_dense_model_dir())
        config = DenseCompressionConfig(quantization=DenseQuantizationMode.INT8)
        index = DenseIndex(self._chunks(), model, compression=config)

        assert index.query("verify login")[0][0] == 0  # authenticate_user ranks first
        assert index.query("tear down")[0][0] == 1  # close_connection ranks first

    def test_binary_rescore_ranks_vocab_mismatch_queries_correctly(self) -> None:
        from tensor_grep.core.retrieval_dense import load_dense_model

        model = load_dense_model(_real_dense_model_dir())
        config = DenseCompressionConfig(
            quantization=DenseQuantizationMode.BINARY_RESCORE, rescore_candidates=2
        )
        index = DenseIndex(self._chunks(), model, compression=config)

        assert index.query("verify login")[0][0] == 0
        assert index.query("tear down")[0][0] == 1

    def test_truncate_128_ranks_vocab_mismatch_queries_correctly(self) -> None:
        from tensor_grep.core.retrieval_dense import load_dense_model

        model = load_dense_model(_real_dense_model_dir())
        config = DenseCompressionConfig(truncate_dims=128)
        index = DenseIndex(self._chunks(), model, compression=config)

        assert index.dim == 128
        assert index.query("verify login")[0][0] == 0
        assert index.query("tear down")[0][0] == 1
