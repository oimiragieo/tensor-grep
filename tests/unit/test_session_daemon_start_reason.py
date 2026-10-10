import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import session_daemon, session_daemon_start_capture
from tensor_grep.cli.main import app
from tensor_grep.cli.session_daemon_start_diagnostics import DaemonStartError


def test_start_reports_child_stderr_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0)
    session_daemon._sessions_dir(project).mkdir(parents=True)

    def fake_popen(*_args: object, **kwargs: object) -> object:
        stderr = kwargs["stderr"]
        message = (
            b"RuntimeError: cannot establish per-user daemon secret "
            b"(secret_dir_untrusted_owner_or_acl); refusing to serve\n"
        )
        if isinstance(stderr, int):
            os.write(stderr, message)
        else:
            stderr.write(message)
            stderr.flush()
        return object()

    monkeypatch.setattr(session_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_daemon, "diagnose_user_secret", lambda: "secret_dir_untrusted_owner_or_acl"
    )

    with pytest.raises(RuntimeError) as caught:
        session_daemon.start_session_daemon(str(project))

    assert "per-user daemon secret" in str(caught.value)
    assert "secret_dir_untrusted_owner_or_acl" in str(caught.value)
    assert list(session_daemon._sessions_dir(project).glob("daemon-start.*.stderr")) == []


def test_failed_start_does_not_follow_planted_stderr_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = session_daemon._sessions_dir(project)
    sessions.mkdir(parents=True)
    sentinel = tmp_path / "sentinel.txt"
    original = b"must remain intact"
    sentinel.write_bytes(original)
    try:
        (sessions / "daemon-start.stderr").symlink_to(sentinel)
        (sessions / "daemon-start.deadbeef.stderr").symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"cannot create symlinks on this host: {exc}")

    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0)

    def fake_popen(*_args: object, **kwargs: object) -> object:
        stderr = kwargs["stderr"]
        if isinstance(stderr, int):
            os.write(stderr, b"child failed\n")
        else:
            stderr.write(b"child failed\n")
            stderr.flush()
        return object()

    monkeypatch.setattr(session_daemon.subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError):
        session_daemon.start_session_daemon(str(project))

    assert sentinel.read_bytes() == original
    assert list(sessions.glob("daemon-start.*.stderr")) == [
        sessions / "daemon-start.deadbeef.stderr"
    ]


def test_successful_start_removes_capture_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = session_daemon._sessions_dir(project)
    sessions.mkdir(parents=True)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    probe_results = iter([
        None,
        {"host": "127.0.0.1", "port": 4321, "pid": 7, "started_at": "now"},
    ])
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: next(probe_results))
    monkeypatch.setattr(session_daemon.subprocess, "Popen", lambda *_a, **_k: object())

    result = session_daemon.start_session_daemon(str(project))

    assert result["running"] is True
    assert list(sessions.glob("daemon-start.*.stderr")) == []


def test_failed_start_preserves_daemon_error_when_capture_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(session_daemon.subprocess, "Popen", lambda *_a, **_k: object())

    def denied_unlink(_path: Path, *, missing_ok: bool = False) -> None:
        raise PermissionError("capture unlink denied")

    monkeypatch.setattr(session_daemon_start_capture.Path, "unlink", denied_unlink)

    with pytest.raises(DaemonStartError, match="daemon startup timed out"):
        session_daemon.start_session_daemon(str(project))


def test_lock_loser_reports_only_its_own_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: False)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: "secret_dir_unavailable")

    with pytest.raises(DaemonStartError) as caught:
        session_daemon.start_session_daemon(str(project))

    assert caught.value.detail == (
        "daemon startup timed out (another tg process held the start lock)"
    )
    assert caught.value.reason_code == "secret_dir_unavailable"


def test_start_json_error_contains_reason_code_and_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = DaemonStartError(
        tmp_path,
        "child failed [secret_dir_untrusted_owner_or_acl]",
        "secret_dir_untrusted_owner_or_acl",
    )
    monkeypatch.setattr(
        session_daemon,
        "start_session_daemon",
        lambda _path: (_ for _ in ()).throw(failure),
    )

    result = CliRunner().invoke(app, ["session", "daemon", "start", str(tmp_path), "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error"]["reason_code"] == "secret_dir_untrusted_owner_or_acl"
    assert payload["error"]["detail"] == "child failed [secret_dir_untrusted_owner_or_acl]"


def test_child_secret_refusal_names_diagnostic_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_daemon, "_load_or_create_user_secret", lambda: None)
    monkeypatch.setattr(
        session_daemon, "diagnose_user_secret", lambda: "secret_dir_untrusted_owner_or_acl"
    )

    with pytest.raises(RuntimeError, match="cannot establish per-user daemon secret") as caught:
        session_daemon.run_session_daemon_server(str(tmp_path))

    assert "(secret_dir_untrusted_owner_or_acl)" in str(caught.value)
