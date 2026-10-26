from contextlib import ExitStack
from pathlib import PureWindowsPath

import pytest

from tensor_grep.cli import native_frontdoor


def _fake_directory_handles(
    monkeypatch, final_path=None, *, owner_ok=True, ancestor_dacl_ok=True, parent_handle_ok=True
):
    from tensor_grep.cli import session_daemon_trust, session_daemon_winsec

    opened = {}
    calls = []
    closed = []

    def open_directory(path, *, directory=False, share=3):
        calls.append(path)
        handle = len(calls)
        opened[handle] = path
        return handle

    def get_final_path(handle):
        path = opened[handle]
        return final_path(path) if final_path else path

    def close_handle(handle):
        closed.append(handle)

    monkeypatch.setattr(native_frontdoor, "Path", PureWindowsPath)
    monkeypatch.setattr(session_daemon_winsec, "open_no_follow", open_directory)
    monkeypatch.setattr(session_daemon_winsec, "final_path", get_final_path)
    monkeypatch.setattr(session_daemon_winsec, "close_handle", close_handle)
    monkeypatch.setattr(
        session_daemon_winsec, "owner_and_dacl_entries", lambda _handle: ("sid", [])
    )
    monkeypatch.setattr(session_daemon_trust, "_win_current_user_sid", lambda: "sid")
    monkeypatch.setattr(session_daemon_trust, "_windows_owner_ok", lambda *_args: owner_ok)
    monkeypatch.setattr(
        session_daemon_trust, "_windows_ancestor_dacl_ok", lambda *_args: ancestor_dacl_ok
    )
    monkeypatch.setattr(session_daemon_trust, "_parent_handle_ok", lambda _handle: parent_handle_ok)
    return calls, closed


def test_ManagedDirectoryPin_WhenEveryHandleMatchesPath_RetainsAllOpenedDirectories(
    monkeypatch,
):
    calls, closed = _fake_directory_handles(monkeypatch)

    with ExitStack() as held:
        final_path = native_frontdoor._pin_managed_directory(
            PureWindowsPath("C:/Users/example/.tensor-grep"), held
        )
        assert closed == []

    assert calls[0] == "C:\\"
    assert calls[-1].endswith(".tensor-grep")
    assert final_path.endswith(".tensor-grep")
    assert len(closed) == len(calls)


def test_ManagedDirectoryPin_WhenManagedRootResolvesOutsideParent_RejectsPathSwap(
    monkeypatch,
):
    calls, closed = _fake_directory_handles(
        monkeypatch,
        lambda path: "C:\\outside" if path.endswith(".tensor-grep") else path,
    )

    with ExitStack() as held, pytest.raises(RuntimeError, match="identity changed"):
        native_frontdoor._pin_managed_directory(
            PureWindowsPath("C:/Users/example/.tensor-grep"), held
        )

    assert calls[-1].endswith(".tensor-grep")
    assert len(closed) == len(calls)


@pytest.mark.parametrize(
    ("owner_ok", "ancestor_dacl_ok", "parent_handle_ok"),
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_ManagedDirectoryPin_WhenAnyDirectoryAclIsUntrusted_RejectsPath(
    monkeypatch, owner_ok, ancestor_dacl_ok, parent_handle_ok
):
    calls, closed = _fake_directory_handles(
        monkeypatch,
        owner_ok=owner_ok,
        ancestor_dacl_ok=ancestor_dacl_ok,
        parent_handle_ok=parent_handle_ok,
    )

    with ExitStack() as held, pytest.raises(RuntimeError, match="not trusted"):
        native_frontdoor._pin_managed_directory(
            PureWindowsPath("C:/Users/example/.tensor-grep"), held
        )

    assert len(closed) == len(calls)


@pytest.mark.parametrize("path", [PureWindowsPath("C:/"), PureWindowsPath("//server/share/root")])
def test_ManagedDirectoryPin_WhenPathIsNotBelowLocalDriveRoot_RejectsBeforeOpen(monkeypatch, path):
    calls, _closed = _fake_directory_handles(monkeypatch)

    with ExitStack() as held, pytest.raises(RuntimeError, match="below a local drive root"):
        native_frontdoor._pin_managed_directory(path, held)

    assert calls == []
