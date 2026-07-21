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

This module also owns a checksum-pinned fetch of the potion-code-16M model files themselves --
:func:`fetch_dense_model` (importable) and ``python -m tensor_grep.core.retrieval_dense --fetch``
(CLI), mirroring :func:`~tensor_grep.core.retrieval_late.fetch_late_model`'s precedent exactly: a
pinned HF commit SHA (never ``main``), a per-file SHA-256 + exact-byte-size manifest, a
byte-capped + time-bound + wall-clock-deadline-bounded download, atomic verify-before-install, and
fail-closed refuse + cleanup on any mismatch -- never a partial install. Never auto-downloads at
query time; fetch is an explicit user action only.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
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
            f"StaticModel directory at {path}; run "
            "`python -m tensor_grep.core.retrieval_dense --fetch` (or set TG_SEMANTIC_MODEL_DIR) "
            "to provide one"
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


# ---------------------------------------------------------------------------------------------
# Dense-leg compression (tensor-grep-semantic-search-campaign, dense-leg compression wave):
# int8 scalar quantization, binary(1-bit)+int8-rescore, and post-hoc dim-truncation. Every lever
# is opt-in via `DenseCompressionConfig`, default OFF (`DenseCompressionConfig()` is a no-op) --
# see that class's docstring for the full design rationale. Learning-free, $0, deterministic; no
# new third-party dependency (pure numpy over the SAME model2vec output this module already
# produces).
# ---------------------------------------------------------------------------------------------


class DenseQuantizationMode(StrEnum):
    """Compression mode for :class:`DenseIndex`'s scoring representation. ``NONE`` (the default)
    is the plain fp32 matrix this module has always used -- selecting it is a byte-identical
    no-op against the pre-compression code."""

    NONE = "none"
    INT8 = "int8"
    BINARY_RESCORE = "binary_rescore"


_ENV_DENSE_QUANTIZATION = "TG_SEMANTIC_DENSE_QUANTIZATION"
_ENV_DENSE_TRUNCATE_DIMS = "TG_SEMANTIC_DENSE_TRUNCATE_DIMS"
_ENV_DENSE_RESCORE_CANDIDATES = "TG_SEMANTIC_DENSE_RESCORE_CANDIDATES"
_DEFAULT_RESCORE_CANDIDATES = 50


