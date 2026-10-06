"""The secret is verified THROUGH the handle that reads it (PR #1197 round 7).

Checking the ancestors, closing their handles and then opening the secret BY PATHNAME is not a race-free
chain, and the ancestor owner was ignored (an owner implicitly holds WRITE_DAC and can grant itself any
right). Now:

* Windows: the secret's directory is opened and PINNED first (no FILE_SHARE_DELETE, so it cannot be
  renamed away), the secret FILE is opened once without following links, and THAT handle is validated:
  owner is the current user, DACL limited to the user / SYSTEM / Administrators, ``nlink == 1``, and its
  final path's directory equals the pinned directory's final path. The bytes are read through that handle.
* POSIX: ``O_NOFOLLOW`` open of the directory, ``fstat`` owner/mode, then the file is opened RELATIVE to
  that dirfd (``dir_fd=``) with ``O_NOFOLLOW``, ``fstat`` owner/mode/``nlink == 1``, and read through the fd.
* Ancestors stay as defence in depth and now also require a trusted OWNER: the current user, SYSTEM,
  Administrators or TrustedInstaller. A swapped / renamed ancestor is therefore DoS at worst, never a
  trusted foreign secret.
* Creation: POSIX creates the temp file, links it and unlinks it relative to the verified dirfd where
  ``os.link``/``os.open`` support ``dir_fd``; Windows has no ``openat`` in Win32, so the directory is pinned
  by an open handle and the file is created (``CreateFileW``) by path under that pin.

MEASURED ancestor owners on this box: C:\\Users = SYSTEM, C:\\Users\\<me> = SYSTEM, AppData / Local / Temp =
the user, C:\\Windows and C:\\Program Files = TrustedInstaller, C:\\ProgramData = SYSTEM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_ME = "S-1-5-21-47336615-1281375635-3487610964-1001"
_FOREIGN = "S-1-5-21-47336615-1281375635-3487610964-1009"
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"


@pytest.fixture(autouse=True)
def _secret_dir(trusted_base_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(trusted_base_dir / "secret"))


# ---- ancestor OWNER policy ----


@pytest.mark.parametrize("owner", [_ME, "S-1-5-18", "S-1-5-32-544", _TRUSTED_INSTALLER])
def test_trusted_ancestor_owners_are_accepted(owner: str) -> None:
    assert trust._windows_owner_ok(owner, _ME) is True


@pytest.mark.parametrize("owner", [_FOREIGN, "S-1-1-0", "S-1-5-32-545", "S-1-5-11", "", None])
def test_any_other_ancestor_owner_is_refused(owner: Any) -> None:
    assert trust._windows_owner_ok(owner, _ME) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ancestor handles")
def test_an_ancestor_with_a_foreign_owner_is_refused_and_the_control_is_accepted(
    trusted_base_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = trusted_base_dir / "secret"
    assert trust._load_or_create_user_secret() is not None
    me = trust._win_current_user_sid()
    real = trust._win_dacl_entries

    def _with_owner(owner: str) -> Any:
        def _query(handle: Any) -> Any:
            queried = real(handle)
            return None if queried is None else (owner, queried[1])

        return _query

    monkeypatch.setattr(trust, "_win_dacl_entries", _with_owner(me))
    assert trust._ancestors_trusted(secret_dir) is True  # control: nothing else changed
    monkeypatch.setattr(trust, "_win_dacl_entries", _with_owner(_FOREIGN))
    assert trust._ancestors_trusted(secret_dir) is False
    assert trust._read_user_secret(secret_dir / "daemon-secret.json") is None


# ---- link count, binding, swaps ----


def test_a_hard_linked_secret_is_not_trusted(trusted_base_dir: Path) -> None:
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    path = trust._daemon_secret_path()
    assert trust._read_user_secret(path) == secret
    os.link(path, trusted_base_dir / "elsewhere-link")
    assert path.stat().st_nlink == 2
    assert (
        trust._read_user_secret(path) is None
    )  # nlink != 1: some other name can reach the content


def test_a_crash_leftover_link_is_recovered_not_refused_forever(trusted_base_dir: Path) -> None:
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    path = trust._daemon_secret_path()
    leftover = path.with_name(f".{path.name}.{'a' * 32}.tmp")
    os.link(path, leftover)  # exactly what a writer killed between link and unlink leaves
    assert trust._read_user_secret(path) is None
    assert trust._load_or_create_user_secret() == secret  # recovery under the lock, then re-read
    assert not leftover.exists()
    assert path.stat().st_nlink == 1


def test_swapping_the_parent_after_the_ancestor_check_never_yields_the_swapped_in_file(
    trusted_base_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = trusted_base_dir / "secret"
    original = trust._load_or_create_user_secret()
    assert original is not None
    path = trust._daemon_secret_path()
    # an attacker-chosen directory holding a file with a VALID owner and ACL (made by this same user)
    attacker_dir = trusted_base_dir / "attacker"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(attacker_dir))
    attacker = trust._load_or_create_user_secret()
    assert attacker is not None and attacker != original
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))

    real = trust._ancestors_trusted
    fired: list[str] = []

    def _swap_after_the_check(parent: Path) -> bool:
        ok = real(parent)
        fired.append("checked")
        try:
            os.rename(parent, parent.with_name("swapped-away"))
            os.rename(attacker_dir, parent)
            fired.append("swapped")
        except OSError:
            fired.append("refused")  # Windows: the pinned directory cannot be renamed
        return ok

    monkeypatch.setattr(trust, "_ancestors_trusted", _swap_after_the_check)
    result = trust._read_user_secret(path)
    assert "checked" in fired, "the ancestor check never ran: the test is vacuous"
    assert result != attacker, "the swapped-in file's bytes were trusted"
    assert result in (None, original)


def test_final_path_binding_compares_the_files_directory_with_the_pinned_directory() -> None:
    parent = "\\\\?\\C:\\Users\\me\\AppData\\Local\\tensor-grep"
    assert trust._file_bound_to_parent(parent + "\\daemon-secret.json", parent) is True
    assert trust._file_bound_to_parent(parent.lower() + "\\daemon-secret.json", parent) is True
    assert trust._file_bound_to_parent("C:\\Users\\me\\x\\daemon-secret.json", parent) is False
    assert trust._file_bound_to_parent(parent + "\\sub\\daemon-secret.json", parent) is False
    assert trust._file_bound_to_parent(None, parent) is False
    assert trust._file_bound_to_parent(parent + "\\f", None) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file / directory handles")
def test_the_real_secret_handle_reports_one_link_and_the_pinned_directory(
    trusted_base_dir: Path,
) -> None:
    assert trust._load_or_create_user_secret() is not None
    path = trust._daemon_secret_path()
    parent_handle = trust._winsec.open_no_follow(str(path.parent), directory=True, share=0x3)
    file_handle = trust._winsec.open_no_follow(str(path))
    assert parent_handle is not None and file_handle is not None
    try:
        assert trust._winsec.link_count(file_handle) == 1
        assert trust._file_bound_to_parent(
            trust._winsec.final_path(file_handle), trust._winsec.final_path(parent_handle)
        )
        with pytest.raises(OSError):  # the pinned directory cannot be renamed away
            os.rename(path.parent, path.parent.with_name("moved"))
    finally:
        trust._winsec.close_handle(file_handle)
        trust._winsec.close_handle(parent_handle)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX dirfd / mode bits")
def test_posix_creation_and_read_use_the_verified_directory_fd(trusted_base_dir: Path) -> None:
    secret = trust._load_or_create_user_secret()
    assert secret is not None
    path = trust._daemon_secret_path()
    st = path.stat()
    assert st.st_nlink == 1 and (st.st_mode & 0o777) == 0o600
    # H.5: `index_lock` leaves its never-deleted, empty, 0600 sidecar handle (`.lock.os`) beside the
    # secret (see test_secret_dir_lock_sidecar.py for its permissions).
    assert sorted(
        p.name for p in path.parent.iterdir() if not p.name.endswith((".lock", ".lock.os"))
    ) == [path.name]
