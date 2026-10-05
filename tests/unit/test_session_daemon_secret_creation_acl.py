"""The Windows secret is NEVER broader than the current user, not even for an instant (round 2).

Before: the temp file was created with the parent's inherited DACL (e.g. ``Everyone:(I)(M)``) and
``icacls`` tightened it afterwards. Tightening a DACL does not revoke handles that were already
opened against the broad one, so a local process could hold a read handle to the file that later
receives the secret. Now the file is created with ``CreateFileW(..., CREATE_NEW, &SECURITY_ATTRIBUTES)``
whose descriptor is ``D:P(A;;FA;;;<current-user-sid>)`` (protected, no inheritance) and share mode 0, so
its DACL is exactly the current user from the first instant. The secret's own parent directory is
DACL-checked (no write/create grant to Everyone/Users/Authenticated Users) and is kept open
(pinned against rename/swap) across creation and publish.

A genuine two-account test is not possible in CI (one logon session). The properties are asserted on
the security descriptor itself, read from the OPEN handle before any byte is written.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_FILE_ALL = 0x1F01FF


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def _grant(path: Path, spec: str) -> None:
    done = subprocess.run(["icacls", str(path), "/grant", spec], capture_output=True, check=False)
    assert done.returncode == 0, done.stderr


# ---- the descriptor at creation (real Windows) ----


@pytest.mark.skipif(sys.platform != "win32", reason="Windows security descriptors")
def test_created_file_dacl_is_exactly_the_current_user_before_any_byte_is_written(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "broad"
    parent.mkdir()
    _grant(parent, "*S-1-1-0:(OI)(CI)(M)")  # the parent would hand Everyone modify by inheritance
    sid = trust._win_current_user_sid()
    assert sid is not None
    target = parent / "probe.tmp"
    handle = trust._win_create_restricted(str(target), sid)
    assert handle is not None
    try:
        assert target.stat().st_size == 0  # nothing has been written yet
        queried = trust._win_dacl_entries(handle)
        assert queried is not None
        owner, entries = queried
        assert owner in trust._our_owners(sid)  # elevated token: default owner is Administrators
        assert entries == [(sid, _FILE_ALL)], (
            entries
        )  # exactly the user: no Everyone, no inheritance
    finally:
        trust._winsec.close_handle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows exclusive create")
def test_creation_is_exclusive_and_cannot_be_opened_while_held(tmp_path: Path) -> None:
    sid = trust._win_current_user_sid()
    assert sid is not None
    target = tmp_path / "x.tmp"
    handle = trust._win_create_restricted(str(target), sid)
    assert handle is not None
    try:
        assert trust._win_create_restricted(str(target), sid) is None  # CREATE_NEW: no clobbering
        with pytest.raises(PermissionError):  # share mode 0: nobody else can open it meanwhile
            open(target, "rb").close()
    finally:
        trust._winsec.close_handle(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows security descriptors")
def test_the_published_secret_dacl_is_user_only_even_under_a_broad_parent(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "broad"
    parent.mkdir()
    _grant(parent, "*S-1-1-0:(OI)(CI)(M)")
    path = parent / "daemon-secret.json"
    trust._write_secret_windows(path, {"secret": "a" * 64})
    handle = trust._winsec.open_no_follow(str(path))
    assert handle is not None
    try:
        queried = trust._win_dacl_entries(handle)
        assert queried is not None
        assert [sid for sid, _m in queried[1]] == [trust._win_current_user_sid()]
    finally:
        trust._winsec.close_handle(handle)
    assert list(parent.glob("*.tmp")) == []


# ---- the parent directory's own DACL (real Windows) ----


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
@pytest.mark.parametrize("sid", ["*S-1-1-0", "*S-1-5-32-545", "*S-1-5-11"])
def test_a_parent_directory_granting_broad_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sid: str
) -> None:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None  # control: works before the grant
    path = secret_dir / "daemon-secret.json"
    assert trust._read_user_secret(path) is not None
    _grant(secret_dir, f"{sid}:(W)")
    assert trust._read_user_secret(path) is None
    assert trust._parent_trusted(secret_dir) is False
    assert trust._load_or_create_user_secret() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_a_parent_directory_with_only_read_grants_is_accepted_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None
    _grant(secret_dir, "*S-1-1-0:(RX)")
    assert trust._parent_trusted(secret_dir) is True
    assert trust._read_user_secret(secret_dir / "daemon-secret.json") is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory handles")
def test_the_parent_directory_is_pinned_during_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    outcome: dict[str, Any] = {}

    def _hook(parent: Path) -> None:
        outcome["called"] = True
        try:
            parent.rename(parent.with_name("swapped"))
            outcome["renamed"] = True
        except OSError:
            outcome["renamed"] = False

    monkeypatch.setattr(trust, "_after_parent_pinned", _hook, raising=False)
    secret = trust._load_or_create_user_secret()
    assert outcome.get("called"), "the pin hook never ran: the test is vacuous"
    assert outcome["renamed"] is False  # the pinned directory cannot be swapped underneath
    assert secret is not None


# ---- fail closed (platform independent) ----


def test_creation_fails_closed_when_the_restricted_file_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: "S-1-5-21-1-2-3-1001")
    monkeypatch.setattr(trust, "_win_create_restricted", lambda _p, _s: None)
    target = tmp_path / "sec" / "daemon-secret.json"
    target.parent.mkdir()
    with pytest.raises(OSError, match="restricted"):
        trust._write_secret_windows(target, {"secret": "a" * 64})
    assert list(target.parent.iterdir()) == []


def test_creation_is_refused_when_the_created_descriptor_is_not_user_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = "S-1-5-21-1-2-3-1001"
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: user)
    monkeypatch.setattr(trust, "_win_create_restricted", lambda _p, _s: object())
    monkeypatch.setattr(trust, "_win_owner_and_dacl", lambda _h: (user, [user, "S-1-1-0"]))
    closed: list[Any] = []
    monkeypatch.setattr(trust._winsec, "close_handle", lambda h: closed.append(h))

    def _no_write(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("a secret byte was written to a file with a broad descriptor")

    monkeypatch.setattr(trust._winsec, "write_all", _no_write)
    target = tmp_path / "sec" / "daemon-secret.json"
    target.parent.mkdir()
    with pytest.raises(OSError, match="descriptor"):
        trust._write_secret_windows(target, {"secret": "a" * 64})
    assert closed, "the handle was leaked"
    assert not target.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="win32 creation branch")
def test_load_or_create_returns_none_and_publishes_nothing_when_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(trust, "_win_create_restricted", lambda _p, _s: None)
    assert trust._load_or_create_user_secret() is None
    assert not trust._daemon_secret_path().exists()