@dataclass(frozen=True)
class DenseCompressionConfig:
    """Opt-in, default-OFF compression for :class:`DenseIndex`'s scoring representation.

    Every existing caller that builds a ``DenseIndex`` without passing ``compression=`` gets
    ``DenseCompressionConfig()`` -- ``quantization=NONE, truncate_dims=None`` -- which is a
    byte-identical no-op against the fp32 path this module shipped with; nothing about default
    behavior changes by adding this class.

    Two ORTHOGONAL, freely-combinable levers (both learning-free, $0, deterministic):

    - ``truncate_dims``: keep only the first N dimensions of the embedding (Model2Vec applies PCA
      as a post-processing step, so the dims are already variance-ordered -- this is principled
      truncation, not an arbitrary slice), then re-L2-normalize. ``None`` (default) keeps the
      model's full dimensionality.
    - ``quantization``: ``NONE`` (fp32, default), ``INT8`` (per-dimension symmetric int8 scalar
      quantization; dequantized elementwise before the cosine dot product), or
      ``BINARY_RESCORE`` (sign-bit binarization for an O(N) Hamming-distance shortlist over the
      WHOLE corpus via packed-byte XOR + popcount, then an int8-dequantized rescore of only the
      ``rescore_candidates`` closest candidates -- the corpus-scale matmul against every chunk is
      never computed in this mode).

    Combining ``truncate_dims`` with ``quantization=INT8`` composes both levers (truncate first,
    then quantize the truncated vectors) -- the "best combo" candidate the campaign specifies.
    """

    quantization: DenseQuantizationMode = DenseQuantizationMode.NONE
    truncate_dims: int | None = None
    rescore_candidates: int = _DEFAULT_RESCORE_CANDIDATES

    def __post_init__(self) -> None:
        if self.truncate_dims is not None and self.truncate_dims <= 0:
            raise ValueError(f"truncate_dims must be positive, got {self.truncate_dims}")
        if self.rescore_candidates <= 0:
            raise ValueError(f"rescore_candidates must be positive, got {self.rescore_candidates}")

    @property
    def is_noop(self) -> bool:
        """``True`` iff this config changes nothing vs. the pre-compression fp32 behavior."""
        return self.quantization == DenseQuantizationMode.NONE and self.truncate_dims is None

    @classmethod
    def from_env(cls) -> DenseCompressionConfig:
        """Read the default-OFF compression knobs from the environment.

        Fail-closed: an unset/blank ``TG_SEMANTIC_DENSE_QUANTIZATION`` resolves to ``NONE`` (the
        safe, unchanged default), but a SET-and-unrecognized value raises :class:`ValueError`
        rather than silently falling back to ``NONE`` -- a typo'd env var must never silently
        no-op the compression the caller thought they were turning on.
        """
        raw_mode = os.environ.get(_ENV_DENSE_QUANTIZATION, "").strip().lower()
        if not raw_mode:
            quantization = DenseQuantizationMode.NONE
        else:
            try:
                quantization = DenseQuantizationMode(raw_mode)
            except ValueError:
                valid = ", ".join(mode.value for mode in DenseQuantizationMode)
                raise ValueError(
                    f"{_ENV_DENSE_QUANTIZATION}={raw_mode!r} is not a recognized dense "
                    f"quantization mode (expected one of: {valid})"
                ) from None

        raw_truncate = os.environ.get(_ENV_DENSE_TRUNCATE_DIMS, "").strip()
        try:
            truncate_dims = int(raw_truncate) if raw_truncate else None
        except ValueError:
            raise ValueError(
                f"{_ENV_DENSE_TRUNCATE_DIMS}={raw_truncate!r} is not a valid integer"
            ) from None

        raw_candidates = os.environ.get(_ENV_DENSE_RESCORE_CANDIDATES, "").strip()
        try:
            rescore_candidates = (
                int(raw_candidates) if raw_candidates else _DEFAULT_RESCORE_CANDIDATES
            )
        except ValueError:
            raise ValueError(
                f"{_ENV_DENSE_RESCORE_CANDIDATES}={raw_candidates!r} is not a valid integer"
            ) from None

        return cls(
            quantization=quantization,
            truncate_dims=truncate_dims,
            rescore_candidates=rescore_candidates,
        )


def _truncate_and_renormalize(matrix: np.ndarray, n_dims: int) -> np.ndarray:
    """Keep the first ``n_dims`` columns and re-L2-normalize.

    Mathematically equivalent to truncating the RAW vector before its first L2-normalization (a
    per-row positive scalar division commutes with a fixed column slice: for unit vector
    ``n = x / ||x||``, ``truncate(n) / ||truncate(n)|| == truncate(x) / ||truncate(x)||``), so
    this can be applied to an already-normalized matrix without changing the result versus
    truncating pre-normalization.
    """
    return _l2_normalize(matrix[:, :n_dims])


_INT8_MAX = 127


