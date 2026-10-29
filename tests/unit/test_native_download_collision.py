import hashlib
import io
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import native_download_guard, native_frontdoor


def _configure(monkeypatch, payload):
    candidate = cli_main._NativeFrontdoorAssetCandidate(flavor="cpu", asset_name="tg-test")
    monkeypatch.setattr(
        cli_main, "_native_frontdoor_download_candidates", lambda _: [(candidate, "fixture")]
    )
    monkeypatch.setattr(
        cli_main,
        "_fetch_native_frontdoor_checksums",
        lambda _: f"{hashlib.sha256(payload).hexdigest()}  tg-test\n",
    )
    monkeypatch.setattr(native_frontdoor, "uuid4", lambda: SimpleNamespace(hex="fixture"))
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _: "1.2.3")
    monkeypatch.setattr(cli_main, "_native_tg_version_matches", lambda *_: True)
    monkeypatch.setattr(cli_main, "_write_native_frontdoor_metadata", lambda *_, **__: None)


def test_NativeDownload_WhenTempNameAlreadyExists_PreservesExistingFile(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")
    temporary = tmp_path / "tg.exe.fixture.tmp"
    temporary.write_bytes(b"existing user data")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("network reached")
    )

    with pytest.raises(RuntimeError, match="asset unavailable"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert temporary.read_bytes() == b"existing user data"
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenCreatedAndVerified_RemovesOwnedTemp(tmp_path, monkeypatch):
    payload = b"verified asset"
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))

    cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert (tmp_path / "tg.exe").read_bytes() == payload
    assert not (tmp_path / "tg.exe.fixture.tmp").exists()


def test_NativeDownload_WhenTransferFails_RemovesPartialOwnedTemp(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")

    class BrokenResponse(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("fixture transfer failure")
            return super().read(size)

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_args, **_kwargs: BrokenResponse(b"part")
    )
    with pytest.raises(RuntimeError, match="fixture transfer failure"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert not (tmp_path / "tg.exe.fixture.tmp").exists()
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenTransferExceedsLimit_RemovesPartialOwnedTemp(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")
    monkeypatch.setattr(cli_main, "_MAX_NATIVE_ASSET_DOWNLOAD_BYTES", 4)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"12345"))

    with pytest.raises(RuntimeError, match="download exceeded"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert not (tmp_path / "tg.exe.fixture.tmp").exists()
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenDescriptorWrappingFails_ClosesAndRemovesTemp(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")

    def fail_wrap(*_args, **_kwargs):
        assert _kwargs["closefd"] is False
        raise OSError("fixture fdopen failure")

    monkeypatch.setattr(native_frontdoor.os, "fdopen", fail_wrap)
    with pytest.raises(RuntimeError, match="fixture fdopen failure"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert not (tmp_path / "tg.exe.fixture.tmp").exists()


def test_NativeDownload_WhenPartialCleanupFails_ReportsAndLeavesFile(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")
    temporary = tmp_path / "tg.exe.fixture.tmp"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network")),
    )
    attempts = []

    def refuse_cleanup(path, _guard):
        attempts.append(path)
        raise PermissionError("fixture cleanup denied")

    if sys.platform == "win32":
        monkeypatch.setattr(native_download_guard, "discard_guarded", refuse_cleanup)
    else:
        unlink = Path.unlink

        def refuse_unlink(path, *args, **kwargs):
            if path == temporary:
                refuse_cleanup(path, None)
            return unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", refuse_unlink)
    with pytest.raises(RuntimeError, match="fixture cleanup denied"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert attempts == [temporary]
    assert temporary.is_file()
    assert not (tmp_path / "tg.exe").exists()
