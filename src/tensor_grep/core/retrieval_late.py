"""Late-interaction (MaxSim / ColBERT-style) rerank stage for `tg search --semantic`
(roadmap docs/plans/design-tensor-grep-late-rerank-2026-07-09.md).

T0-T2 (foundation): pure MaxSim math (:func:`maxsim_scores`, :func:`rank_by_maxsim`) plus the
:class:`LateReranker` contract against an INJECTED token encoder.

T3 (this increment): a real ONNX encoder behind the ``rerank`` extra -- :func:`late_available`
(import probe, mirrors ``retrieval_dense.dense_available``), :func:`load_late_model` (loads the
ONNX session + tokenizer + ``onnx_config.json`` contract, two-tier fail-closed),
:func:`build_late_encoder` (a real, non-stub ``Callable[[str], np.ndarray]`` matching
:class:`LateReranker`'s injected ``encode`` signature), and :func:`load_late_reranker` (a
ready-to-use :class:`LateReranker` wired with it).

T4 (this increment): a checksum-pinned fetch of the 3 required model files from a PINNED Hugging
Face revision -- :func:`fetch_late_model` (importable) and ``python -m
tensor_grep.core.retrieval_late --fetch`` (CLI). Never auto-downloads at query time.

Still pending: the seam wiring into ``rerank_hybrid`` (T5), the latency-budget + bidirectional
fail-closed invariant (T6), and the golden-set quality gate that decides ship/no-ship (T8) -- this
module remains unreachable from `tg search` until those land.

Assumption: both :func:`maxsim_scores` inputs are ALREADY L2-normalized per token (each row a unit
vector) -- this module does not normalize them there; the real encoder built by
:func:`build_late_encoder` DOES normalize (see its docstring). A plain dot product between two
normalized rows IS cosine similarity, so ``MaxSim(q, d) = sum_i max_j (q_i . d_j)`` is a sum of
per-query-token cosine maxima.

Fail-closed contract (see AGENTS.md "Backend Fail-Closed Contract" and retrieval_dense.py:9-19):
extra not installed, or the model directory not fetched -> :class:`LateRerankUnavailableError`
(RECOVERABLE; callers must degrade visibly -- stderr + ``SearchResult.rank_fallback_reason`` --
never silently). ONNX/tokenizer load fails, or an encode call raises or produces a malformed
shape -> :class:`~tensor_grep.backends.base.BackendExecutionError` (unrecoverable).
:meth:`LateReranker.rerank` itself still assumes its injected ``encode`` callable already
succeeded -- a raising ``encode`` propagates raw from there, same as T0-T2. Latency-budget
enforcement (``TG_RERANK_BUDGET_MS``) is NOT implemented here; that lands in T6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tensor_grep.backends.base import BackendExecutionError

if TYPE_CHECKING:
    import numpy as np


def maxsim_scores(query_matrix: np.ndarray, doc_matrices: Sequence[np.ndarray]) -> list[float]:
    """MaxSim(query, doc) for each doc in ``doc_matrices``, returned in the same order.

    ``query_matrix`` is ``(Tq, D)``; each ``doc_matrices[k]`` is ``(Tdk, D)`` -- both assumed
    already L2-normalized per row (see module docstring). Score = sum over query-token rows of
    that row's max dot product against any doc-token row: ``sum_i max_j (q_i . d_j)``.

    A doc with zero tokens (an empty ``(0, D)`` matrix) scores 0.0 -- there is nothing to compare
    against, and numpy's ``.max(axis=1)`` would otherwise raise on a zero-size reduction.
    """
    import numpy as np

    scores: list[float] = []
    for doc_matrix in doc_matrices:
        if query_matrix.size == 0 or doc_matrix.size == 0:
            scores.append(0.0)
            continue
        similarity = query_matrix @ doc_matrix.T  # (Tq, Td)
        scores.append(float(np.max(similarity, axis=1).sum()))
    return scores


def rank_by_maxsim(scores: Sequence[float], indices: Sequence[int]) -> list[int]:
    """Rank ``indices`` by descending ``scores``; ties break by ascending index VALUE.

    Pure ordering utility -- ``scores[i]`` is the MaxSim score already computed for
    ``indices[i]``. Mirrors the ascending-index tie-break determinism contract used by
    ``Bm25Index.query`` / ``DenseIndex.query`` (retrieval_bm25.py:77, retrieval_dense.py:192),
    except the tie-break key is the ORIGINAL index value rather than list position -- callers pass
    an arbitrary pre-ranked subset (the RRF-fused pool), not the full corpus in index order, so
    position alone would not be a stable, input-order-independent contract.
    """
    paired = zip(indices, scores, strict=True)
    return [idx for idx, _ in sorted(paired, key=lambda pair: (-pair[1], pair[0]))]


class LateReranker:
    """Order-only MaxSim reranker over an already-pooled candidate set.

    The token encoder is INJECTED (``encode: str -> (T, D) ndarray``), so unit tests exercise a
    deterministic stub -- no ONNX model required at this stage (T3 wires the real encoder behind
    the ``rerank`` extra). Mirrors :class:`~tensor_grep.core.retrieval_dense.DenseIndex`'s
    dependency-injection shape (retrieval_dense.py:155, the model is passed in already loaded)
    rather than owning model lifecycle itself.
    """

    def __init__(self, encode: Callable[[str], np.ndarray]) -> None:
        self._encode = encode

    def rerank(self, query: str, chunks: list[str], indices: list[int]) -> list[int]:
        """Return ``indices`` permuted by MaxSim(query, chunk) descending; ties break by
        ascending original index value.

        Output is EXACTLY ``indices`` reordered -- never adds, never drops (the seam this feeds,
        ``rerank_hybrid``, splices this back over a ``fused_order`` head slice and must preserve
        membership; design doc "The seam"). An empty pool returns ``[]`` without invoking
        ``encode`` at all.

        ``chunks[i]`` must correspond to ``indices[i]`` (same length, position-aligned) -- the
        caller's already-pooled candidate set.
        """
        if not indices:
            return []
        query_matrix = self._encode(query)
        doc_matrices = [self._encode(chunk) for chunk in chunks]
        scores = maxsim_scores(query_matrix, doc_matrices)
        return rank_by_maxsim(scores, indices)


# ---------------------------------------------------------------------------------------------
# T3 -- ONNX encoder behind the `rerank` extra.
# ---------------------------------------------------------------------------------------------

_LATE_MODEL_SUBDIR = ("models", "LateOn-Code-edge")
_REQUIRED_MODEL_FILES = ("model_int8.onnx", "tokenizer.json", "onnx_config.json")

# Hard safety ceiling on tokens-per-encode, independent of whatever `onnx_config.json` claims for
# `query_length`/`document_length` (design doc "Inference": "512-token truncation guard"). The
# real onnx_config.json pinned below configures document_length=2048 -- this guard additionally
# caps that, trading a little quality on very long chunks for a bounded worst-case encode cost
# (latency/DoS defense; a corrupt or tampered onnx_config.json cannot blow the cost past this).
_MAX_TOKEN_LENGTH = 512


class LateRerankUnavailableError(RuntimeError):
    """The late-interaction rerank stage cannot run for a RECOVERABLE reason.

    Covers: the ``rerank`` extra is not installed, or the model has not been fetched to disk (or
    an encoder produced a malformed embedding shape). Callers MUST catch this and degrade to the
    pre-rerank order visibly -- stderr + ``SearchResult.rank_fallback_reason`` -- rather than let
    it propagate as a crash or silently drop the rerank stage. Mirrors
    :class:`~tensor_grep.core.retrieval_dense.DenseUnavailableError`; distinct from
    :class:`~tensor_grep.backends.base.BackendExecutionError`, which is for a genuine backend
    fault (corrupt model files, an encode-time crash) the caller cannot recover from in-process.
    """


def late_available() -> tuple[bool, str | None]:
    """Lazy-import probe: can the late-rerank stage even attempt to run in this environment?

    Returns ``(True, None)`` when ``onnxruntime``, ``tokenizers``, and ``numpy`` all import
    cleanly, else ``(False, human_reason)``. Pure import check -- does NOT check whether the
    model has been fetched to disk; see :func:`load_late_model` for that. Mirrors
    ``retrieval_dense.dense_available()`` exactly.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError as exc:
        return False, (
            "late rerank unavailable: onnxruntime not installed -- "
            f"pip install 'tensor-grep[rerank]' ({exc})"
        )
    try:
        import tokenizers  # noqa: F401
    except ImportError as exc:
        return False, (
            "late rerank unavailable: tokenizers not installed -- "
            f"pip install 'tensor-grep[rerank]' ({exc})"
        )
    try:
        import numpy  # noqa: F401
    except ImportError as exc:
        return False, f"late rerank unavailable: numpy not installed ({exc})"
    return True, None


