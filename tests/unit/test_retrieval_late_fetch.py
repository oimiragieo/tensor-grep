"""Tests for the checksum-pinned LateOn-Code-edge model fetch (design doc "T4",
docs/plans/design-tensor-grep-late-rerank-2026-07-09.md).

NO real network access here -- `urllib.request.urlopen` is monkeypatched to a deterministic fake
response for every test (supply-chain-hardening H2/H3: byte-capped + time-bound downloads,
checksum-gated fail-closed installs). `retrieval_late._FETCH_MANIFEST` (the real pinned
17.2MB/3.5MB/792B manifest) is also monkeypatched to a small synthetic manifest in the tests that
need a file to actually pass verification -- SHA-256 is preimage-resistant, so there is no way to
construct a small fake payload that matches the REAL pinned hashes; the checksum-mismatch/atomicity
tests instead intentionally serve WRONG bytes against the real manifest.
"""

from __future__ import annotations

import hashlib
import urllib.request
from typing import Any

import pytest

from tensor_grep.backends.base import BackendExecutionError
from tensor_grep.core import retrieval_late


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
        for name in retrieval_late._FETCH_MANIFEST
    }
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(wrong_payloads))

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="checksum mismatch"):
        retrieval_late.fetch_late_model(dest)

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
    monkeypatch.setattr(retrieval_late, "_FETCH_MANIFEST", fake_manifest)
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
        retrieval_late.fetch_late_model(dest)

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
        retrieval_late.fetch_late_model(dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_download_exceeding_byte_cap_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # H2 byte-cap enforcement: shrink the cap to 10 bytes and serve 11 -- must be refused, not
    # silently truncated or accepted.
    monkeypatch.setattr(retrieval_late, "_MAX_DOWNLOAD_BYTES", 10)
    oversized = b"x" * 11
    first_filename = next(iter(retrieval_late._FETCH_MANIFEST))
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen({first_filename: oversized}))

    dest = tmp_path / "model-dest"
    with pytest.raises(BackendExecutionError, match="byte cap"):
        retrieval_late.fetch_late_model(dest)

    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_respects_TG_RERANK_MODEL_DIR(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    target_dir = tmp_path / "env-configured-model-dir"
    monkeypatch.setenv("TG_RERANK_MODEL_DIR", str(target_dir))

    fake_payloads = {
        name: f"synthetic content for {name}".encode() for name in retrieval_late._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_late, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    # No explicit dest_dir -- must fall back to default_model_dir(), which itself must honor
    # TG_RERANK_MODEL_DIR.
    result = retrieval_late.fetch_late_model()

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
        name: f"content v1 for {name}".encode() for name in retrieval_late._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_late, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    result1 = retrieval_late.fetch_late_model(dest)
    assert result1 == dest
    for name, data in fake_payloads.items():
        assert (dest / name).read_bytes() == data

    # Re-fetch with DIFFERENT content under the same filenames -- must fully replace, not merge.
    fake_payloads_v2 = {
        name: f"content v2 for {name}".encode() for name in retrieval_late._FETCH_MANIFEST
    }
    fake_manifest_v2 = {
        name: (hashlib.sha256(data).hexdigest(), len(data))
        for name, data in fake_payloads_v2.items()
    }
    monkeypatch.setattr(retrieval_late, "_FETCH_MANIFEST", fake_manifest_v2)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads_v2))

    result2 = retrieval_late.fetch_late_model(dest)
    assert result2 == dest
    for name, data in fake_payloads_v2.items():
        assert (dest / name).read_bytes() == data


def test_fetch_cli_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake_payloads = {
        name: f"cli content for {name}".encode() for name in retrieval_late._FETCH_MANIFEST
    }
    fake_manifest = {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in fake_payloads.items()
    }
    monkeypatch.setattr(retrieval_late, "_FETCH_MANIFEST", fake_manifest)
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(fake_payloads))

    dest = tmp_path / "cli-model-dest"
    exit_code = retrieval_late._fetch_cli(["--fetch", "--model-dir", str(dest)])

    assert exit_code == 0
    for name, data in fake_payloads.items():
        assert (dest / name).read_bytes() == data


def test_fetch_cli_without_fetch_flag_prints_help_and_exits_nonzero() -> None:
    exit_code = retrieval_late._fetch_cli([])
    assert exit_code == 2


def test_fetch_cli_returns_nonzero_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def _raising_urlopen(request: urllib.request.Request, timeout: float | None = None) -> Any:
        raise OSError("simulated connection reset")

    monkeypatch.setattr(urllib.request, "urlopen", _raising_urlopen)

    dest = tmp_path / "cli-model-dest"
    exit_code = retrieval_late._fetch_cli(["--fetch", "--model-dir", str(dest)])

    assert exit_code == 1
    assert not dest.exists()
