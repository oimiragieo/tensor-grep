"""Two CI-runner realities the first CI run exposed (PR #1197).

1. ``uv``-installed CPythons (python-build-standalone) omit ``os.pidfd_open`` / ``signal.pidfd_send_signal``
   even on kernels that support pidfd, so the bound-handle kill path silently became unavailable on Linux
   runners (escalation reported ``no_bound_process_handle``). The raw ``syscall(2)`` fallbacks
   (``pidfd_open`` = 434, ``pidfd_send_signal`` = 424, identical on every Linux architecture) restore it;
   a kernel without the syscall fails with ENOSYS and the guard is simply unbound (nothing is signalled).
2. An ELEVATED Windows token (``runneradmin``) creates objects owned by the token's default owner,
   ``BUILTIN\\Administrators``, not by the user SID. The owner checks accepted only the user SID, so the
   secret could never be created or read on an elevated runner. The token's default owner is accepted too
   (a non-elevated token's default owner IS the user, so nothing widens there).
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon_trust as trust

_USER = "S-1-5-21-1-2-3-1001"
_ADMINS = "S-1-5-32-544"
_FOREIGN = "S-1-5-21-1-2-3-1009"


class _FakeLibc:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []

    def syscall(self, *args: Any) -> int:
        # full-width ctypes values (c_long / c_void_p): compare their plain values
        self.calls.append(tuple(getattr(a, "value", a) for a in args))
        return self.result


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trust, "_IS_LINUX", True)


# ---- (1) pidfd through syscall(2) ----


def test_pidfd_open_falls_back_to_the_raw_syscall(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    libc = _FakeLibc(7)
    monkeypatch.setattr(trust, "_load_libc", lambda: libc)
    assert trust._syscall_pidfd_open(1234) == 7
    assert libc.calls == [(434, 1234, 0)]


def test_pidfd_send_signal_falls_back_to_the_raw_syscall(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    libc = _FakeLibc(0)
    monkeypatch.setattr(trust, "_load_libc", lambda: libc)
    trust._syscall_pidfd_send_signal(7, 15)
    assert libc.calls == [(424, 7, 15, None, 0)]


def test_a_missing_process_maps_to_processlookuperror_and_enosys_to_oserror(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_load_libc", lambda: _FakeLibc(-1))
    monkeypatch.setattr(trust, "_libc_errno", lambda: errno.ESRCH)
    with pytest.raises(ProcessLookupError):
        trust._syscall_pidfd_open(1234)
    monkeypatch.setattr(trust, "_libc_errno", lambda: errno.ENOSYS)
    with pytest.raises(OSError) as info:
        trust._syscall_pidfd_open(1234)
    assert info.value.errno == errno.ENOSYS
    with pytest.raises(OSError):
        trust._syscall_pidfd_send_signal(7, 15)


def test_the_syscall_wrappers_refuse_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trust, "_IS_LINUX", False)
    with pytest.raises(OSError):
        trust._syscall_pidfd_open(1)
    with pytest.raises(OSError):
        trust._syscall_pidfd_send_signal(1, 15)


def test_an_enosys_kernel_gives_an_unbound_guard_never_a_signal(
    linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_load_libc", lambda: _FakeLibc(-1))
    monkeypatch.setattr(trust, "_libc_errno", lambda: errno.ENOSYS)
    monkeypatch.setattr(trust, "_pidfd_open", trust._syscall_pidfd_open)
    monkeypatch.setattr(trust, "_pidfd_send_signal", trust._syscall_pidfd_send_signal)
    monkeypatch.setattr(trust, "_pid_guard_platform_is_windows", lambda: False, raising=False)
    if sys.platform == "win32":
        pytest.skip("the Windows guard path does not consult pidfd")
    guard = trust._open_pid_guard(os.getpid())
    assert guard is not None and guard.bound is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux pidfd seams")
def test_on_linux_the_pidfd_seams_exist_even_without_the_stdlib_wrappers() -> None:
    assert callable(trust._pidfd_open)
    assert callable(trust._pidfd_send_signal)


@pytest.mark.parametrize("machine", ["mips", "mips64", "ppc64le", "riscv64", "s390x", ""])
def test_an_unverified_architecture_never_attempts_the_raw_syscall_and_is_unbound(
    machine: str, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    libc = _FakeLibc(7)
    monkeypatch.setattr(trust, "_load_libc", lambda: libc)
    monkeypatch.setattr(trust.platform, "machine", lambda: machine)
    for call in (
        lambda: trust._syscall_pidfd_open(1234),
        lambda: trust._syscall_pidfd_send_signal(7, 15),
    ):
        with pytest.raises(OSError) as info:
            call()
        assert info.value.errno == errno.ENOSYS
    assert libc.calls == []  # CONTROL below proves the same fake IS called on a verified machine
    if sys.platform != "win32":
        monkeypatch.setattr(trust, "_pidfd_open", trust._syscall_pidfd_open)
        monkeypatch.setattr(trust, "_pidfd_send_signal", trust._syscall_pidfd_send_signal)
        guard = trust._open_pid_guard(os.getpid())
        assert guard is not None and guard.bound is False


@pytest.mark.parametrize("machine", ["x86_64", "AMD64", "aarch64", "arm64"])
def test_a_verified_architecture_uses_the_raw_syscall(
    machine: str, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    libc = _FakeLibc(7)
    monkeypatch.setattr(trust, "_load_libc", lambda: libc)
    monkeypatch.setattr(trust.platform, "machine", lambda: machine)
    assert trust._syscall_pidfd_open(1234) == 7
    assert libc.calls == [(434, 1234, 0)]


# ---- (2) elevated Windows tokens ----


def _seams(monkeypatch: pytest.MonkeyPatch, *, owner: str, token_owner: str | None) -> None:
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: _USER)
    monkeypatch.setattr(trust, "_win_token_owner_sid", lambda: token_owner, raising=False)
    monkeypatch.setattr(trust, "_win_owner_and_dacl", lambda _h: (owner, [_USER]))


def test_an_elevated_tokens_default_owner_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _seams(monkeypatch, owner=_ADMINS, token_owner=_ADMINS)
    assert trust._windows_handle_trusted(object(), check_dacl=True) is True


def test_administrators_ownership_is_refused_when_the_token_owner_is_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CONTROL: a non-elevated token's default owner is the user, so an Administrators-owned file (made
    # by someone else) stays untrusted: nothing widens for ordinary tokens.
    _seams(monkeypatch, owner=_ADMINS, token_owner=_USER)
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False


def test_the_user_owner_and_a_foreign_owner_behave_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seams(monkeypatch, owner=_USER, token_owner=_USER)
    assert trust._windows_handle_trusted(object(), check_dacl=True) is True
    _seams(monkeypatch, owner=_FOREIGN, token_owner=_ADMINS)
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False
    _seams(monkeypatch, owner=_ADMINS, token_owner=None)  # token owner unknown: user only
    assert trust._windows_handle_trusted(object(), check_dacl=True) is False


def test_the_created_secret_may_be_owned_by_the_elevated_tokens_default_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trust, "_win_current_user_sid", lambda: _USER)
    monkeypatch.setattr(trust, "_win_token_owner_sid", lambda: _ADMINS, raising=False)
    monkeypatch.setattr(trust, "_win_create_restricted", lambda _p, _s: object())
    monkeypatch.setattr(trust, "_win_owner_and_dacl", lambda _h: (_ADMINS, [_USER]))
    monkeypatch.setattr(trust._winsec, "write_all", lambda *_a, **_k: True)
    monkeypatch.setattr(trust._winsec, "close_handle", lambda _h: None)
    published: list[Path] = []
    monkeypatch.setattr(trust, "_publish_bytes_no_clobber", lambda src, dst: published.append(dst))
    target = tmp_path / "daemon-secret.json"
    trust._write_secret_windows(
        target, {"secret": "a" * 64}
    )  # must not raise "unexpected descriptor"
    assert published == [target]


@pytest.mark.skipif(sys.platform != "win32", reason="real token")
def test_the_real_token_owner_is_known_and_the_users_own_secret_is_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert trust._win_token_owner_sid() is not None
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))
    assert trust._load_or_create_user_secret() is not None
