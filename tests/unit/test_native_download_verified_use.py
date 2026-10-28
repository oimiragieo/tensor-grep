import hashlib
import io
import mmap
import os
import subprocess
import sys
import urllib.request
from contextlib import ExitStack
from pathlib import Path

import pytest

from tensor_grep.cli import main as cli_main
from tensor_grep.cli import native_download_guard, native_frontdoor
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli import session_daemon_winsec as winsec
from tests.unit.test_native_download_collision import _configure

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native asset pinning")


def test_verified_read_refuses_overflow_and_wrong_digest(tmp_path):
    path = tmp_path / "asset"
    path.write_bytes(b"asset")
    expected = hashlib.sha256(b"asset").hexdigest()
    with path.open("rb") as source:
        assert native_download_guard.read_verified_asset(source.fileno(), 5, expected) == b"asset"
        with pytest.raises(native_download_guard.NativeDownloadCleanupError, match="changed"):
            native_download_guard.read_verified_asset(source.fileno(), 4, expected)
        with pytest.raises(native_download_guard.NativeDownloadCleanupError, match="changed"):
            native_download_guard.read_verified_asset(source.fileno(), 5, "0" * 64)


def test_guarded_download_creates_private_file(tmp_path, monkeypatch):
    payload = b"verified fixture asset"
    path = tmp_path / "asset.tmp"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    guard = cli_main._download_native_frontdoor_asset("fixture", path, retain_guard=True)
    assert isinstance(guard, int)
    try:
        import msvcrt

        sid = winsec.current_user_sid()
        details = winsec.owner_and_dacl_entries(msvcrt.get_osfhandle(guard))
        assert sid is not None and details is not None and details[1]
        assert all(principal == sid for principal, _mask in details[1])
    finally:
        native_download_guard.dispose_guarded(path, guard, state="fixture cleanup")


def test_ordinary_installer_refuses_missing_sid_before_download(tmp_path, monkeypatch):
    destination = tmp_path / "tg.exe"
    destination.write_bytes(b"existing launcher")
    _configure(monkeypatch, b"asset")
    monkeypatch.setattr(winsec, "current_user_sid", lambda: None)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *_args, **_kwargs: pytest.fail("unexpected download")
    )
    with pytest.raises(native_download_guard.NativeDownloadCleanupError, match="identity"):
        cli_main._install_release_native_frontdoor("1.2.3", destination)
    assert destination.read_bytes() == b"existing launcher"
    assert list(tmp_path.iterdir()) == [destination]


def test_verified_native_asset_cannot_be_swapped_before_version_probe(
    trusted_base_dir, tmp_path, monkeypatch
):
    payload = b"verified fixture asset"
    replacement = b"unverified fixture asset"
    folder = trusted_base_dir / f"native-verified-use-{tmp_path.name}"
    assert trust._ensure_secret_dir(folder)
    destination = folder / "tg.exe"
    original = folder / "original-download"
    destination.write_bytes(b"previous installation")
    _configure(monkeypatch, payload)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    checksum = cli_main._native_frontdoor_checksum_error
    observed = []

    def checksum_then_swap(path, asset, manifest):
        result = checksum(path, asset, manifest)
        assert result is None
        try:
            Path(path).rename(original)
            Path(path).write_bytes(replacement)
        except PermissionError:
            pass
        return result

    def probe(path):
        observed.append(Path(path).read_bytes())
        return "1.2.3"

    monkeypatch.setattr(cli_main, "_native_frontdoor_checksum_error", checksum_then_swap)
    monkeypatch.setattr(cli_main, "_native_tg_version", probe)
    cli_main._install_release_native_frontdoor("1.2.3", destination, _managed_source=True)
    assert observed and observed[0] == payload
    assert destination.read_bytes() == payload


def test_pin_rejects_existing_writable_mapping(trusted_base_dir, tmp_path, monkeypatch):
    folder = trusted_base_dir / f"native-mapping-{tmp_path.name}"
    assert trust._ensure_secret_dir(folder)
    path = folder / "asset.tmp"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"asset"))
    guard = cli_main._download_native_frontdoor_asset("fixture", path, retain_guard=True)
    assert isinstance(guard, int)
    try:
        with ExitStack() as held:
            parent = native_frontdoor._pin_managed_directory(folder, held)
            with native_download_guard.hold_private_asset(path, guard, parent):
                pass
            with path.open("r+b") as source:
                mapping = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_WRITE)
            try:
                with pytest.raises(
                    native_download_guard.NativeDownloadCleanupError, match="pinned"
                ):
                    with native_download_guard.hold_private_asset(path, guard, parent):
                        pass
            finally:
                mapping.close()
            with native_download_guard.hold_private_asset(path, guard, parent):
                pass
    finally:
        native_download_guard.dispose_guarded(path, guard, state="fixture cleanup")


def test_windows_executable_runs_while_asset_is_pinned(trusted_base_dir, tmp_path, monkeypatch):
    folder = trusted_base_dir / f"native-executable-{tmp_path.name}"
    assert trust._ensure_secret_dir(folder)
    path = folder / "where.exe"
    system_executable = Path(os.environ["SystemRoot"]) / "System32" / "where.exe"
    payload = system_executable.read_bytes()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))
    guard = cli_main._download_native_frontdoor_asset("fixture", path, retain_guard=True)
    assert isinstance(guard, int)
    try:
        with ExitStack() as held:
            parent = native_frontdoor._pin_managed_directory(folder, held)
            with native_download_guard.hold_private_asset(path, guard, parent):
                result = subprocess.run([str(path), "/?"], capture_output=True, timeout=30)
                assert result.returncode == 0, result.stderr
    finally:
        native_download_guard.dispose_guarded(path, guard, state="fixture cleanup")


def test_pin_refuses_replacement_and_preserves_it(trusted_base_dir, tmp_path, monkeypatch):
    folder = trusted_base_dir / f"native-replacement-{tmp_path.name}"
    assert trust._ensure_secret_dir(folder)
    path = folder / "asset.tmp"
    original = folder / "original.tmp"
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"asset"))
    guard = cli_main._download_native_frontdoor_asset("fixture", path, retain_guard=True)
    assert isinstance(guard, int)
    try:
        path.rename(original)
        path.write_bytes(b"replacement")
        with ExitStack() as held:
            parent = native_frontdoor._pin_managed_directory(folder, held)
            with pytest.raises(native_download_guard.NativeDownloadCleanupError, match="changed"):
                with native_download_guard.hold_private_asset(path, guard, parent):
                    pass
        assert path.read_bytes() == b"replacement"
    finally:
        native_download_guard.dispose_guarded(original, guard, state="fixture cleanup")
