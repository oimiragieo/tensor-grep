"""Tests for the checksum-pinned potion-code-16M dense-embedding model fetch (mirrors
``test_retrieval_late_fetch.py``'s T4 LateOn-Code-edge precedent -- see that file's module
docstring for the full rationale; this is the twin for the DENSE leg, closing the gap where
``DenseUnavailableError`` used to say "There is no `tg` fetch command for this leg yet").

NO real network access here -- `urllib.request.urlopen` is monkeypatched to a deterministic fake
response for every test (supply-chain-hardening H2/H3: byte-capped + time-bound downloads,
checksum-gated fail-closed installs). `retrieval_dense._FETCH_MANIFEST` (the real pinned
~64.3MB/1.0MB/97B manifest) is also monkeypatched to a small synthetic manifest in the tests that
need a file to actually pass verification -- SHA-256 is preimage-resistant, so there is no way to
construct a small fake payload that matches the REAL pinned hashes; the checksum-mismatch/atomicity
tests instead intentionally serve WRONG bytes against the real manifest.

The ONE real-network fetch (proving the pinned manifest against the actual HuggingFace host) lives
in ``scripts/dogfood/`` (opt-in), never in this unit suite.
"""

from __future__ import annotations

import hashlib
import itertools
import sys
import types
import urllib.request
from typing import Any

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core import retrieval_dense


class _FakeHTTPResponse:
    """Duck-typed stand-in for the context-managed object `urllib.request.urlopen` returns."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeDripResponse:
    """Duck-typed fake response that ignores the requested read size and instead yields a fixed
    sequence of small chunks, one per `.read()` call, then EOF -- simulates a slow-drip server
    that returns a little data on every recv (each individual read small/fast enough to dodge the
    per-recv socket timeout and the byte cap) without ever finishing the file. Deliberately
    FINITE, not an infinite generator: if the total-deadline check under test were missing or
    broken, this response still drains normally and the test fails fast on an assertion mismatch
    instead of hanging the suite (anti-hang-test-protocol).
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = [*chunks, b""]  # trailing b"" = EOF once the scripted chunks run out
        self._idx = 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._chunks[self._idx] if self._idx < len(self._chunks) else b""
        self._idx += 1
        return chunk

    def __enter__(self) -> _FakeDripResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _make_fake_urlopen(payloads: dict[str, bytes]) -> Any:
    """Build a fake `urlopen(request, timeout=...)` keyed by the request URL's final path
    segment (the filename) -- looks up `payloads[filename]` and returns it wrapped as a fake
    HTTP response; raises `KeyError` (a test bug, not a production path) for an unexpected URL.
    """

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        filename = request.full_url.rsplit("/", 1)[-1]
        return _FakeHTTPResponse(payloads[filename])

    return fake_urlopen


