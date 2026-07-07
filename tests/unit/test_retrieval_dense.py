"""Tests for the CPU dense-embedding leg (`tg search --semantic`, Path B Stage 1, roadmap #27).

Per the failure-archaeology mock-green trap (a mocked FFI/bridge can pass green while the real
thing is dead), one test class here (`TestRealFetchedModel`) exercises the ACTUAL installed
`model2vec` package against the ACTUAL fetched `minishlab/potion-code-16M` model -- not a mock.
It skips (rather than fails) when the model has not been fetched locally (it is NOT committed to
the repo; CI does not fetch it), so the suite stays green without network access while still
proving the real path works whenever the model IS present (verified locally this session: dim=256,
"verify login" ranks an `authenticate_user` chunk first, "tear down" ranks a `close_connection`
chunk first -- the exact vocab-mismatch case this campaign targets).
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_dense import (
    DenseIndex,
    DenseUnavailableError,
    default_model_dir,
    dense_available,
    load_dense_model,
)


class _FixedDimModel:
    """Minimal duck-typed dense encoder: always returns unit-shape-consistent vectors of a fixed
    dimensionality, for tests that only care about shape-validation behavior."""

    def __init__(self, dim: int, *, row_count_delta: int = 0) -> None:
        self.dim = dim
        self._row_count_delta = row_count_delta

    def encode(self, texts: list[str]) -> np.ndarray:
        count = max(0, len(texts) + self._row_count_delta)
        return np.ones((count, self.dim), dtype=np.float32)


class _CapturingModel:
    """Records exactly what it was asked to encode, to assert chunk text is passed RAW."""

    dim = 4

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        return np.ones((len(texts), self.dim), dtype=np.float32)


class TestDenseAvailable:
    def test_true_in_this_environment(self) -> None:
        # Real environment check -- model2vec + numpy ARE installed here via the `semantic`
        # extra (not mocked). If this flips False, the extra failed to install.
        available, reason = dense_available()
        assert available is True
        assert reason is None

    def test_false_when_model2vec_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "model2vec", None)
        available, reason = dense_available()
        assert available is False
        assert reason is not None
        assert "model2vec not installed" in reason
        assert "tensor-grep[semantic]" in reason

    def test_false_when_numpy_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "numpy", None)
        available, reason = dense_available()
        assert available is False
        assert reason is not None
        assert "numpy not installed" in reason


class TestDefaultModelDir:
    def test_honors_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(tmp_path / "custom-model"))
        assert default_model_dir() == tmp_path / "custom-model"

    def test_defaults_under_home_dotdir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TG_SEMANTIC_MODEL_DIR", raising=False)
        result = default_model_dir()
        assert result.parts[-3:] == (".tensor-grep", "models", "potion-code-16M")


class TestLoadDenseModel:
    def test_not_fetched_raises_recoverable_error(self, tmp_path) -> None:
        missing_dir = tmp_path / "does-not-exist"
        with pytest.raises(DenseUnavailableError, match="not fetched"):
            load_dense_model(missing_dir)

    def test_corrupt_dir_raises_backend_execution_error(self, tmp_path) -> None:
        corrupt_dir = tmp_path / "corrupt-model"
        corrupt_dir.mkdir()
        (corrupt_dir / "not_a_real_model.txt").write_text("garbage", encoding="utf-8")
        with pytest.raises(BackendExecutionError):
            load_dense_model(corrupt_dir)


class TestDenseIndexShapeValidation:
    def test_empty_chunks_query_returns_empty(self) -> None:
        index = DenseIndex([], _FixedDimModel(dim=4))
        assert index.dim == 0
        assert index.query("anything") == []

    def test_encode_uses_raw_chunk_text_not_tokenized(self) -> None:
        model = _CapturingModel()
        chunks = [
            Chunk(file_path="a.py", start_line=1, end_line=2, text="def fooBar_baz():\n    pass\n")
        ]
        DenseIndex(chunks, model)
        assert model.calls == [["def fooBar_baz():\n    pass\n"]]

    def test_corpus_row_count_mismatch_raises_recoverable_not_indexerror(self) -> None:
        # The fake model returns one EXTRA row vs. the number of chunks encoded -- a malformed
        # shape that must degrade visibly (DenseUnavailableError), never crash as a raw
        # IndexError/ValueError deep inside a later numpy operation.
        chunks = [Chunk(file_path="a.py", start_line=1, end_line=1, text="hello")]
        with pytest.raises(DenseUnavailableError):
            DenseIndex(chunks, _FixedDimModel(dim=4, row_count_delta=1))

    def test_query_dim_mismatch_degrades_not_indexerror(self) -> None:
        chunks = [Chunk(file_path="a.py", start_line=1, end_line=1, text="hello world")]
        index = DenseIndex(chunks, _FixedDimModel(dim=4))
        # Simulate a model whose behavior diverges between corpus-encode time and query time
        # (e.g. a corrupted/reloaded model) -- must raise DenseUnavailableError, not a raw
        # shape-mismatch exception from the matrix multiply.
        index.model = _FixedDimModel(dim=8)
        with pytest.raises(DenseUnavailableError, match="dim mismatch"):
            index.query("hello")

    def test_ranking_ties_break_by_ascending_chunk_index(self) -> None:
        # Two chunks with IDENTICAL vectors -> identical cosine score -> tie-break by index.
        chunks = [
            Chunk(file_path="a.py", start_line=1, end_line=1, text="same"),
            Chunk(file_path="b.py", start_line=1, end_line=1, text="same"),
        ]

        class _SameVectorModel:
            dim = 2

            def encode(self, texts: list[str]) -> np.ndarray:
                return np.ones((len(texts), 2), dtype=np.float32)

        index = DenseIndex(chunks, _SameVectorModel())
        ranked = index.query("query")
        assert [chunk_idx for chunk_idx, _ in ranked] == [0, 1]


def _real_dense_model_dir():
    candidate = default_model_dir()
    return candidate if candidate.is_dir() else None


@pytest.mark.skipif(
    _real_dense_model_dir() is None,
    reason="requires the real fetched minishlab/potion-code-16M model (local dev only, not "
    "committed; CI does not fetch it -- this test exercises the ACTUAL model when present)",
)
class TestRealFetchedModel:
    """Exercises the ACTUAL installed model2vec + fetched potion-code-16M model -- not a mock."""

    def test_real_model_loads_with_expected_dimensionality(self) -> None:
        model = load_dense_model(_real_dense_model_dir())
        chunks = [Chunk(file_path="a.py", start_line=1, end_line=1, text="def foo(): pass")]
        index = DenseIndex(chunks, model)
        assert index.dim == 256

    def test_real_model_ranks_vocab_mismatch_queries_correctly(self) -> None:
        """The vocab-mismatch case this campaign targets: a query with NO literal term overlap
        with the matching code must still rank the semantically-relevant chunk first."""
        model = load_dense_model(_real_dense_model_dir())
        chunks = [
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
        index = DenseIndex(chunks, model)

        login_ranked = index.query("verify login")
        assert login_ranked[0][0] == 0  # authenticate_user ranks first

        teardown_ranked = index.query("tear down")
        assert teardown_ranked[0][0] == 1  # close_connection ranks first
