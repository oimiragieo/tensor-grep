"""Fail-closed trust of the per-user daemon secret (PR #1197 audit round: bypasses 1 and 2).

The daemon is only an optimisation: whenever the secret (or its directory) cannot be PROVEN to be
this user's, the secret is refused and the client takes the cold path. Failing closed is always
acceptable; failing open never is.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_USER = "S-1-5-21-111-222-333-1001"
_OTHER = "S-1-5-21-111-222-333-1999"


@pytest.fixture(autouse=True)
def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def _seams(monkeypatch: pytest.MonkeyPatch, user: Any, queried: Any) -> None:
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: user)
    monkeypatch.setattr(trust, "_win_owner_and_dacl", lambda _h: queried)


# ---- bypass 1: Windows ownership + DACL check on the OPENED handle (logic is OS-independent) ----


def test_windows_foreign_owner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _seams(monkeypatch, _USER, (_OTHER, [_OTHER]))
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False


def test_windows_extra_sid_ace_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _seams(monkeypatch, _USER, (_USER, [_USER, "S-1-1-0"]))  # Everyone
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False


def test_windows_null_dacl_and_unparsed_ace_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _seams(monkeypatch, _USER, (_USER, ["NULL-DACL"]))
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False
    _seams(monkeypatch, _USER, (_USER, ["UNPARSED-ACE-TYPE-5"]))
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False


@pytest.mark.parametrize(
    ("user", "queried"), [(None, (_USER, [_USER])), (_USER, None), (None, None)]
)
def test_windows_api_failure_is_refused(
    monkeypatch: pytest.MonkeyPatch, user: Any, queried: Any
) -> None:
    _seams(monkeypatch, user, queried)
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False
    assert trust._windows_handle_trusted(object(), check_dacl=False) is False


def test_windows_correct_owner_and_acl_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    # POSITIVE CONTROL for the refusals above.
    _seams(monkeypatch, _USER, (_USER, [_USER, "S-1-5-18", "S-1-5-32-544"]))
    assert trust._windows_handle_trusted(object(), check_dacl=True) is True
    # a directory check ignores the DACL contents but still requires the owner
    _seams(monkeypatch, _USER, (_USER, ["S-1-1-0"]))
    assert trust._windows_handle_trusted(object(), check_dacl=False) is True


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows ACL")
def test_real_freshly_created_secret_passes_the_windows_check() -> None:
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    path = trust._daemon_secret_path()
    handle = trust._winsec.open_no_follow(str(path))
    assert handle is not None
    try:
        queried = trust._win_owner_and_dacl(handle)
        assert queried is not None
        owner, granted = queried
        user = trust._win_current_user_sid()
        assert user is not None
        assert owner in trust._our_owners(user)  # elevated token: default owner is Administrators
        assert set(granted) <= {
            user,
            "S-1-5-18",
            "S-1-5-32-544",
        }  # DACL names the user, not the owner
        assert trust._windows_handle_trusted(handle, check_dacl=True) is True
    finally:
        trust._winsec.close_handle(handle)
    assert trust._read_user_secret(path) == secret


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows ACL")
def test_real_secret_with_an_extra_grant_is_refused() -> None:
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    done = subprocess.run(
        ["icacls", str(path), "/grant", "*S-1-1-0:R"], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert trust._read_user_secret(path) is None


# Superseded (round 2): the two icacls-failure tests that used to live here modelled a file created
# with the parent's inherited DACL and tightened afterwards. The secret is now created with a
# user-only descriptor at CreateFileW time (no icacls step); the equivalent fail-closed arms are in
# test_session_daemon_secret_creation_acl.py.


def test_an_untrusted_existing_secret_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = trust._daemon_secret_path()
    path.parent.mkdir(parents=True)
    planted = b'{"secret": "' + b"b" * 64 + b'"}'
    path.write_bytes(planted)
    monkeypatch.setattr(trust, "_read_user_secret", lambda _p: None)  # "cannot be trusted"
    assert trust._load_or_create_user_secret() is None
    assert path.read_bytes() == planted


# ---- bypass 2: single open without following links; same fd for check and read ----


def test_swap_between_check_and_read_never_yields_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = trust._load_or_create_user_secret()
    assert original is not None
    path = trust._daemon_secret_path()
    attacker = b"c" * 64
    staged = path.with_name("staged.json")
    staged.write_text('{"secret": "' + attacker.decode() + '"}', encoding="utf-8")
    if sys.platform != "win32":
        staged.chmod(0o600)
    fired: list[str] = []

    def _swap(*_a: Any) -> None:
        fired.append("swap")
        try:
            os.replace(staged, path)  # a locked/refused swap (Windows) is fine; it just fails
        except OSError:
            fired.append("swap-refused")

    # The new code calls the seam between validate and read; the OLD code read via
    # Path.read_text after a separate lstat -- hook both so the swap lands in either shape.
    monkeypatch.setattr(trust, "_between_validate_and_read", _swap, raising=False)
    real_read_text = Path.read_text

    def _swap_then_read(self: Path, *a: Any, **k: Any) -> str:
        if self == path and not fired:
            _swap()
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _swap_then_read)
    result = trust._read_user_secret(path)
    assert fired, "no swap was attempted: the test is vacuous"
    assert result in (None, original)
    assert result != attacker


def _link_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "win32":
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=False
        )
        if done.returncode == 0:
            return
    pytest.skip("cannot create a directory link here")


def test_a_symlinked_or_junction_parent_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(real))
    assert trust._load_or_create_user_secret() is not None  # positive control: real dir works
    assert trust._read_user_secret(trust._daemon_secret_path()) is not None
    link = tmp_path / "linkdir"
    _link_dir(link, real)
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(link))
    assert trust._read_user_secret(trust._daemon_secret_path()) is None
    assert trust._load_or_create_user_secret() is None


def test_a_symlinked_secret_file_is_refused(tmp_path: Path) -> None:
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    path = trust._daemon_secret_path()
    real = path.with_name("real-secret.json")
    os.replace(path, real)
    try:
        path.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    assert trust._read_user_secret(path) is None
    assert trust._load_or_create_user_secret() is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_group_writable_parent_directory_is_refused() -> None:
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    os.chmod(path.parent, 0o770)
    assert trust._read_user_secret(path) is None