def _quantize_int8(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-dimension symmetric int8 scalar quantization.

    Returns ``(codes, scale)`` where ``codes`` is ``int8`` shape ``(N, D)`` and ``scale`` is
    ``float32`` shape ``(D,)``, such that ``codes.astype(float32) * scale`` recovers ``matrix``
    within ``+/- scale[d] / 2`` per component (the standard round-to-nearest quantization error
    bound). A column that is exactly all-zero gets ``scale=1.0`` (never a division by zero) and
    every code in it is exactly ``0``, so it round-trips EXACTLY, not just within a bound.

    Clipping to ``[-127, 127]`` (not the full int8 range down to -128) keeps the quantization
    symmetric around zero and is ALSO load-bearing correctness, not decoration: rounding a
    component whose true value sits fractionally above ``127 * scale`` could otherwise produce a
    code of ``128``, which silently WRAPS to ``-128`` on ``.astype(np.int8)`` (numpy does not
    clip on integer-cast overflow) -- a wrong-sign corruption, not just extra error.
    """
    import numpy as np

    absmax = np.max(np.abs(matrix), axis=0)
    absmax = np.where(absmax == 0.0, 1.0, absmax)
    scale = (absmax / _INT8_MAX).astype(np.float32)
    codes = np.clip(np.round(matrix / scale), -_INT8_MAX, _INT8_MAX).astype(np.int8)
    return codes, scale


def _pack_binary(matrix: np.ndarray) -> np.ndarray:
    """Sign-bit binarization (``1`` if a component is ``>= 0`` else ``0``), packed 8-per-byte via
    ``np.packbits`` -- the compact representation the Hamming-distance shortlist scans."""
    import numpy as np

    bits = (matrix >= 0).astype(np.uint8)
    return np.packbits(bits, axis=1)


_POPCOUNT_BITS: tuple[int, ...] = tuple(bin(i).count("1") for i in range(256))


@lru_cache(maxsize=1)
def _popcount_table() -> np.ndarray:
    """8-bit popcount lookup table, built once (numpy stays a lazy/optional import at module
    scope -- see the module docstring's fail-closed contract -- so this cannot be a module-level
    numpy array literal)."""
    import numpy as np

    return np.asarray(_POPCOUNT_BITS, dtype=np.uint8)


def _hamming_distances(packed_codes: np.ndarray, query_packed: np.ndarray) -> np.ndarray:
    """Hamming distance from every row of ``packed_codes`` to the single ``query_packed`` code,
    via byte-wise XOR + the precomputed popcount table (no per-bit Python loop, no per-query
    table rebuild)."""
    import numpy as np

    table = _popcount_table()
    xor = np.bitwise_xor(packed_codes, query_packed)
    return table[xor].sum(axis=1).astype(np.int64)


class DenseIndex:
    """In-memory dense (cosine) index over a chunk corpus.

    Vectors are L2-normalized so a plain dot product IS cosine similarity. Chunk text is fed to
    the model RAW -- static embeddings tokenize internally, so this deliberately does NOT reuse
    ``retrieval_lexical.split_terms`` (that tokenizer stays the BM25 leg's alone).

    ``compression`` (default ``None``, treated as ``DenseCompressionConfig()``) is an OPT-IN,
    default-OFF experimental knob -- see :class:`DenseCompressionConfig`. Every caller in this
    repo today omits it, and gets exactly the fp32 behavior this class has always had.
    """

    def __init__(
        self,
        chunks: list[Chunk],
        model: Any,
        *,
        compression: DenseCompressionConfig | None = None,
    ) -> None:
        import numpy as np

        self.chunks = list(chunks)
        self.model = model
        self.compression = compression if compression is not None else DenseCompressionConfig()
        self._dim = 0
        self._matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self._int8_codes: np.ndarray | None = None
        self._int8_scale: np.ndarray | None = None
        self._binary_codes: np.ndarray | None = None
        if not self.chunks:
            return

        vectors = _encode_matrix(model, [c.text for c in self.chunks])
        matrix = _l2_normalize(vectors)

        if self.compression.truncate_dims is not None:
            if self.compression.truncate_dims >= matrix.shape[1]:
                raise ValueError(
                    f"truncate_dims={self.compression.truncate_dims} must be less than the "
                    f"model's embedding dimensionality ({matrix.shape[1]}) -- truncating to a "
                    "value >= the full dimensionality is a no-op; omit truncate_dims instead of "
                    "setting it to a non-reducing value"
                )
            matrix = _truncate_and_renormalize(matrix, self.compression.truncate_dims)

        self._dim = int(matrix.shape[1])

        if self.compression.quantization == DenseQuantizationMode.NONE:
            self._matrix = matrix
        elif self.compression.quantization == DenseQuantizationMode.INT8:
            self._int8_codes, self._int8_scale = _quantize_int8(matrix)
        elif self.compression.quantization == DenseQuantizationMode.BINARY_RESCORE:
            self._binary_codes = _pack_binary(matrix)
            self._int8_codes, self._int8_scale = _quantize_int8(matrix)
        else:  # pragma: no cover -- exhaustive enum; defensive only.
            raise AssertionError(
                f"unhandled DenseQuantizationMode: {self.compression.quantization!r}"
            )

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def index_nbytes(self) -> int:
        """Actual heap footprint (bytes) of the active scoring representation -- the numeric
        arrays this instance retains, NOT ``self.chunks``/the model. A REAL measured number per
        compression mode, not an assumed theoretical multiplier."""
        total = int(self._matrix.nbytes)
        if self._int8_codes is not None:
            total += int(self._int8_codes.nbytes)
        if self._int8_scale is not None:
            total += int(self._int8_scale.nbytes)
        if self._binary_codes is not None:
            total += int(self._binary_codes.nbytes)
        return total

    def _prepare_query_vec(self, text: str) -> np.ndarray:
        """Encode + (if configured) truncate + L2-normalize ``text`` into the SAME space the
        index's chunk vectors live in. Raises :class:`DenseUnavailableError` on a dimensionality
        mismatch (a broken/inconsistent model), mirroring the pre-compression check exactly.
        """
        query_matrix = _encode_matrix(self.model, [text])
        if self.compression.truncate_dims is not None:
            if query_matrix.shape[1] < self.compression.truncate_dims:
                raise DenseUnavailableError(
                    "semantic ranking unavailable: query embedding dim "
                    f"{query_matrix.shape[1]} is smaller than the configured truncate_dims "
                    f"{self.compression.truncate_dims} (dim mismatch)"
                )
            query_matrix = query_matrix[:, : self.compression.truncate_dims]
        if query_matrix.shape[1] != self._dim:
            raise DenseUnavailableError(
                "semantic ranking unavailable: query embedding dim "
                f"{query_matrix.shape[1]} does not match index dim {self._dim} (dim mismatch)"
            )
        return _l2_normalize(query_matrix)[0]

    def query(self, text: str, *, top_k: int = 10) -> list[tuple[int, float]]:
        """Rank chunks by similarity to ``text``; returns ``(chunk_index, score)`` desc.

        Ties break by ascending chunk index (mirrors ``Bm25Index.query``), so results are fully
        deterministic regardless of ``compression`` mode. Raises :class:`DenseUnavailableError` --
        NOT a raw ``IndexError``/``ValueError`` -- on a dimensionality mismatch: a defensive shape
        check so a broken/inconsistent model degrades visibly (BM25-only) instead of crashing deep
        inside a numpy matrix multiply.

        ``BINARY_RESCORE`` mode returns AT MOST ``compression.rescore_candidates`` results (an
        honest ANN-shortlist recall bound, not a bug) -- the corpus-scale similarity computation
        against every chunk is deliberately never performed in that mode.
        """
        import numpy as np

        if not self.chunks or self._dim == 0:
            return []

        query_vec = self._prepare_query_vec(text)

        if self.compression.quantization == DenseQuantizationMode.NONE:
            scores = self._matrix @ query_vec
            ranked = sorted(enumerate(scores.tolist()), key=lambda item: (-item[1], item[0]))
            return ranked[:top_k]

        if self.compression.quantization == DenseQuantizationMode.INT8:
            assert self._int8_codes is not None and self._int8_scale is not None
            dequantized = self._int8_codes.astype(np.float32) * self._int8_scale
            scores = dequantized @ query_vec
            ranked = sorted(enumerate(scores.tolist()), key=lambda item: (-item[1], item[0]))
            return ranked[:top_k]

        # BINARY_RESCORE: Hamming-shortlist over the WHOLE corpus (cheap: packed-byte XOR +
        # popcount), then an int8-dequantized rescore of only the closest `rescore_candidates` --
        # the full-corpus fp32/int8 matmul is never computed.
        assert self._binary_codes is not None
        assert self._int8_codes is not None and self._int8_scale is not None
        query_packed = _pack_binary(query_vec.reshape(1, -1))[0]
        hamming = _hamming_distances(self._binary_codes, query_packed)
        candidate_count = min(self.compression.rescore_candidates, len(self.chunks))
        shortlist = sorted(range(len(hamming)), key=lambda i: (int(hamming[i]), i))[
            :candidate_count
        ]
        shortlist_idx = np.asarray(shortlist, dtype=np.int64)
        dequantized_shortlist = (
            self._int8_codes[shortlist_idx].astype(np.float32) * self._int8_scale
        )
        scores = dequantized_shortlist @ query_vec
        order = sorted(range(len(shortlist)), key=lambda i: (-float(scores[i]), shortlist[i]))
        return [(shortlist[i], float(scores[i])) for i in order][:top_k]


# ---------------------------------------------------------------------------------------------
# Checksum-pinned fetch of the potion-code-16M model files (mirrors
# ``retrieval_late.py``'s LateOn-Code-edge fetch -- see that module for the full design
# rationale). Never auto-downloads at query time; fetch is an explicit user action only, via
# ``python -m tensor_grep.core.retrieval_dense --fetch``.
# ---------------------------------------------------------------------------------------------

# `minishlab/potion-code-16M` (MIT; see NOTICE). Pinned to a fixed commit SHA via a
# `resolve/<sha>/...` URL (never `/resolve/main/...`) so the fetched content is immutable
# regardless of future upstream changes. Resolved 2026-07-16 via `git ls-remote
# https://huggingface.co/minishlab/potion-code-16M` (the repo's `refs/heads/main` at fetch time).
_HF_REPO = "minishlab/potion-code-16M"
_HF_REVISION = "1b0ff71095656b23306542bbad34a09109673720"
_HF_RESOLVE_BASE = f"https://huggingface.co/{_HF_REPO}/resolve/{_HF_REVISION}"

# filename -> (sha256_hex, exact_byte_size). This is exactly the 3-file model2vec "native" layout
# `StaticModel.from_pretrained` requires (model2vec's `persistence/datamodels.py`
# `FOLDER_LAYOUTS[0]`: `config.json` + `model.safetensors` + `tokenizer.json`, verified against
# the model2vec source). The upstream repo ALSO carries `modules.json` and `README.md`, but
# neither is read by the loader (`persistence/persistence.py::load_pretrained`), so both are
# deliberately excluded from the pinned manifest -- fewer pinned files means less to verify and
# less that can silently drift.
#
# Computed by downloading each file from the pinned revision above and hashing locally, verified
# 2026-07-16 via THREE independent tools (Python `hashlib.sha256`, PowerShell `Get-FileHash`, and
# `certutil -hashfile`, all byte-identical) -- see supply-chain-hardening skill H6
# "SHA-confirmation discipline": never trust an agent-reported or sidecar SHA, always
# download+hash to confirm. `model.safetensors`' hash additionally cross-checks the HF API tree
# endpoint's LFS pointer `oid` (sha256) for the pinned revision, obtained WITHOUT downloading the
# file first (`GET /api/models/minishlab/potion-code-16M/tree/<revision>`) -- all four independent
# sources agree.
_FETCH_MANIFEST: dict[str, tuple[str, int]] = {
    "model.safetensors": (
        "ca6159081a6e96cebe4ad878e5e8437bfccc761e8db16223370149cd2faa6c0b",
        64_299_272,
    ),
    "tokenizer.json": (
        "8e84217af15e70e8127c855435fc3d8a4cd91d7bbe686f72e75f188118ec78ae",
        1_041_917,
    ),
    "config.json": (
        "edf07552b5d768d556ded176d19f3a34f25360548de3a246d226ce8e28647914",
        97,
    ),
}

_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024  # per-file cap; the largest pinned file is ~64.3 MB
_DOWNLOAD_TIMEOUT_S = 60.0
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _download_bounded(url: str, *, max_bytes: int, timeout_s: float) -> bytes:
    """Stream ``url`` fully into memory, refusing to exceed ``max_bytes``
    (supply-chain-hardening H2: byte-capped + time-bound). The cap is enforced per-chunk during
    the read, not after buffering the whole body, so an oversized response cannot exhaust memory
    before the cap trips.

    ALSO enforces a total wall-clock deadline across the whole streamed read
    (``TG_SEMANTIC_FETCH_DEADLINE_S``, default 300s -- generous for a ~65MB download on a slow
    link). ``timeout_s`` only bounds a SINGLE ``resp.read()`` call, so a malicious/compromised
    server that keeps every individual recv just under ``timeout_s`` (and every chunk under
    ``max_bytes``) -- a slow-drip -- could otherwise hang the fetch indefinitely. This is an
    ADDITIVE third bound: it does not change the per-recv socket timeout or the byte cap. Mirrors
    ``retrieval_late._download_bounded`` exactly (see that module for the original design note,
    Opus security-gate nit #87).

    Raises a plain ``OSError``/``ValueError`` on any failure (network error, timeout, the byte
    cap, or the wall-clock deadline). The caller (:func:`fetch_dense_model`) wraps ALL of this
    uniformly into :class:`~tensor_grep.backends.base.BackendExecutionError`.
    """
    deadline_s = float(os.environ.get("TG_SEMANTIC_FETCH_DEADLINE_S", "300"))
    start = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "tensor-grep-semantic-fetch"})
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        buffer = bytearray()
        while True:
            chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise ValueError(f"{url} exceeded the {max_bytes}-byte cap")
            if time.monotonic() - start > deadline_s:
                raise ValueError(f"{url} exceeded the {deadline_s}s total download deadline")
        return bytes(buffer)


def fetch_dense_model(dest_dir: str | Path | None = None) -> Path:
    """Download the 3 pinned potion-code-16M files into ``dest_dir``, checksum-gated + atomic.

    Fail-closed contract (supply-chain-hardening H2/H3): each file is streamed with a byte cap +
    timeout, verified against a hard-coded SHA-256 pin from a PINNED HF revision (``_HF_REVISION``,
    never ``main``) BEFORE anything lands at ``dest_dir``. On any download failure or checksum
    mismatch, the temp download directory is discarded and :class:`BackendExecutionError` is
    raised -- no partial or unverified file is ever left where :func:`load_dense_model` would find
    it.

    The final install step (moving the verified temp directory to ``dest_dir``) is a single
    ``os.replace`` -- atomic on both POSIX and Windows PROVIDED ``dest_dir`` does not already
    exist. If ``dest_dir`` already holds a previous install (a re-fetch), it is removed
    immediately before the replace: ``os.replace`` cannot atomically overwrite a non-empty
    directory on Windows (verified empirically: ``PermissionError [WinError 5] Access is
    denied``), so a plain overwrite is not available cross-platform. This narrows, but does not
    eliminate, the crash window between the rmtree and the replace; the new copy is fully
    verified before the old one is ever touched, and a re-run of ``--fetch`` is idempotent.

    Mirrors :func:`~tensor_grep.core.retrieval_late.fetch_late_model` exactly (see that module for
    the original design note); NEVER called automatically at query time -- fetch is an explicit
    user action only (``python -m tensor_grep.core.retrieval_dense --fetch``).
    """
    dest = Path(dest_dir) if dest_dir is not None else default_model_dir()
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = tempfile.mkdtemp(dir=str(dest.parent), prefix=".tg-semantic-fetch-")
    tmp_path = Path(tmp_dir)
    try:
        for filename, (expected_sha256, expected_size) in _FETCH_MANIFEST.items():
            url = f"{_HF_RESOLVE_BASE}/{filename}"
            try:
                data = _download_bounded(
                    url, max_bytes=_MAX_DOWNLOAD_BYTES, timeout_s=_DOWNLOAD_TIMEOUT_S
                )
            except Exception as exc:
                raise BackendExecutionError(
                    f"dense model fetch failed downloading {filename} from {url}: {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != expected_sha256 or len(data) != expected_size:
                raise BackendExecutionError(
                    f"dense model fetch checksum mismatch for {filename}: expected "
                    f"sha256={expected_sha256} size={expected_size}, got "
                    f"sha256={actual_sha256} size={len(data)} -- refusing to install (the fetch "
                    "is PINNED to a fixed HF revision and must never silently accept changed "
                    "content)"
                )
            (tmp_path / filename).write_bytes(data)

        if dest.exists():
            shutil.rmtree(dest)
        os.replace(tmp_path, dest)
    finally:
        # A no-op if the `os.replace` above already moved `tmp_path` away (success path);
        # cleans up the partial download on any failure path (checksum mismatch, network error).
        shutil.rmtree(tmp_path, ignore_errors=True)
    return dest


def _fetch_cli(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m tensor_grep.core.retrieval_dense --fetch``."""
    parser = argparse.ArgumentParser(
        prog="python -m tensor_grep.core.retrieval_dense",
        description=(
            "Fetch the pinned potion-code-16M dense-embedding model files (checksum-verified)."
        ),
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download the 3 pinned model files to the model cache directory.",
    )
    parser.add_argument(
        "--model-dir",
        default=None,
        help="Override the fetch destination (else TG_SEMANTIC_MODEL_DIR, or the default cache dir).",
    )
    args = parser.parse_args(argv)
    if not args.fetch:
        parser.print_help()
        return 2
    try:
        dest = fetch_dense_model(args.model_dir)
    except Exception as exc:
        print(f"tg: dense model fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"tg: dense model fetched to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_fetch_cli())
