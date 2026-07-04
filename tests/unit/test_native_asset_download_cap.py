"""Audit #5: native-asset downloads (native front door + detached refresh/upgrade) must be
byte-capped so an oversized/malicious CDN response can't exhaust disk/memory before checksum
verification. All three sites now route through the capped _download_native_frontdoor_asset."""

import urllib.request

import pytest

from tensor_grep.cli import main as tg_main


class _FakeResponse:
    def __init__(self, total_bytes: int) -> None:
        self._left = total_bytes

    def read(self, size: int) -> bytes:
        if self._left <= 0:
            return b""
        give = min(size, self._left)
        self._left -= give
        return b"x" * give

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def test_oversized_download_raises_before_finishing(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 100)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _FakeResponse(500))
    with pytest.raises(RuntimeError, match="exceeded"):
        tg_main._download_native_frontdoor_asset("https://example/asset.bin", tmp_path / "a.bin")


def test_within_cap_download_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 1000)
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _FakeResponse(42))
    dest = tmp_path / "b.bin"
    tg_main._download_native_frontdoor_asset("https://example/asset.bin", dest)
    assert dest.read_bytes() == b"x" * 42