def default_model_dir() -> Path:
    """Resolve the fetched late-rerank model directory.

    ``TG_RERANK_MODEL_DIR`` if set, else ``~/.tensor-grep/models/LateOn-Code-edge`` -- the same
    single per-machine cache convention as ``retrieval_dense.default_model_dir()``, so the model
    is downloaded once and every repo/search reuses it.
    """
    env = os.environ.get("TG_RERANK_MODEL_DIR")
    if env:
        return Path(env)
    return Path.home() / ".tensor-grep" / Path(*_LATE_MODEL_SUBDIR)


@dataclass
class LateModel:
    """A loaded late-rerank encoder: the ONNX session + tokenizer + the parsed
    ``onnx_config.json`` contract (prefixes, per-role max lengths, embedding dim).

    ``session``/``tokenizer`` are typed ``Any`` (not ``onnxruntime.InferenceSession`` /
    ``tokenizers.Tokenizer``) so this module never needs those packages importable at type-check
    time -- mirrors how ``retrieval_dense.DenseIndex`` stores its injected ``model: Any``.
    """

    session: Any
    tokenizer: Any
    query_prefix: str
    document_prefix: str
    query_length: int
    document_length: int
    embedding_dim: int


def load_late_model(model_dir: str | Path) -> LateModel:
    """Load the ONNX session + tokenizer + ``onnx_config.json`` contract from ``model_dir``.

    Raises :class:`LateRerankUnavailableError` (recoverable) when the directory, or any of the 3
    required files (``model_int8.onnx``, ``tokenizer.json``, ``onnx_config.json``), is missing --
    "model not fetched" is an expected, visibly-degraded state, not a crash. Raises
    :class:`~tensor_grep.backends.base.BackendExecutionError` (unrecoverable) when all 3 files
    are present but fail to parse/load (corrupt or incompatible) -- a genuine backend fault.
    """
    path = Path(model_dir)
    missing = not path.is_dir() or any(
        not (path / name).is_file() for name in _REQUIRED_MODEL_FILES
    )
    if missing:
        raise LateRerankUnavailableError(
            "late rerank unavailable: model not fetched -- expected "
            f"{', '.join(_REQUIRED_MODEL_FILES)} under {path}; run "
            "`python -m tensor_grep.core.retrieval_late --fetch` (or set TG_RERANK_MODEL_DIR) "
            "to provide one"
        )
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        config = json.loads((path / "onnx_config.json").read_text(encoding="utf-8"))
        tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        session = ort.InferenceSession(
            str(path / "model_int8.onnx"), providers=["CPUExecutionProvider"]
        )
        return LateModel(
            session=session,
            tokenizer=tokenizer,
            query_prefix=str(config["query_prefix"]),
            document_prefix=str(config["document_prefix"]),
            query_length=int(config["query_length"]),
            document_length=int(config["document_length"]),
            embedding_dim=int(config["embedding_dim"]),
        )
    except Exception as exc:  # any load failure here is a genuine backend fault
        raise BackendExecutionError(
            f"late rerank model at {path} failed to load (corrupt or incompatible): {exc}"
        ) from exc


