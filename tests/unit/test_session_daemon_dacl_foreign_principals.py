"""Windows directory trust must reject OTHER accounts' dangerous rights (PR #1197 round 6).

``_windows_ancestor_dacl_ok([(foreign_sid, 0x1F01FF)])`` used to return True: a principal other than the
current user / SYSTEM / Administrators holding FILE_DELETE_CHILD (full control) on the secret's directory
could delete the secret, and an earlier test pinned that bypass. Now:

* PARENT (the secret directory): any grant to a foreign principal that includes delete-child, delete,
  write-DAC, write-owner or create/write rights is refused.
* ANCESTORS: any grant to a foreign principal that includes delete-child, write-DAC or write-owner (the
  rights that let someone replace or rename a path component) is refused; the earlier rule about broad
  groups (Everyone / Users / Authenticated Users) holding write rights still applies.

MEASURED ACLs on this box (``icacls``), which the mask had to accept for the DEFAULT location:

  C:\\                          drive root (exempt): Authenticated Users:(AD) and (OI)(CI)(IO)(M), Users RX
  C:\\Users                    SYSTEM F, Administrators F, Users RX, Everyone RX, AppContainer RX
  C:\\Users\\<me>              SYSTEM F, Administrators F, <me> F, one other local account RX, AppContainer RX
  ...\\AppData, ...\\Local     SYSTEM F, Administrators F, <me> F, CodexSandboxUsers RX, and FOREIGN local
                               accounts with Modify (M = 0x1301BF: DELETE but NOT delete-child)
  ...\\Local\\Temp             as above plus a foreign account with (M,DC): DELETE_CHILD  -> REFUSED

Modify without delete-child is accepted on ancestors (the default location passes on this box); the Temp
directory, which carries a foreign delete-child grant, is refused.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_ME = "S-1-5-21-47336615-1281375635-3487610964-1001"
_FOREIGN = "S-1-5-21-47336615-1281375635-3487610964-1009"
_SYSTEM, _ADMINS = "S-1-5-18", "S-1-5-32-544"
_USERS, _EVERYONE, _APPCONTAINER = "S-1-5-32-545", "S-1-1-0", "S-1-15-3-65536-1-2-3"

_F, _M, _RX = 0x1F01FF, 0x1301BF, 0x1200A9
_DELETE_CHILD, _WRITE_DAC, _WRITE_OWNER, _DELETE = 0x40, 0x40000, 0x80000, 0x10000
_CREATE_FILE, _CREATE_DIR = 0x2, 0x4


# ---- the pinned bypass is gone ----


def test_a_foreign_sid_with_full_control_is_rejected_on_an_ancestor() -> None:
    assert trust._windows_ancestor_dacl_ok([(_ME, _F), (_FOREIGN, _F)], _ME) is False


@pytest.mark.parametrize("mask", [_DELETE_CHILD, _WRITE_DAC, _WRITE_OWNER, 0x10000000])
def test_a_foreign_sid_holding_a_replace_or_rename_right_is_rejected_on_an_ancestor(
    mask: int,
) -> None:
    assert trust._windows_ancestor_dacl_ok([(_ME, _F), (_FOREIGN, mask)], _ME) is False


@pytest.mark.parametrize(
    "mask",
    [_DELETE_CHILD, _DELETE, _WRITE_DAC, _WRITE_OWNER, _CREATE_FILE, _CREATE_DIR, 0x100, 0x10],
)
def test_a_foreign_sid_holding_delete_or_write_rights_is_rejected_on_the_parent(mask: int) -> None:
    assert trust._windows_parent_dacl_ok([(_ME, _F), (_FOREIGN, mask)], _ME) is False


def test_the_users_own_and_system_and_administrators_grants_are_fine_on_the_parent() -> None:
    entries = [(_ME, _F), (_SYSTEM, _F), (_ADMINS, _F)]
    assert trust._windows_parent_dacl_ok(entries, _ME) is True
    assert trust._windows_ancestor_dacl_ok(entries, _ME) is True


def test_read_only_grants_to_anyone_are_fine_on_both() -> None:
    entries = [(_ME, _F), (_FOREIGN, _RX), (_USERS, _RX), (_EVERYONE, _RX), (_APPCONTAINER, _RX)]
    assert trust._windows_parent_dacl_ok(entries, _ME) is True
    assert trust._windows_ancestor_dacl_ok(entries, _ME) is True


def test_sentinels_and_an_unknown_current_user_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert trust._windows_parent_dacl_ok([("NULL-DACL", 0xFFFFFFFF)], _ME) is False
    assert trust._windows_ancestor_dacl_ok([("UNPARSED-ACE-TYPE-5", 0xFFFFFFFF)], _ME) is False
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: None)
    assert trust._windows_ancestor_dacl_ok([(_ME, _F)]) is False
    assert trust._windows_parent_dacl_ok([(_ME, _F)]) is False


# ---- the measured default ancestors are accepted ----

_MEASURED_DEFAULT_ANCESTORS = {
    "C:\\Users": [
        (_SYSTEM, _F),
        (_ADMINS, _F),
        (_USERS, _RX),
        (_EVERYONE, _RX),
        (_APPCONTAINER, _RX),
    ],
    "C:\\Users\\<me>": [
        (_SYSTEM, _F),
        (_ADMINS, _F),
        (_ME, _F),
        (_FOREIGN, _RX),
        (_APPCONTAINER, _RX),
    ],
    "AppData / Local (foreign accounts with Modify, no delete-child)": [
        (_SYSTEM, _F),
        (_ADMINS, _F),
        (_ME, _F),
        (_FOREIGN, _M),
        ("S-1-5-21-3640877200-779319315-1397809029-4222329326", _M),
        ("S-1-5-21-4145746082-2770681444-4182672818-1665397812", _M),
        ("S-1-5-21-1111111111-1-1-1001", _RX),  # CodexSandboxUsers-style read group
        (_APPCONTAINER, _RX),
    ],
}


@pytest.mark.parametrize("where", list(_MEASURED_DEFAULT_ANCESTORS), ids=lambda w: w[:14])
def test_the_measured_default_ancestors_are_accepted(where: str) -> None:
    assert trust._windows_ancestor_dacl_ok(_MEASURED_DEFAULT_ANCESTORS[where], _ME) is True


def test_the_measured_temp_directory_with_a_foreign_delete_child_grant_is_refused() -> None:
    temp = [
        (_SYSTEM, _F),
        (_ADMINS, _F),
        (_ME, _F),
        (_FOREIGN, _M),
        ("S-1-5-21-251652277-3133185733-1136664309-227498030", _M | _DELETE_CHILD),  # (M,DC)
    ]
    assert trust._windows_ancestor_dacl_ok(temp, _ME) is False


@pytest.mark.skipif(sys.platform != "win32", reason="real %LOCALAPPDATA%")
def test_the_real_default_secret_location_ancestors_are_accepted_on_this_machine() -> None:
    base = Path(os.environ["LOCALAPPDATA"])
    assert trust._ancestors_trusted(base / "tensor-grep") is True


# ---- real Windows filesystem ----


def _grant(path: Path, spec: str) -> None:
    done = subprocess.run(["icacls", str(path), "/grant", spec], capture_output=True, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_a_foreign_full_control_grant_on_the_real_secret_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None  # control: works before the grant
    path = secret_dir / "daemon-secret.json"
    assert trust._read_user_secret(path) is not None
    _grant(secret_dir, "*S-1-5-4:(F)")  # INTERACTIVE: a principal that is not us / SYSTEM / Admins
    assert trust._read_user_secret(path) is None
    assert trust._parent_trusted(secret_dir) is False
    assert trust._load_or_create_user_secret() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_a_new_secret_directory_is_created_user_only_even_under_a_foreign_modify_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grand = tmp_path / "grand"
    grand.mkdir()
    _grant(grand, "*S-1-5-4:(OI)(CI)(M)")  # would be INHERITED by a plain mkdir
    secret_dir = grand / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._load_or_create_user_secret() is not None
    handle = trust._winsec.open_no_follow(str(secret_dir), directory=True)
    assert handle is not None
    try:
        queried = trust._win_dacl_entries(handle)
        assert queried is not None
        owner, entries = queried
        assert [sid for sid, _mask in entries] == [owner]  # user-only: nothing inherited
    finally:
        trust._winsec.close_handle(handle)
    assert trust._parent_trusted(secret_dir) is True
