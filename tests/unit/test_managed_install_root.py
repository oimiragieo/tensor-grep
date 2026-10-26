import sys

import pytest

from tensor_grep.cli import managed_runtime_candidate, native_frontdoor, session_daemon_trust


@pytest.mark.parametrize(
    "venv_path",
    [
        ".tensor-grep/.venv/Scripts/python.exe",
        ".tensor-grep/runtimes/0123456789abcdef0123456789abcdef/.venv/Scripts/python.exe",
    ],
)
def test_ManagedInstallRoot_WhenLegacyOrGenerationVenvMatchesOsProfile_ReturnsRoot(
    monkeypatch, tmp_path, venv_path
):
    root = tmp_path / ".tensor-grep"
    pins = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(managed_runtime_candidate, "_windows_profile_directory", lambda: tmp_path)
    monkeypatch.setattr(session_daemon_trust, "_ancestors_trusted", lambda _path: True)
    monkeypatch.setattr(
        native_frontdoor,
        "_pin_managed_directory",
        lambda path, _held: (pins.append(path), str(path))[1],
    )

    install_root = native_frontdoor.managed_windows_install_root(str(tmp_path / venv_path))

    assert install_root == root
    assert pins == [root]


def test_ManagedInstallRoot_WhenPathShapedVenvIsOutsideOsProfile_RejectsRoot(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        managed_runtime_candidate,
        "_windows_profile_directory",
        lambda: tmp_path / "profile",
    )
    monkeypatch.setattr(session_daemon_trust, "_ancestors_trusted", lambda _path: True)
    monkeypatch.setattr(
        native_frontdoor,
        "_pin_managed_directory",
        lambda *_args: pytest.fail("outside-profile paths must refuse before pinning"),
    )

    executable = tmp_path / "attacker" / ".tensor-grep" / ".venv" / "Scripts" / "python.exe"

    with pytest.raises(RuntimeError, match="outside the OS-verified user profile"):
        native_frontdoor.managed_windows_install_root(str(executable))


def test_ManagedInstallRoot_WhenExecutableIsNotWindowsManagedPython_ReturnsNone(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        managed_runtime_candidate,
        "_windows_profile_directory",
        lambda: pytest.fail("unmanaged executables do not query profile authority"),
    )

    assert native_frontdoor.managed_windows_install_root(str(tmp_path / "python.exe")) is None


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows Known Folder API")
def test_WindowsProfileDirectory_WhenKnownFolderIsAvailable_ReturnsAbsolutePath():
    profile = managed_runtime_candidate._windows_profile_directory()

    assert profile.is_absolute()
