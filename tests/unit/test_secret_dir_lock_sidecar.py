"""The OS sidecar lock handle left in the daemon SECRET dir is as private as the secret itself.

`_load_or_create_user_secret` serialises creation with `index_lock(<secret path>)`, so the
never-deleted handle `.daemon-secret.json.lock.os` lands in the owner-only secret directory. It is
empty (no secret material) and must not widen access to that directory: 0600 on POSIX, and a DACL
the same trust check the secret and its directory pass on Windows.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import session_daemon_trust as trust


def _create_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None
    return secret_dir


def test_the_creation_lock_sidecar_exists_and_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = _create_secret(tmp_path, monkeypatch)
    sidecars = sorted(p for p in secret_dir.iterdir() if p.name.endswith(".lock.os"))
    assert [p.name for p in sidecars] == [".daemon-secret.json.lock.os"]
    assert sidecars[0].stat().st_size == 0, "the sidecar must carry no secret material"
    secret = secret_dir / "daemon-secret.json"
    assert secret.exists() and secret.read_bytes() not in (b"",)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_posix_sidecar_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_dir = _create_secret(tmp_path, monkeypatch)
    for entry in secret_dir.iterdir():
        mode = stat.S_IMODE(entry.stat().st_mode)
        assert mode & 0o077 == 0, f"{entry.name} is accessible beyond the owner: {oct(mode)}"
    assert stat.S_IMODE((secret_dir / ".daemon-secret.json.lock.os").stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACL")
def test_windows_sidecar_passes_the_same_dacl_trust_check_as_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = _create_secret(tmp_path, monkeypatch)
    for name in (".daemon-secret.json.lock.os", "daemon-secret.json"):
        handle = trust._winsec.open_no_follow(str(secret_dir / name))
        assert handle is not None
        try:
            assert trust._windows_handle_trusted(handle, check_dacl=True), name
        finally:
            trust._winsec.close_handle(handle)


def test_reading_the_secret_ignores_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every reader of the directory keys on the exact secret file name, so the sidecar cannot be
    # mistaken for (or substituted as) the secret.
    secret_dir = _create_secret(tmp_path, monkeypatch)
    first = trust._read_user_secret(secret_dir / "daemon-secret.json")
    assert first is not None
    assert trust._load_or_create_user_secret() == first  # a second load reuses it
    assert trust._read_user_secret(secret_dir / ".daemon-secret.json.lock.os") is None
