"""Audit #5: native-asset downloads (native front door + detached refresh/upgrade) must be
byte-capped so an oversized/malicious CDN response can't exhaust disk before checksum verification.
_download_native_frontdoor_asset enforces the cap via urlretrieve's reporthook (actual bytes read)."""

from pathlib import Path

import pytest

from tensor_grep.cli import main as tg_main


def test_oversized_download_raises_via_reporthook(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 100)

    def _fake_urlretrieve(url, filename, reporthook=None, **_kwargs):
        # Simulate the CDN streaming past the cap: 200 bytes downloaded (block 200 x 1 byte).
        if reporthook is not None:
            reporthook(200, 1, -1)  # total_size = -1 (unknown / untrusted)

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    with pytest.raises(RuntimeError, match="exceeded"):
        tg_main._download_native_frontdoor_asset("https://example/asset.bin", tmp_path / "a.bin")


def test_within_cap_download_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(tg_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 1000)
    dest = tmp_path / "b.bin"

    def _fake_urlretrieve(url, filename, reporthook=None, **_kwargs):
        if reporthook is not None:
            reporthook(1, 42, -1)  # 42 bytes, well under the cap
        Path(filename).write_bytes(b"x" * 42)

    monkeypatch.setattr("urllib.request.urlretrieve", _fake_urlretrieve)
    tg_main._download_native_frontdoor_asset("https://example/asset.bin", dest)
    assert dest.read_bytes() == b"x" * 42
