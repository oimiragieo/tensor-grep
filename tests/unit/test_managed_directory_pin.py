from contextlib import ExitStack
from pathlib import PureWindowsPath

import pytest

from tensor_grep.cli import native_frontdoor


def _fake_directory_handles(
    monkeypatch,
    final_path=None,
    *,
    owner_ok=True,
    ancestor_dacl_ok=True,
    parent_handle_ok=True,
    file_trusted=True,
    file_links=1,
    file_final_path=None,
):
    from tensor_grep.cli import session_daemon_trust, session_daemon_winsec

    opened = {}
    calls = []
    closed = []

    def open_directory(path, *, directory=False, share=3):
        calls.append(path)
        handle = len(calls)
        opened[handle] = (path, directory)
        return handle

    def get_final_path(handle):
        path, is_directory = opened[handle]
        if not is_directory and file_final_path:
            return file_final_path(path)
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
    monkeypatch.setattr(session_daemon_winsec, "link_count", lambda _handle: file_links)
    monkeypatch.setattr(session_daemon_trust, "_win_current_user_sid", lambda: "sid")
    monkeypatch.setattr(session_daemon_trust, "_windows_owner_ok", lambda *_args: owner_ok)
    monkeypatch.setattr(
        session_daemon_trust, "_windows_ancestor_dacl_ok", lambda *_args: ancestor_dacl_ok
    )
    monkeypatch.setattr(session_daemon_trust, "_parent_handle_ok", lambda _handle: parent_handle_ok)
    monkeypatch.setattr(
        session_daemon_trust,
        "_windows_handle_trusted",
        lambda _handle, *, check_dacl: file_trusted and check_dacl,
    )
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


def test_ManagedExecutablePin_WhenPinnedLeafIsTrusted_RetainsAndClosesAllHandles(
    monkeypatch,
):
    calls, closed = _fake_directory_handles(monkeypatch)

    with ExitStack() as held:
        native_frontdoor._pin_managed_executable(
            PureWindowsPath("C:/Users/example/.tensor-grep/bin/tg.exe"), held
        )
        assert closed == []

    assert calls[-1] == "C:\\Users\\example\\.tensor-grep\\bin\\tg.exe"
    assert len(closed) == len(calls)


@pytest.mark.parametrize(
    ("file_trusted", "file_links", "file_final_path", "error"),
    [
        (False, 1, None, "ACL is not trusted"),
        (True, 2, None, "one hard link"),
        (True, 1, lambda _path: "C:\\outside\\tg.exe", "outside its pinned directory"),
    ],
)
def test_ManagedExecutablePin_WhenLeafIsUntrustedOrEscapes_RejectsAndClosesHandles(
    monkeypatch, file_trusted, file_links, file_final_path, error
):
    calls, closed = _fake_directory_handles(
        monkeypatch,
        file_trusted=file_trusted,
        file_links=file_links,
        file_final_path=file_final_path,
    )

    with ExitStack() as held, pytest.raises(RuntimeError, match=error):
        native_frontdoor._pin_managed_executable(
            PureWindowsPath("C:/Users/example/.tensor-grep/bin/tg.exe"), held
        )

    assert len(closed) == len(calls)


def test_ManagedGenerationPin_WhenGenerationContainsBothExecutables_PinsAllThreePaths(
    monkeypatch,
):
    root = PureWindowsPath(
        "C:/Users/example/.tensor-grep/runtimes/0123456789abcdef0123456789abcdef"
    )
    calls, closed = _fake_directory_handles(monkeypatch)

    with ExitStack() as held:
        native_frontdoor._pin_managed_generation_executables(root, held)
        assert closed == []

    assert str(root / ".venv") in calls
    assert str(root / "bin" / "tg.exe") in calls
    assert str(root / ".venv" / "Scripts" / "python.exe") in calls
    assert len(closed) == len(calls)
