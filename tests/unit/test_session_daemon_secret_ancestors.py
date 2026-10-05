"""Every ANCESTOR of the secret's directory must be trustworthy, not just its parent (PR #1197).

StrictModes-style: whoever can rename, replace or write into an ancestor can swap the whole subtree
beneath it, so the secret directory is only as safe as its weakest ancestor. POSIX: each ancestor
is a real directory owned by the user or root, and if group/world-writable it must be sticky (like
/tmp). Windows: no ancestor is a reparse point (the drive root is exempt) and none grants write or
modify access to Everyone / Users / Authenticated Users (the drive root is exempt). Bounded walk;
any API error fails closed.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_USER = "S-1-5-21-111-222-333-1001"
_EVERYONE = "S-1-1-0"
_USERS = "S-1-5-32-545"
_AUTH_USERS = "S-1-5-11"

# FILE_WRITE_DATA / FILE_APPEND_DATA / FILE_DELETE_CHILD / DELETE / WRITE_DAC / GENERIC_WRITE
_WRITE, _APPEND, _DELETE_CHILD, _DELETE, _WRITE_DAC, _GENERIC_WRITE = (
    0x2,
    0x4,
    0x40,
    0x10000,
    0x40000,
    0x40000000,
)
_READ_EXEC = 0x1200A9


def _secret_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *parts: str) -> Path:
    d = tmp_path.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(d))
    return d


# ---- Windows DACL logic (OS-independent over the entries seam) ----


@pytest.mark.parametrize("sid", [_EVERYONE, _USERS, _AUTH_USERS])
@pytest.mark.parametrize(
    "mask", [_WRITE, _APPEND, _DELETE_CHILD, _DELETE, _WRITE_DAC, _GENERIC_WRITE, 0x1301BF]
)
def test_windows_write_class_grant_to_a_broad_group_is_refused(sid: str, mask: int) -> None:
    entries = [(_USER, 0x1F01FF), (sid, mask)]
    assert trust._windows_ancestor_dacl_ok(entries, _USER) is False


def test_windows_read_only_grants_and_unrelated_sids_are_accepted() -> None:
    # POSITIVE CONTROL (shape of C:\Users: Users/Everyone read+execute only).
    entries = [
        (_USERS, _READ_EXEC),
        (_EVERYONE, _READ_EXEC),
        ("S-1-5-21-1-2-3-4000", 0x1200A9),  # another account may READ (full control is refused:
        # see test_session_daemon_dacl_foreign_principals.py)
        ("S-1-5-18", 0x1F01FF),
        (_USER, 0x1F01FF),
    ]
    assert trust._windows_ancestor_dacl_ok(entries, _USER) is True


def test_windows_null_dacl_and_unparsed_ace_sentinels_are_refused() -> None:
    assert trust._windows_ancestor_dacl_ok([("NULL-DACL", 0xFFFFFFFF)], _USER) is False
    assert trust._windows_ancestor_dacl_ok([("UNPARSED-ACE-TYPE-5", 0xFFFFFFFF)], _USER) is False


# ---- Windows, real filesystem ----


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reparse points / ACLs")
def test_real_local_app_data_path_is_accepted() -> None:
    # POSITIVE CONTROL on this box: the real per-user state path must pass.
    base = Path(os.environ["LOCALAPPDATA"])
    assert trust._ancestors_trusted(base / "tensor-grep") is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows reparse points")
def test_a_junction_ancestor_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real"
    (real / "secret").mkdir(parents=True)
    junction = tmp_path / "jn"
    done = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real)], capture_output=True, check=False
    )
    if done.returncode != 0:
        pytest.skip("cannot create a junction here")
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(real / "secret"))
    assert trust._load_or_create_user_secret() is not None  # control: reachable without the link
    secret_dir = junction / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._read_user_secret(secret_dir / "daemon-secret.json") is None
    assert trust._ancestors_trusted(secret_dir) is False
    assert trust._load_or_create_user_secret() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")
def test_an_ancestor_granting_everyone_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = _secret_dir(tmp_path, monkeypatch, "a", "b", "secret")
    # control first: the same layout WITHOUT the grant works
    assert trust._load_or_create_user_secret() is not None
    path = secret_dir / "daemon-secret.json"
    assert trust._read_user_secret(path) is not None
    done = subprocess.run(
        ["icacls", str(tmp_path / "a"), "/grant", "*S-1-1-0:(W)"], capture_output=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert trust._read_user_secret(path) is None
    assert trust._ancestors_trusted(secret_dir) is False
    assert trust._load_or_create_user_secret() is None  # present but untrusted: never reused


# ---- POSIX ----


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757])
def test_a_writable_non_sticky_ancestor_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    secret_dir = _secret_dir(tmp_path, monkeypatch, "a", "b", "secret")
    os.chmod(secret_dir, 0o700)
    assert trust._load_or_create_user_secret() is not None  # control: untouched layout works
    os.chmod(tmp_path / "a", mode)
    try:
        assert trust._read_user_secret(secret_dir / "daemon-secret.json") is None
        assert trust._ancestors_trusted(secret_dir) is False
    finally:
        os.chmod(tmp_path / "a", 0o700)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits")
def test_a_sticky_world_writable_ancestor_like_tmp_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = _secret_dir(tmp_path, monkeypatch, "a", "b", "secret")
    os.chmod(tmp_path / "a", 0o1777)
    try:
        assert stat.S_ISVTX & (tmp_path / "a").stat().st_mode
        assert trust._ancestors_trusted(secret_dir) is True
    finally:
        os.chmod(tmp_path / "a", 0o700)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX ownership")
def test_a_user_owned_symlink_ancestor_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real"
    (real / "secret").mkdir(parents=True)
    link = tmp_path / "ln"
    link.symlink_to(real, target_is_directory=True)
    secret_dir = link / "secret"
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    assert trust._ancestors_trusted(secret_dir) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX home path")
def test_real_home_existing_ancestors_of_the_default_location_are_accepted() -> None:
    # POSITIVE CONTROL on the real home layout (a stock runner's /home/runner is 0750 owned by the user,
    # /home is root 0755). The default location's own directories (~/.local/state/tensor-grep) usually do
    # NOT exist yet -- production creates them (0700) before it checks -- so check the deepest EXISTING
    # directory along the default path, with no filesystem side effect.
    deepest = Path.home() / ".local" / "state" / "tensor-grep"
    while not deepest.exists() and deepest != deepest.parent:
        deepest = deepest.parent
    # CONTRACT: ``_ancestors_refusal(p)`` vets ``p`` ITSELF (via its resolved chain) and every ancestor
    # of ``p``, so ``p`` must exist -- passing a not-yet-created child is (correctly) refused.
    assert trust._ancestors_refusal(deepest) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX layout")
def test_a_stock_runner_layout_with_no_local_state_is_accepted_up_to_the_deepest_existing_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Controlled copy of the measured GitHub ubuntu layout: home 0750 (user), ~/.local 0755 (user),
    # NO ~/.local/state. The deepest existing directory is ~/.local.
    home = tmp_path / "home" / "runner"
    (home / ".local").mkdir(parents=True)
    home.chmod(0o750)
    (home / ".local").chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    deepest = Path.home() / ".local" / "state" / "tensor-grep"
    while not deepest.exists() and deepest != deepest.parent:
        deepest = deepest.parent
    assert deepest == home / ".local"
    assert trust._ancestors_refusal(deepest) is None
    # CONTROL: the not-yet-created child itself IS refused (and the reason says why), so a None above
    # cannot be an always-pass.
    reason = trust._ancestors_refusal(deepest / "state")
    assert reason is not None and "FileNotFoundError" in reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX layout")
def test_a_missing_ancestor_is_refused_and_the_reason_names_it(tmp_path: Path) -> None:
    # The refusal that the first CI run hit on a real runner: ~/.local/state did not exist.
    reason = trust._ancestors_refusal(tmp_path / "missing" / "state" / "tensor-grep")
    assert reason is not None
    assert "FileNotFoundError" in reason
    assert "missing" in reason


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX home path")
def test_the_production_default_location_is_created_then_accepted_under_a_stock_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The standard layout on a stock Linux runner: a user-owned home with NO ~/.local at all. The daemon
    # must still get its secret (the directories are created 0700 by us, THEN vetted).
    home = tmp_path / "home" / "runner"
    home.mkdir(parents=True)
    home.chmod(0o750)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TG_DAEMON_SECRET_DIR", raising=False)
    assert not (home / ".local").exists()
    assert trust._load_or_create_user_secret() is not None
    state = home / ".local" / "state" / "tensor-grep"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert trust._ancestors_trusted(state) is True


# ---- fail closed ----


def test_the_walk_is_bounded_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deep = tmp_path.joinpath(*["d"] * 200)
    assert trust._ancestors_trusted(deep) is False  # deeper than the bound: refuse, never "assume"


def test_an_api_error_while_walking_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = _secret_dir(tmp_path, monkeypatch, "a", "secret")
    assert trust._ancestors_trusted(secret_dir) is True  # control
    real_lstat = os.lstat
    calls: list[Any] = []

    def _boom(p: Any, *a: Any, **k: Any) -> Any:
        if Path(p) == tmp_path / "a":
            calls.append(p)
            raise PermissionError("denied")
        return real_lstat(p, *a, **k)

    monkeypatch.setattr(trust, "_lstat", _boom)  # private seam, never the global os.lstat
    monkeypatch.setattr(trust, "_win_dacl_entries", lambda _h: None)
    if sys.platform == "win32":
        monkeypatch.setattr(trust._winsec, "open_no_follow", lambda *a, **k: None)
    assert trust._ancestors_trusted(secret_dir) is False
