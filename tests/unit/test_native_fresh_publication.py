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
from tensor_grep.io import windows_publication
from tests.unit.test_native_download_collision import _configure

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows managed publication")
publish_fresh_asset = native_download_guard.publish_fresh_asset
CleanupError = native_download_guard.NativeDownloadCleanupError


@pytest.fixture
def folder(trusted_base_dir, tmp_path):
    folder = trusted_base_dir / f"native-fresh-{tmp_path.name}"
    assert trust._ensure_secret_dir(folder)
    return folder


@pytest.fixture
def verified_download(monkeypatch):
    _configure(monkeypatch, b"verified asset")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: io.BytesIO(b"verified asset"))
    monkeypatch.setattr(cli_main, "_native_tg_version", lambda _path: "1.2.3")


def _install_fresh(destination, held):
    install = cli_main._install_release_native_frontdoor
    flags = {"verify_destination": False, "_managed_source": True, "_fresh_destination": True}
    return install("1.2.3", destination, _managed_held=held, **flags)


def test_fresh_managed_destination_collision_preserves_existing_file(folder, verified_download):
    destination = folder / "tg.exe"
    destination.write_bytes(b"existing object")
    before = destination.stat()
    with ExitStack() as held, pytest.raises(FileExistsError):
        _install_fresh(destination, held)
    assert destination.read_bytes() == b"existing object"
    assert destination.stat().st_ino == before.st_ino


def test_fresh_managed_publication_writes_trusted_file(folder, verified_download):
    destination = folder / "tg.exe"
    with ExitStack() as held:
        _install_fresh(destination, held)
        assert destination.read_bytes() == b"verified asset"
        assert destination.stat().st_nlink == 1
        native_frontdoor._pin_managed_executable(destination, held)


@pytest.mark.parametrize("failure", ["flush", "rename", "after-rename"])
def test_fresh_publication_failure_removes_only_owned_file(folder, monkeypatch, failure):
    destination = folder / "tg.exe"
    sentinel = folder / "unrelated"
    sentinel.write_bytes(b"keep me")
    if failure in {"flush", "rename"}:

        def refuse(*_args, **_kwargs):
            raise OSError(f"injected {failure} failure")

        if failure == "flush":
            monkeypatch.setattr(native_download_guard.os, "fsync", refuse)
        else:
            monkeypatch.setattr(windows_publication, "rename_open", refuse)
    else:
        validate = native_frontdoor._validate_managed_publication_descriptor

        def refuse_after(descriptor, parent, name):
            validate(descriptor, parent, name)
            if name == destination.name:
                raise RuntimeError("injected post-rename failure")

        monkeypatch.setattr(
            native_frontdoor, "_validate_managed_publication_descriptor", refuse_after
        )
    with ExitStack() as held:
        parent = native_frontdoor._pin_managed_directory(folder, held)
        with pytest.raises((OSError, RuntimeError), match="injected"):
            publish_fresh_asset(destination, b"verified asset", parent, held)
    assert sentinel.read_bytes() == b"keep me"
    assert list(folder.iterdir()) == [sentinel]


def test_fresh_publication_reports_cleanup_failure(folder, monkeypatch):
    destination = folder / "tg.exe"
    destination.write_bytes(b"occupant")

    def fail_cleanup(_descriptor):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(windows_publication, "discard_open", fail_cleanup)
    with ExitStack() as held:
        parent = native_frontdoor._pin_managed_directory(folder, held)
        with pytest.raises(CleanupError, match="cleanup failure"):
            publish_fresh_asset(destination, b"verified asset", parent, held)
    assert destination.read_bytes() == b"occupant"
    leftovers = list(folder.glob("tg.exe.*.tmp"))
    assert len(leftovers) == 1 and leftovers[0].read_bytes() == b"verified asset"
    leftovers[0].unlink()


def test_fresh_publication_remains_read_only_until_staging_finishes(folder):
    destination = folder / "tg.exe"
    with ExitStack() as held:
        parent = native_frontdoor._pin_managed_directory(folder, held)
        publish_fresh_asset(destination, b"verified asset", parent, held)
        with pytest.raises(PermissionError):
            destination.write_bytes(b"changed")
        with pytest.raises(PermissionError):
            destination.rename(folder / "renamed")
    assert destination.read_bytes() == b"verified asset"


@pytest.mark.parametrize("attack", ["rewrite", "replace"])
def test_fresh_publication_rejects_handoff_mutation(folder, monkeypatch, attack):
    destination = folder / "tg.exe"
    original = folder / "original"
    hold = native_download_guard.hold_private_asset

    def mutate(path, guard, parent):
        if attack == "replace":
            path.rename(original)
        path.write_bytes(b"attacker bytes")
        return hold(path, guard, parent)

    monkeypatch.setattr(native_download_guard, "hold_private_asset", mutate)
    with ExitStack() as held:
        parent = native_frontdoor._pin_managed_directory(folder, held)
        with pytest.raises(CleanupError):
            publish_fresh_asset(destination, b"verified asset", parent, held)
    if attack == "replace":
        assert destination.read_bytes() == b"attacker bytes"
        assert original.read_bytes() == b"verified asset"
    else:
        assert not destination.exists()


def test_fresh_publication_rejects_live_writable_mapping(folder, monkeypatch):
    destination = folder / "tg.exe"
    mappings = []
    hold = native_download_guard.hold_private_asset

    def map_before_pin(path, guard, parent):
        with path.open("r+b") as source:
            mappings.append(mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_WRITE))
        return hold(path, guard, parent)

    monkeypatch.setattr(native_download_guard, "hold_private_asset", map_before_pin)
    try:
        with ExitStack() as held:
            parent = native_frontdoor._pin_managed_directory(folder, held)
            with pytest.raises(CleanupError, match="pinned"):
                publish_fresh_asset(destination, b"verified asset", parent, held)
    finally:
        for mapping in mappings:
            mapping.close()
    if destination.exists():
        assert destination.read_bytes() == b"verified asset"
        destination.unlink()


def test_fresh_pinned_executable_can_run(folder):
    destination = folder / "where.exe"
    payload = (Path(os.environ["SystemRoot"]) / "System32" / "where.exe").read_bytes()
    with ExitStack() as held:
        parent = native_frontdoor._pin_managed_directory(folder, held)
        publish_fresh_asset(destination, payload, parent, held)
        result = subprocess.run([str(destination), "/?"], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr


def test_fresh_publication_reports_uncertain_guard_close(folder, monkeypatch):
    destination = folder / "tg.exe"
    acquire = native_download_guard.acquire_guard
    close = os.close
    guards = []

    def remember(path, writer):
        guard = acquire(path, writer)
        guards.append(guard)
        return guard

    def fail_guard_close(descriptor):
        if descriptor in guards:
            raise OSError("injected guard close failure")
        close(descriptor)

    monkeypatch.setattr(native_download_guard, "acquire_guard", remember)
    monkeypatch.setattr(native_download_guard.os, "close", fail_guard_close)
    try:
        with ExitStack() as held:
            parent = native_frontdoor._pin_managed_directory(folder, held)
            with pytest.raises(CleanupError, match=r"guard.*may remain"):
                publish_fresh_asset(destination, b"verified asset", parent, held)
        assert destination.read_bytes() == b"verified asset"
    finally:
        for guard in guards:
            close(guard)
