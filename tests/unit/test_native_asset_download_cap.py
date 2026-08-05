"""Audit #5: native-asset downloads (native front door + detached refresh/upgrade) must be
byte-capped so an oversized/malicious CDN response can't exhaust disk before checksum verification.
_download_native_frontdoor_asset enforces the cap by counting ACTUAL bytes read from the streamed
urlopen response as they arrive (frontdoor-download-held-fd task: urlretrieve's reporthook-based
enforcement was replaced by a held-fd urlopen + chunked-read loop to close a TOCTOU gap; the cap
semantics -- count real bytes read, not attacker-controlled Content-Length -- are unchanged)."""

import pytest

from tensor_grep.cli import main as tg_main


class _FakeResponse:
    """Minimal stand-in for the context-managed object urlopen() returns: .read(n) yields
    successive chunks from a fixed list, then empty bytes (EOF), same as a real socket response."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc_info: object) -> bool:
        return False


def test_oversized_download_raises_via_streamed_byte_count(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 100)

    # Simulate the CDN streaming past the cap: 200 bytes in one chunk, well over the 100-byte cap.
    def _fake_urlopen(url, timeout=None):
        return _FakeResponse([b"x" * 200])

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    dest = tmp_path / "a.bin"
    with pytest.raises(RuntimeError, match="exceeded"):
        tg_main._download_native_frontdoor_asset("https://example/asset.bin", dest)
    # Fail-closed: no partial/oversized artifact left behind at a valid-looking install path.
    assert not dest.exists()


def test_within_cap_download_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 1000)
    dest = tmp_path / "b.bin"

    def _fake_urlopen(url, timeout=None):
        return _FakeResponse([b"x" * 42])  # well under the cap

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    tg_main._download_native_frontdoor_asset("https://example/asset.bin", dest)
    assert dest.read_bytes() == b"x" * 42
