"""CPU dense-embedding leg for local hybrid semantic search (`tg search --semantic`, roadmap #27).

Uses `model2vec <https://github.com/MinishLab/model2vec>`_ (MIT) -- a pure-numpy static-embedding
runtime with NO torch/GPU dependency -- paired with the MIT-licensed
`minishlab/potion-code-16M <https://huggingface.co/minishlab/potion-code-16M>`_ model (256-dim,
code-distilled, ~64MB F32). The model is fetched ONCE to a local directory; every search after
that is fully offline.

Fail-closed contract (see AGENTS.md "Backend Fail-Closed Contract"):

- extra not installed, or the model directory has not been fetched -> :class:`DenseUnavailableError`
  (a RECOVERABLE condition; callers MUST catch it and degrade visibly to BM25-only -- stderr +
  ``SearchResult.rank_fallback_reason`` -- never silently, never mislabeled "semantic").
- the model directory exists but fails to load, or produces a malformed embedding shape at
  encode time -> :class:`~tensor_grep.backends.base.BackendExecutionError` (unrecoverable; the
  caller must fail loudly instead of returning a clean-empty result), EXCEPT the specific
  dim-mismatch shape check in :meth:`DenseIndex.query`, which is intentionally recoverable (see
  its docstring) so a broken encoder degrades visibly instead of crashing deep inside a numpy
  matrix multiply.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core.retrieval_chunker import Chunk

if TYPE_CHECKING:
    import numpy as np

_DEFAULT_MODEL_SUBDIR = ("models", "potion-code-16M")


class DenseUnavailableError(RuntimeError):
    """The dense leg cannot run for a RECOVERABLE reason.

    Covers: the model has not been fetched to disk, or a defensive shape check caught a
    malformed/mismatched embedding. Callers MUST catch this and degrade to BM25-only (stderr +
    ``rank_fallback_reason``) rather than let it propagate as a crash or return an empty result
    silently. Distinct from :class:`~tensor_grep.backends.base.BackendExecutionError`, which is
    for a genuine backend fault the caller cannot recover from in-process (e.g. a corrupt model
    directory the loader cannot parse at all).
    """


def dense_available() -> tuple[bool, str | None]:
    """Lazy-import probe: can the dense leg even attempt to run in this environment?

    Returns ``(True, None)`` when both ``model2vec`` and ``numpy`` import cleanly, else
    ``(False, human_reason)``. This is a pure import check -- it does NOT check whether the model
    has been fetched to disk; see :func:`load_dense_model` for that.
    """
    try:
        import model2vec  # noqa: F401
    except ImportError as exc:
        return False, (
            "semantic ranking unavailable: model2vec not installed -- "
            f"pip install 'tensor-grep[semantic]' ({exc})"
        )
    try:
        import numpy  # noqa: F401
    except ImportError as exc:
        return False, f"semantic ranking unavailable: numpy not installed ({exc})"
    return True, None


def default_model_dir() -> Path:
    """Resolve the fetched dense-model directory.

    ``TG_SEMANTIC_MODEL_DIR`` if set, else ``~/.tensor-grep/models/potion-code-16M`` -- the
    single per-machine cache so the model is downloaded once and every repo/search reuses it.
    """
    env = os.environ.get("TG_SEMANTIC_MODEL_DIR")
    if env:
        return Path(env)
    return Path.home() / ".tensor-grep" / Path(*_DEFAULT_MODEL_SUBDIR)


def load_dense_model(model_dir: str | Path) -> Any:
    """Load the model2vec ``StaticModel`` from ``model_dir``.

    Raises :class:`DenseUnavailableError` (recoverable) when the directory does not exist --
    "model not fetched" is an expected, visibly-degraded state, not a crash. Raises
    :class:`BackendExecutionError` (unrecoverable) when the directory exists but the model fails
    to load (corrupt or incompatible files) -- that is a genuine backend fault, never a silent
    clean-empty result.
    """
    path = Path(model_dir)
    if not path.is_dir():
        raise DenseUnavailableError(
            "semantic ranking unavailable: model not fetched -- expected a model2vec "
            f"StaticModel directory at {path}; run `tg index --fetch-model` (or set "
            "TG_SEMANTIC_MODEL_DIR) to provide one"
        )
    try:
        from model2vec import StaticModel

        return StaticModel.from_pretrained(str(path))
    except Exception as exc:  # any load failure here is a genuine backend fault
        raise BackendExecutionError(
            f"dense model at {path} failed to load (corrupt or incompatible): {exc}"
        ) from exc


def _encode_matrix(model: Any, texts: list[str]) -> np.ndarray:
    """Encode ``texts`` raw (no BM25 tokenization -- static embeddings tokenize internally) and
    shape-validate the result into a 2-D ``float32`` matrix.

    ``model.encode`` and ``np.asarray`` are third-party/numpy calls outside this module's control:
    a broken or incompatible model can raise anything (a bare ``RuntimeError`` from model2vec, a
    ``ValueError`` from numpy refusing to build an array out of a ragged nested sequence, etc). Per
    the Backend Fail-Closed Contract (module docstring), that is a genuine unrecoverable backend
    fault, not the deliberately-recoverable shape mismatch below -- so it is re-raised as
    :class:`~tensor_grep.backends.base.BackendExecutionError`, never left to propagate raw.
    """
    import numpy as np

    try:
        raw = model.encode(texts)
        matrix = np.asarray(raw, dtype=np.float32)
    except Exception as exc:
        raise BackendExecutionError(
            f"dense model encode failed for {len(texts)} input(s): {exc}"
        ) from exc
    if matrix.ndim == 1:
        # Some encoders collapse a single-text batch to a 1-D vector; treat it as one row.
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or matrix.shape[0] != len(texts):
        raise DenseUnavailableError(
            "semantic ranking unavailable: dense model produced a malformed embedding shape "
            f"{matrix.shape} for {len(texts)} input(s)"
        )
    return matrix


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


class DenseIndex:
    """In-memory dense (cosine) index over a chunk corpus.

    Vectors are L2-normalized so a plain dot product IS cosine similarity. Chunk text is fed to
    the model RAW -- static embeddings tokenize internally, so this deliberately does NOT reuse
    ``retrieval_lexical.split_terms`` (that tokenizer stays the BM25 leg's alone).
    """

    def __init__(self, chunks: list[Chunk], model: Any) -> None:
        import numpy as np

        self.chunks = list(chunks)
        self.model = model
        if not self.chunks:
            self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
            return

        vectors = _encode_matrix(model, [c.text for c in self.chunks])
        self._matrix = _l2_normalize(vectors)

    @property
    def dim(self) -> int:
        return int(self._matrix.shape[1]) if self._matrix.size else 0

    def query(self, text: str, *, top_k: int = 10) -> list[tuple[int, float]]:
        """Rank chunks by cosine similarity to ``text``; returns ``(chunk_index, score)`` desc.

        Ties break by ascending chunk index (mirrors ``Bm25Index.query``), so results are fully
        deterministic. Raises :class:`DenseUnavailableError` -- NOT a raw ``IndexError``/
        ``ValueError`` -- if the query embedding's dimensionality does not match the index's: a
        defensive shape check so a broken/inconsistent model degrades visibly (BM25-only) instead
        of crashing deep inside a numpy matrix multiply.
        """
        if not self.chunks or self._matrix.size == 0:
            return []

        query_matrix = _encode_matrix(self.model, [text])
        if query_matrix.shape[1] != self._matrix.shape[1]:
            raise DenseUnavailableError(
                "semantic ranking unavailable: query embedding dim "
                f"{query_matrix.shape[1]} does not match index dim {self._matrix.shape[1]} "
                "(dim mismatch)"
            )
        query_vec = _l2_normalize(query_matrix)[0]
        scores = self._matrix @ query_vec
        ranked = sorted(enumerate(scores.tolist()), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]
