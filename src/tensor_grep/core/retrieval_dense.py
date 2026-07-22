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
        # v1.92.1 dogfood item 3 (UX/honesty batch): lead with the one-shot `tg install-dense`
        # command (CEO#7) -- the pip extra stays as a parenthetical alternative for a caller who
        # wants to script the install directly. Keep both "model2vec not installed" and
        # "tensor-grep[semantic]" verbatim in the message: pinned by
        # test_retrieval_dense.py::test_false_when_model2vec_missing.
        return False, (
            "semantic ranking unavailable: model2vec not installed -- "
            f"run `tg install-dense` (or pip install 'tensor-grep[semantic]') ({exc})"
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
