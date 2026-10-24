from pathlib import Path

import pytest

from tensor_grep.cli import session_daemon


@pytest.fixture
def autostart_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()

    def capture_without_child(resolved_root: Path) -> tuple[int, Path]:
        import os

        (resolved_root / "child-spawn-requested").write_text("requested", encoding="utf-8")
        capture = resolved_root / "capture.log"
        return os.open(capture, os.O_CREAT | os.O_EXCL | os.O_WRONLY), capture

    monkeypatch.setattr(session_daemon, "_spawn_daemon_subprocess", capture_without_child)
    return root


@pytest.mark.parametrize(
    "reason",
    [
        "secret_dir_untrusted_owner_or_acl",
        "secret_ancestor_untrusted",
        "secret_untrusted_present",
        "secret_extra_links",
    ],
)
def test_autostart_KnownSecretTrustRefusal_ReturnsFalse(
    autostart_root: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: reason)

    result = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))

    assert result is False
    assert not (autostart_root / "child-spawn-requested").exists()


@pytest.mark.parametrize("reason", [None, "secret_dir_unavailable", "secret_io_error"])
def test_autostart_TrustedOrFirstRunSecret_AllowsInitialization(
    autostart_root: Path, monkeypatch: pytest.MonkeyPatch, reason: str | None
) -> None:
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: reason)

    result = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))

    assert result is True
    assert (autostart_root / "child-spawn-requested").read_text(encoding="utf-8") == "requested"
    assert not (autostart_root / "capture.log").exists()


def test_autostart_TrustRepairedOnSameRoot_AllowsNextAttempt(
    autostart_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        session_daemon, "diagnose_user_secret", lambda: "secret_dir_untrusted_owner_or_acl"
    )
    refused = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: None)

    recovered = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))

    assert refused is False
    assert recovered is True
    assert (autostart_root / "child-spawn-requested").read_text(encoding="utf-8") == "requested"
    assert not (autostart_root / "capture.log").exists()


def test_autostart_PreviousAttemptCompleted_ReleasesStartLock(
    autostart_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: None)

    first = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))
    second = session_daemon.maybe_autostart_session_daemon_nonblocking(str(autostart_root))

    assert first is True
    assert second is True
    assert not (autostart_root / "capture.log").exists()
    assert (autostart_root / "child-spawn-requested").read_text(encoding="utf-8") == "requested"
