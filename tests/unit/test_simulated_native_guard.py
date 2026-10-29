import os
from types import SimpleNamespace

import pytest

from tests.unit.test_cli_modes_shared import _install_simulated_native_guard


def test_simulation_is_posix_only_and_uses_live_descriptor(tmp_path, monkeypatch):
    def original(*_args):
        return None

    guard = SimpleNamespace(acquire_guard=original, discard_guarded=original)
    _install_simulated_native_guard(monkeypatch, tmp_path, guard)
    if os.name == "nt":
        assert guard.acquire_guard is original and guard.discard_guarded is original
        return
    path = tmp_path / "asset"
    writer = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with pytest.raises(AssertionError):
            guard.acquire_guard(tmp_path / ".." / "outside", writer)
        pinned = guard.acquire_guard(path, writer)
    finally:
        os.close(writer)
    try:
        assert os.fstat(pinned).st_ino == os.stat(path).st_ino
        guard.discard_guarded(path, pinned)
        assert not path.exists()
    finally:
        os.close(pinned)


@pytest.mark.skipif(os.name == "nt", reason="POSIX simulation only")
@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_simulation_refuses_replaced_fixture(tmp_path, monkeypatch, replacement):
    guard = SimpleNamespace(acquire_guard=None, discard_guarded=None)
    _install_simulated_native_guard(monkeypatch, tmp_path, guard)
    path, saved = tmp_path / "asset", tmp_path / "saved"
    writer = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        pinned = guard.acquire_guard(path, writer)
    finally:
        os.close(writer)
    try:
        path.rename(saved)
        if replacement == "file":
            path.write_bytes(b"replacement")
        else:
            path.symlink_to(saved)
        with pytest.raises(OSError, match="fixture identity changed"):
            guard.discard_guarded(path, pinned)
        assert (
            path.is_symlink() if replacement == "symlink" else path.read_bytes() == b"replacement"
        )
        assert saved.is_file()
    finally:
        os.close(pinned)


@pytest.mark.skipif(os.name == "nt", reason="POSIX simulation only")
def test_simulated_private_creation_is_scoped_and_exclusive(tmp_path, monkeypatch):
    from tensor_grep.cli import session_daemon_winsec as winsec
    from tensor_grep.io import windows_publication

    guard = SimpleNamespace(acquire_guard=None, discard_guarded=None)
    _install_simulated_native_guard(monkeypatch, tmp_path, guard)
    path = tmp_path / "asset"
    sid = winsec.current_user_sid()
    fd = windows_publication.open_temporary(path, restrict_to_sid=sid)
    os.close(fd)
    with pytest.raises(FileExistsError):
        windows_publication.open_temporary(path, restrict_to_sid=sid)
    with pytest.raises(AssertionError):
        windows_publication.open_temporary(tmp_path / ".." / "outside", restrict_to_sid=sid)
    assert path.is_file()
