import io
import os
import sys
import urllib.request
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import native_download_guard
from tests.unit.test_native_download_collision import _configure

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows handle-bound cleanup")


def test_NativeDownload_WhenTempNameIsReplaced_DoesNotDeleteReplacement(tmp_path, monkeypatch):
    payload = b"verified fixture asset"
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    temporary = tmp_path / "tg.exe.fixture.tmp"
    original = tmp_path / "original-download"

    def replace_before_refusal(path, _asset, _manifest):
        assert Path(path) == temporary
        temporary.rename(original)
        temporary.write_bytes(b"replacement user data")
        return "checksum mismatch"

    monkeypatch.setattr(cli_main, "_native_frontdoor_checksum_error", replace_before_refusal)
    with pytest.raises(
        RuntimeError, match=r"before installation.*changed; cleanup refused"
    ) as caught:
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert "checksum mismatch" in str(caught.value)
    assert original.read_bytes() == payload
    assert temporary.read_bytes() == b"replacement user data"
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenTempBecomesHardlink_DoesNotDeleteReplacement(tmp_path, monkeypatch):
    payload = b"verified fixture asset"
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    temporary = tmp_path / "tg.exe.fixture.tmp"
    target = tmp_path / "other-file"
    target.write_bytes(b"other user data")

    def replace_before_refusal(path, _asset, _manifest):
        assert Path(path) == temporary
        temporary.unlink()
        os.link(target, temporary)
        return "checksum mismatch"

    monkeypatch.setattr(cli_main, "_native_frontdoor_checksum_error", replace_before_refusal)
    with pytest.raises(RuntimeError, match=r"before installation.*changed; cleanup refused"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert target.read_bytes() == temporary.read_bytes() == b"other user data"
    assert os.path.samefile(target, temporary)


def test_NativeDownload_WhenGuardAcquisitionFails_ReportsPotentialOrphan(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")

    def fail_network(*_args, **_kwargs):
        raise OSError("fixture network failure")

    def fail_guard(*_args, **_kwargs):
        raise OSError("fixture guard failure")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.setattr(native_download_guard, "acquire_guard", fail_guard)
    with pytest.raises(
        RuntimeError, match="after OSError; cleanup unverified; temporary may remain"
    ):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert (tmp_path / "tg.exe.fixture.tmp").is_file()
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenCleanupIsUnverified_DoesNotTryAnotherCandidate(tmp_path, monkeypatch):
    _configure(monkeypatch, b"verified asset")
    first = cli_main._NativeFrontdoorAssetCandidate(flavor="nvidia", asset_name="tg-first")
    second = cli_main._NativeFrontdoorAssetCandidate(flavor="cpu", asset_name="tg-second")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda _version: [(first, "first"), (second, "second")],
    )
    requested = []

    def fail_network(url, **_kwargs):
        requested.append(url)
        raise OSError("fixture network failure")

    def fail_guard(*_args, **_kwargs):
        raise OSError("fixture guard failure")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    monkeypatch.setattr(native_download_guard, "acquire_guard", fail_guard)
    with pytest.raises(
        native_download_guard.NativeDownloadCleanupError, match="cleanup unverified"
    ):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert requested == ["first"]
    assert (tmp_path / "tg.exe.fixture.tmp").is_file()
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenCleanupAndGuardCloseFail_ReportsBoth(tmp_path, monkeypatch):
    def fail_cleanup(*_args, **_kwargs):
        raise OSError("fixture cleanup failure")

    def fail_close(_fd):
        raise OSError("fixture close failure")

    monkeypatch.setattr(native_download_guard, "discard_guarded", fail_cleanup)
    monkeypatch.setattr(native_download_guard.os, "close", fail_close)
    with pytest.raises(native_download_guard.NativeDownloadCleanupError) as caught:
        native_download_guard.dispose_guarded(
            tmp_path / "fixture", 123, state="before installation"
        )

    assert "fixture cleanup failure" in str(caught.value)
    assert "fixture close failure" in str(caught.value)


@pytest.mark.parametrize("guard_fails", [False, True], ids=["guard-held", "guard-failed"])
def test_NativeDownload_WhenWriterCloseFails_DoesNotTryAnotherCandidate(
    tmp_path, monkeypatch, guard_fails
):
    _configure(monkeypatch, b"verified asset")
    first = cli_main._NativeFrontdoorAssetCandidate(flavor="nvidia", asset_name="tg-first")
    second = cli_main._NativeFrontdoorAssetCandidate(flavor="cpu", asset_name="tg-second")
    monkeypatch.setattr(
        cli_main,
        "_native_frontdoor_download_candidates",
        lambda _version: [(first, "first"), (second, "second")],
    )
    requests = []

    def response(url, **_kwargs):
        requests.append(url)
        if guard_fails:
            raise OSError("fixture network failure")
        return io.BytesIO(b"verified asset")

    real_fdopen, real_close = os.fdopen, os.close
    writer = []

    def capture_fdopen(fd, *args, **kwargs):
        writer.append(fd)
        return real_fdopen(fd, *args, **kwargs)

    def fail_writer_close(fd):
        if writer and fd == writer[0]:
            writer.clear()
            real_close(fd)
            raise OSError("fixture writer close failure")
        return real_close(fd)

    monkeypatch.setattr(urllib.request, "urlopen", response)
    monkeypatch.setattr(os, "fdopen", capture_fdopen)
    monkeypatch.setattr(os, "close", fail_writer_close)
    if guard_fails:
        monkeypatch.setattr(
            native_download_guard,
            "acquire_guard",
            lambda *_args: (_ for _ in ()).throw(OSError("fixture guard failure")),
        )
    with pytest.raises(native_download_guard.NativeDownloadCleanupError, match="writer close"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert requests == ["first"]
    assert not (tmp_path / "tg.exe").exists()


def test_NativeDownload_WhenDeleteHandleCloseFails_PreservesIdentityFailure(tmp_path, monkeypatch):
    payload = b"verified fixture asset"
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    temporary = tmp_path / "tg.exe.fixture.tmp"
    original = tmp_path / "original-download"
    guard = cli_main._download_native_frontdoor_asset("fixture", temporary, retain_guard=True)
    assert isinstance(guard, int)
    temporary.rename(original)
    temporary.write_bytes(b"replacement user data")
    real_close = os.close

    def fail_delete_handle_close(fd):
        real_close(fd)
        if fd != guard:
            raise OSError("fixture delete handle close failure")

    monkeypatch.setattr(os, "close", fail_delete_handle_close)
    with pytest.raises(native_download_guard.NativeDownloadCleanupError) as caught:
        native_download_guard.dispose_guarded(temporary, guard, state="before installation")

    assert "changed; cleanup refused" in str(caught.value)
    assert "fixture delete handle close failure" in str(caught.value)
    assert temporary.read_bytes() == b"replacement user data"
    assert original.read_bytes() == payload


def test_NativeDownload_WhenCleanupFailsAfterPublish_ReportsDestinationMayHaveChanged(
    tmp_path, monkeypatch
):
    payload = b"verified fixture asset"
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))

    def fail_cleanup(*_args, **_kwargs):
        raise OSError("fixture cleanup failure")

    monkeypatch.setattr(native_download_guard, "discard_guarded", fail_cleanup)
    with pytest.raises(RuntimeError, match="destination may have changed: fixture cleanup failure"):
        cli_main._install_release_native_frontdoor("1.2.3", tmp_path / "tg.exe")

    assert (tmp_path / "tg.exe").read_bytes() == payload
    assert (tmp_path / "tg.exe.fixture.tmp").read_bytes() == payload