def test_fetch_rejects_checksum_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Every file downloads "successfully" (200 OK, bytes returned) but the content does not
    # match ANY of the real pinned SHA-256 hashes -- must be rejected, not silently accepted.
    wrong_payloads = {
        name: b"wrong bytes, not the pinned content for " + name.encode()
        for name in retrieval_dense._FETCH_MANIFEST
    }
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(wrong_payloads))

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="checksum mismatch"):
        retrieval_dense.fetch_dense_model(dest)

    # Fail-closed: no files land, no partial state left behind anywhere under tmp_path.
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_is_atomic_on_partial_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # file_a downloads AND verifies successfully; file_b's declared checksum does not match what
    # the fake server actually returns. The whole multi-file fetch must be all-or-nothing: even
    # though file_a is fully valid on its own, its verified bytes must NOT be left behind
    # anywhere once file_b's failure aborts the overall fetch.
    good_bytes = b"real verified content for file A"
    fake_manifest = {
        "file_a.bin": (hashlib.sha256(good_bytes).hexdigest(), len(good_bytes)),
        "file_b.bin": (hashlib.sha256(b"the CORRECT content for file B").hexdigest(), 30),
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _make_fake_urlopen({
            "file_a.bin": good_bytes,
            "file_b.bin": b"WRONG bytes served for file B!",
        }),
    )

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="checksum mismatch"):
        retrieval_dense.fetch_dense_model(dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_download_error_becomes_backend_execution_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A raw network failure (not a checksum mismatch) must ALSO be wrapped, never propagate as a
    # bare OSError, and must ALSO leave no partial state behind.
    def _raising_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise OSError("simulated connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen)

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="simulated connection reset"):
        retrieval_dense.fetch_dense_model(dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_download_exceeding_byte_cap_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # H2 byte-cap enforcement: shrink the cap to 10 bytes and serve 11 -- must be refused, not
    # silently truncated or accepted.
    monkeypatch.setattr(retrieval_dense, "_MAX_DOWNLOAD_BYTES", 10)
    oversized = b"x" * 11
    first_filename = next(iter(retrieval_dense._FETCH_MANIFEST))
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen({first_filename: oversized}))

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="byte cap"):
        retrieval_dense.fetch_dense_model(dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_exceeds_total_deadline_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # `_download_bounded` must bound the TOTAL wall-clock time of a download, not just the
    # per-recv socket timeout and the total byte cap. A malicious/compromised HF server could
    # slow-drip bytes forever -- each individual recv small and fast enough to dodge both existing
    # bounds -- and hang the fetch indefinitely (mirrors retrieval_late's Opus security-gate nit
    # #87 fix).
    #
    # No real sleep: the fake response drips 2 small chunks (well under the byte cap) and
    # `time.monotonic` is monkeypatched to jump past a shrunk 1s deadline on the SECOND deadline
    # check, so the total-deadline path trips deterministically and fast.
    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        return _FakeDripResponse([b"a" * 8, b"b" * 8])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TG_SEMANTIC_FETCH_DEADLINE_S", "1")

    # `_download_bounded`'s `time.monotonic()` call order: (1) `start`; (2) the deadline check
    # after the 1st chunk read (must NOT trip yet -- only "0.5s" elapsed); (3) the deadline check
    # after the 2nd chunk read (must trip -- "5.0s" elapsed, past the shrunk 1s deadline). The
    # schedule then repeats "5.0" forever so any incidental extra call never raises StopIteration.
    monotonic_values = itertools.chain([0.0, 0.5], itertools.repeat(5.0))
    monkeypatch.setattr(retrieval_dense.time, "monotonic", lambda: next(monotonic_values))

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="deadline"):
        retrieval_dense.fetch_dense_model(dest)

    # Fail-closed: no files land, no partial state left behind anywhere under tmp_path.
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_respects_TG_SEMANTIC_MODEL_DIR(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    target_dir = tmp_path / "env-configured-model-dir"
    monkeypatch.setenv("TG_SEMANTIC_MODEL_DIR", str(target_dir))

    fake_payloads = {
        name: f"synthetic content for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    # No explicit dest_dir -- must fall back to default_model_dir(), which itself must honor
    # TG_SEMANTIC_MODEL_DIR.
    result = retrieval_dense.fetch_dense_model()

    assert result == target_dir
    for name, data in fake_payloads.items():
        assert (target_dir / name).read_bytes() == data


def test_fetch_succeeds_end_to_end_and_is_idempotent_on_refetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # A full successful fetch, followed by a SECOND successful fetch to the same destination
    # (e.g. a user re-running `--fetch` to refresh) -- the second run must also succeed and
    # correctly replace the first install (exercises the "dest already exists" removal path,
    # which is required on Windows: os.replace cannot overwrite a non-empty directory there).
    dest = tmp_path / "model-dest"
    fake_payloads = {
        name: f"content v1 for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    result1 = retrieval_dense.fetch_dense_model(dest)
    assert result1 == dest
    for name, data in fake_payloads.items():
        assert (dest / name).read_bytes() == data

    # Re-fetch with DIFFERENT content under the same filenames -- must fully replace, not merge.
    fake_payloads_v2 = {
        name: f"content v2 for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    fake_manifest_v2 = {
        name: (hashlib.sha256(data).hexdigest(), len(data))
        for name, data in fake_payloads_v2.items()
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest_v2)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads_v2))

    result2 = retrieval_dense.fetch_dense_model(dest)
    assert result2 == dest
    for name, data in fake_payloads_v2.items():
        assert (dest / name).read_bytes() == data


def test_fetch_cli_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_payloads = {
        name: f"cli content for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    dest = tmp_path / "cli-model-dest"
    exit_code = retrieval_dense._fetch_cli(["--fetch", "--model-dir", str(dest)])

    assert exit_code == 0
    for name, data in fake_payloads.items():
        assert (dest / name).read_bytes() == data


def test_fetch_cli_without_fetch_flag_prints_help_and_exits_nonzero() -> None:
    exit_code = retrieval_dense._fetch_cli([])
    assert exit_code == 2


def test_fetch_cli_returns_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def _raising_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise OSError("simulated connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen)

    dest = tmp_path / "cli-model-dest"
    exit_code = retrieval_dense._fetch_cli(["--fetch", "--model-dir", str(dest)])

    assert exit_code == 1
    assert not dest.exists()


def test_not_fetched_error_names_the_new_fetch_command(tmp_path) -> None:
    """The DenseUnavailableError raised for "not fetched" must point at the module CLI this PR
    adds (F2 council must-fix: module CLI only, no new `tg`-registered surface) -- not the old
    "fetch it yourself via model2vec's own HuggingFace integration" workaround text."""
    missing_dir = tmp_path / "does-not-exist"
    with pytest.raises(retrieval_dense.DenseUnavailableError) as exc_info:
        retrieval_dense.load_dense_model(missing_dir)

    message = str(exc_info.value)
    assert "not fetched" in message
    assert "python -m tensor_grep.core.retrieval_dense --fetch" in message
    assert "save_pretrained" not in message  # the old unpinned/unchecksummed workaround is gone


def test_fetch_then_load_activates_hybrid_dense_leg_over_bm25_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """End-to-end proof that this PR's fetch front door is what the dense leg needed to go from
    BM25-only to genuinely fused hybrid ranking -- the concrete gap this PR closes.

    1. A (mocked-network) ``--fetch`` populates a model directory that satisfies
       :func:`~tensor_grep.core.retrieval_dense.load_dense_model`'s "is it fetched" check.
    2. An injected fake ``model2vec.StaticModel`` (no real model2vec install or real weights
       required -- that is ``TestRealFetchedModel``'s job in test_retrieval_dense.py, gated on the
       real fetched model) proves ``load_dense_model`` hands back something usable once the
       directory exists.
    3. Feeding that into :func:`~tensor_grep.core.reranker.rerank_hybrid` produces a DIFFERENT
       match order than :func:`~tensor_grep.core.reranker.rerank_by_bm25` alone -- the dense leg
       is genuinely contributing, reproducing test_reranker_hybrid.py's proven BM25-vs-hybrid
       disagreement fixture, but sourced through the real fetch+load path instead of a
       directly-constructed DenseIndex.
    """
    from tensor_grep.core.reranker import rerank_by_bm25, rerank_hybrid
    from tensor_grep.core.result import MatchLine, SearchResult
    from tensor_grep.core.retrieval_bm25 import Bm25Index
    from tensor_grep.core.retrieval_chunker import Chunk
    from tensor_grep.core.retrieval_dense import DenseIndex

    # --- 1. Mocked-network happy-path fetch (small synthetic manifest). ---
    dest = tmp_path / "model-dest"
    fake_payloads = {
        name: f"synthetic content for {name}".encode() for name in retrieval_dense._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_dense, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    result_dir = retrieval_dense.fetch_dense_model(dest)
    assert result_dir == dest
    for name in fake_manifest:
        assert (dest / name).is_file()

    # --- 2. Inject a fake model2vec so load_dense_model succeeds on the fetched (synthetic) dir,
    # WITHOUT a real model2vec install or real weights. ---
    vectors_by_text = {
        "parse_invoice": [0.0, 1.0],
        "helper_one": [1.0, 1.0],
        "helper_two": [1.0, 0.0],
        "invoice": [1.0, 0.0],
    }

    class _FakeStaticModel:
        def __init__(self, vectors: dict[str, list[float]]) -> None:
            self._vectors = vectors

        def encode(self, texts: list[str]) -> Any:
            import numpy as np

            return np.array([self._vectors[t] for t in texts], dtype=np.float32)

        @classmethod
        def from_pretrained(cls, path: str) -> _FakeStaticModel:
            return cls(vectors_by_text)

    fake_module = types.ModuleType("model2vec")
    fake_module.StaticModel = _FakeStaticModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "model2vec", fake_module)

    # Must NOT raise DenseUnavailableError("not fetched") anymore -- the directory now exists.
    model = retrieval_dense.load_dense_model(dest)

    # --- 3. Same disagreement fixture as test_reranker_hybrid.py's _build_scenario: the dense
    # leg is crafted to disagree with BM25 so fusing the two legs is observable in the output. ---
    chunks = [
        Chunk(file_path="f1.py", start_line=1, end_line=1, text="parse_invoice"),
        Chunk(file_path="f2.py", start_line=1, end_line=1, text="helper_one"),
        Chunk(file_path="f3.py", start_line=1, end_line=1, text="helper_two"),
    ]
    bm25_index = Bm25Index(chunks)
    dense_index = DenseIndex(chunks, model)
    result = SearchResult(
        matches=[
            MatchLine(line_number=1, text="parse_invoice", file="f1.py"),
            MatchLine(line_number=1, text="helper_one", file="f2.py"),
            MatchLine(line_number=1, text="helper_two", file="f3.py"),
        ],
        total_matches=3,
    )

    bm25_only = rerank_by_bm25(result, "invoice", [], index=bm25_index)
    hybrid = rerank_hybrid(result, "invoice", [], bm25_index=bm25_index, dense_index=dense_index)

    # SAME set of matches, but a DIFFERENT order -- the dense leg fetched+loaded via this PR's
    # front door is genuinely engaged, not a no-op that degrades back to BM25-only.
    assert {m.file for m in bm25_only.matches} == {m.file for m in hybrid.matches}
    assert [m.file for m in bm25_only.matches] == ["f1.py", "f2.py", "f3.py"]
    assert [m.file for m in hybrid.matches] == ["f1.py", "f3.py", "f2.py"]