def _l2_normalize_rows(matrix: np.ndarray) -> np.ndarray:
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def _encode_tokens(model: LateModel, text: str, *, is_query: bool) -> np.ndarray:
    """Tokenize + run the ONNX session for one text, returning a per-token L2-normalized
    ``(T, D)`` matrix (``D == model.embedding_dim``).

    Applies the role-appropriate prefix and max length from ``onnx_config.json``
    (``query_prefix`` + ``query_length``, or ``document_prefix`` + ``document_length``), further
    hard-capped at ``_MAX_TOKEN_LENGTH`` (512) regardless of the configured value -- the design
    doc's "512-token truncation guard".

    Not thread-safe for concurrent calls sharing one ``LateModel``: ``tokenizer.enable_truncation``
    mutates the shared tokenizer's truncation setting in place before each encode. The current
    caller (``LateReranker.rerank``) encodes sequentially, so this is not a live bug today: a
    future concurrent/parallel caller would need a tokenizer clone per worker.
    """
    import numpy as np

    prefix = model.query_prefix if is_query else model.document_prefix
    configured_length = model.query_length if is_query else model.document_length
    effective_length = max(1, min(configured_length, _MAX_TOKEN_LENGTH))
    try:
        model.tokenizer.enable_truncation(max_length=effective_length)
        encoding = model.tokenizer.encode(prefix + text)
        input_ids = np.asarray([encoding.ids], dtype=np.int64)
        attention_mask = np.asarray([encoding.attention_mask], dtype=np.int64)
        outputs = model.session.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )
        raw = np.asarray(outputs[0][0], dtype=np.float32)
    except Exception as exc:
        raise BackendExecutionError(
            f"late rerank encode failed for a {len(text)}-char input: {exc}"
        ) from exc
    if raw.ndim != 2 or raw.shape[1] != model.embedding_dim:
        raise LateRerankUnavailableError(
            "late rerank unavailable: encoder produced a malformed embedding shape "
            f"{raw.shape} (expected (*, {model.embedding_dim}))"
        )
    return _l2_normalize_rows(raw)


