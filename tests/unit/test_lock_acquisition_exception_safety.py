"""Two-stage locks (OS sidecar, then the legacy protocol) must release the sidecar on ANY failure.

Otherwise a long-lived process (MCP server, LSP) that hit one transient error (ENOSPC, Ctrl-C)
could never start a daemon / take an index lock again until it exited.
"""

from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import _index_lock as il
from tensor_grep.cli import session_daemon_start_lock as sl


def _spy_releases(monkeypatch: pytest.MonkeyPatch, module: Any) -> list[int]:
    released: list[int] = []
    real = module.release_os_file_lock

    def _spy(fd: int) -> None:
        released.append(fd)
        real(fd)

    monkeypatch.setattr(module, "release_os_file_lock", _spy)
    return released


def test_normal_start_lock_cycle_still_works(tmp_path: Path) -> None:
    # POSITIVE CONTROL for the fault-injection tests below.
    root = tmp_path.resolve()
    assert sl._try_acquire_daemon_start_lock(root) is True
    assert sl._try_acquire_daemon_start_lock(root) is False  # held: not re-entrant
    sl._release_daemon_start_lock(root)
    assert sl._try_acquire_daemon_start_lock(root) is True
    sl._release_daemon_start_lock(root)


@pytest.mark.parametrize(
    "error",
    [OSError(errno.ENOSPC, "No space left on device"), KeyboardInterrupt()],
    ids=["OSError-ENOSPC", "KeyboardInterrupt"],
)
def test_start_lock_releases_the_sidecar_when_the_legacy_step_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    root = tmp_path.resolve()
    released = _spy_releases(monkeypatch, sl)
    real_legacy = sl._legacy_try_acquire_daemon_start_lock

    def _boom(_root: Path, **_k: Any) -> bool:
        raise error

    monkeypatch.setattr(sl, "_legacy_try_acquire_daemon_start_lock", _boom)
    with pytest.raises(type(error)) as caught:
        sl._try_acquire_daemon_start_lock(root)
    assert caught.value is error  # the primary error is kept
    assert len(released) == 1, "the sidecar fd was not released on the failure path"
    assert sl._DAEMON_START_LOCK_FDS == {}

    monkeypatch.setattr(sl, "_legacy_try_acquire_daemon_start_lock", real_legacy)
    assert sl._try_acquire_daemon_start_lock(root) is True, "a failed attempt blocked later starts"
    sl._release_daemon_start_lock(root)


def test_start_lock_releases_the_sidecar_when_the_legacy_step_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    released = _spy_releases(monkeypatch, sl)
    real_legacy = sl._legacy_try_acquire_daemon_start_lock
    monkeypatch.setattr(sl, "_legacy_try_acquire_daemon_start_lock", lambda _r, **_k: False)
    assert sl._try_acquire_daemon_start_lock(root) is False
    assert len(released) == 1
    monkeypatch.setattr(sl, "_legacy_try_acquire_daemon_start_lock", real_legacy)
    assert sl._try_acquire_daemon_start_lock(root) is True
    sl._release_daemon_start_lock(root)


def test_start_lock_releases_both_stages_when_registration_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()

    class _ExplodingGuard:
        def __enter__(self) -> None:
            raise RuntimeError("registration failed")

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(sl, "_DAEMON_START_LOCK_GUARD", _ExplodingGuard())
    with pytest.raises(RuntimeError, match="registration failed"):
        sl._try_acquire_daemon_start_lock(root)
    assert not sl._daemon_start_lock_path(root).exists(), "the legacy lock file was left behind"
    monkeypatch.undo()
    assert sl._try_acquire_daemon_start_lock(root) is True
    sl._release_daemon_start_lock(root)


@pytest.mark.parametrize(
    "error",
    [OSError(errno.ENOSPC, "No space left on device"), KeyboardInterrupt()],
    ids=["OSError-ENOSPC", "KeyboardInterrupt"],
)
def test_index_lock_releases_the_sidecar_when_the_legacy_step_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    index = tmp_path / "index.json"
    real_legacy = il._legacy_index_lock

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise error

    monkeypatch.setattr(il, "_legacy_index_lock", _boom)
    with pytest.raises(type(error)):
        with il.index_lock(index, timeout_s=2):
            pytest.fail("entered the lock although the legacy step raised")
    monkeypatch.setattr(il, "_legacy_index_lock", real_legacy)
    with il.index_lock(index, timeout_s=2, poll_interval_s=0.01):  # sidecar must be free again
        pass


def test_index_lock_body_exception_still_releases_everything(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    with pytest.raises(ValueError):
        with il.index_lock(index, timeout_s=2):
            raise ValueError("body failed")
    with il.index_lock(index, timeout_s=2, poll_interval_s=0.01):
        pass


def test_os_lock_registration_failure_does_not_leak_the_held_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = tmp_path / ".x.lock.os"

    def _boom(_fd: int) -> None:
        raise OSError(errno.ENOMEM, "registration failed")

    monkeypatch.setattr(il, "_register_held_fd", _boom)
    with pytest.raises(OSError):
        il.try_os_file_lock(sidecar)
    monkeypatch.undo()
    fd = il.try_os_file_lock(sidecar)
    assert fd is not None, "a failed registration leaked the OS lock"
    il.release_os_file_lock(fd)