def build_late_encoder(model: LateModel, *, is_query: bool) -> Callable[[str], np.ndarray]:
    """Build a real (non-stub) ``encode: str -> (T, D) ndarray`` callable for one role.

    The returned callable matches :class:`LateReranker`'s injected ``encode`` signature exactly
    -- this is the piece T5's seam wiring uses to supply role-aware encoders (query vs document)
    once ``rerank_hybrid`` splices this stage in. See :func:`load_late_reranker` for a
    ready-to-use :class:`LateReranker` wired with the document-role encoder.
    """

    def encode(text: str) -> np.ndarray:
        return _encode_tokens(model, text, is_query=is_query)

    return encode


def load_late_reranker(model_dir: str | Path | None = None) -> LateReranker:
    """Convenience constructor: load the model and wire a real encoder into :class:`LateReranker`.

    NOTE: :meth:`LateReranker.rerank` (T2) calls the SAME injected ``encode`` for both the query
    text and every candidate chunk -- it does not yet distinguish roles. This constructor wires
    the DOCUMENT-role encoder (chunks dominate call volume: one query encode vs N chunk encodes
    per search) as a conservative default. The asymmetric ``query_prefix``/``document_prefix`` +
    ``query_length``/``document_length`` in ``onnx_config.json`` are both read and available via
    ``build_late_encoder(model, is_query=True/False)`` for T5's seam wiring to route correctly.
    """
    resolved_dir = model_dir if model_dir is not None else default_model_dir()
    model = load_late_model(resolved_dir)
    return LateReranker(build_late_encoder(model, is_query=False))


# ---------------------------------------------------------------------------------------------
# T4 -- checksum-pinned fetch of the LateOn-Code-edge model files.
# ---------------------------------------------------------------------------------------------

# `lightonai/LateOn-Code-edge` (Apache-2.0; design doc "Model + LICENSE"). Pinned to a fixed
# commit SHA via a `resolve/<sha>/...` URL (never `/resolve/main/...`) so the fetched content is
# immutable regardless of future upstream changes.
_HF_REPO = "lightonai/LateOn-Code-edge"
_HF_REVISION = "07ef20f406c86badca122464808f4cac2f6e4b25"
_HF_RESOLVE_BASE = f"https://huggingface.co/{_HF_REPO}/resolve/{_HF_REVISION}"

# filename -> (sha256_hex, exact_byte_size). Computed by downloading each file from the pinned
# revision above and hashing locally, verified 2026-07-09 via TWO independent tools (Python
# `hashlib.sha256` and the `sha256sum` CLI produced byte-identical digests) -- see
# supply-chain-hardening skill H6 "SHA-confirmation discipline": never trust an agent-reported or
# sidecar SHA, always download+hash to confirm.
_FETCH_MANIFEST: dict[str, tuple[str, int]] = {
    "model_int8.onnx": (
        "eac35bdaa862e2762e6455337f7a3e704b05dbc4259f00929fcc8e10207f11c7",
        17_228_399,
    ),
    "tokenizer.json": (
        "a388b94942e98e5c661c6c23f919842285738bfd123a0d148dea0c56287505d0",
        3_583_847,
    ),
    "onnx_config.json": (
        "fa4fef89820dcdc33c5504c62c1d5efc19603cfbfebf02368a70d51a4dbe6651",
        792,
    ),
}

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024  # per-file cap; the largest pinned file is ~17.2 MB
_DOWNLOAD_TIMEOUT_S = 60.0
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def _download_bounded(url: str, *, max_bytes: int, timeout_s: float) -> bytes:
    """Stream ``url`` fully into memory, refusing to exceed ``max_bytes``
    (supply-chain-hardening H2: byte-capped + time-bound). The cap is enforced per-chunk during
    the read, not after buffering the whole body, so an oversized response cannot exhaust memory
    before the cap trips.

    Raises a plain ``OSError``/``ValueError`` on any failure (network error, timeout, or the byte
    cap). The caller (:func:`fetch_late_model`) wraps ALL of this uniformly into
    :class:`~tensor_grep.backends.base.BackendExecutionError`.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "tensor-grep-rerank-fetch"})
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        buffer = bytearray()
        while True:
            chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise ValueError(f"{url} exceeded the {max_bytes}-byte cap")
        return bytes(buffer)


def fetch_late_model(dest_dir: str | Path | None = None) -> Path:
    """Download the 3 pinned LateOn-Code-edge files into ``dest_dir``, checksum-gated + atomic.

    Fail-closed contract (supply-chain-hardening H2/H3): each file is streamed with a byte cap +
    timeout, verified against a hard-coded SHA-256 pin from a PINNED HF revision (``_HF_REVISION``,
    never ``main``) BEFORE anything lands at ``dest_dir``. On any download failure or checksum
    mismatch, the temp download directory is discarded and :class:`BackendExecutionError` is
    raised -- no partial or unverified file is ever left where :func:`load_late_model` would find
    it.

    The final install step (moving the verified temp directory to ``dest_dir``) is a single
    ``os.replace`` -- atomic on both POSIX and Windows PROVIDED ``dest_dir`` does not already
    exist. If ``dest_dir`` already holds a previous install (a re-fetch), it is removed
    immediately before the replace: ``os.replace`` cannot atomically overwrite a non-empty
    directory on Windows (verified empirically: ``PermissionError [WinError 5] Access is
    denied``), so a plain overwrite is not available cross-platform. This narrows, but does not
    eliminate, the crash window between the rmtree and the replace; the new copy is fully
    verified before the old one is ever touched, and a re-run of ``--fetch`` is idempotent.
    """
    dest = Path(dest_dir) if dest_dir is not None else default_model_dir()
    dest.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = tempfile.mkdtemp(dir=str(dest.parent), prefix=".tg-rerank-fetch-")
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
                    f"late rerank fetch failed downloading {filename} from {url}: {exc}"
                ) from exc

            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != expected_sha256 or len(data) != expected_size:
                raise BackendExecutionError(
                    f"late rerank fetch checksum mismatch for {filename}: expected "
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
    """Entry point for ``python -m tensor_grep.core.retrieval_late --fetch``."""
    parser = argparse.ArgumentParser(
        prog="python -m tensor_grep.core.retrieval_late",
        description=(
            "Fetch the pinned LateOn-Code-edge late-rerank model files (checksum-verified)."
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
        help="Override the fetch destination (else TG_RERANK_MODEL_DIR, or the default cache dir).",
    )
    args = parser.parse_args(argv)
    if not args.fetch:
        parser.print_help()
        return 2
    try:
        dest = fetch_late_model(args.model_dir)
    except Exception as exc:
        print(f"tg: late rerank model fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"tg: late rerank model fetched to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_fetch_cli())
