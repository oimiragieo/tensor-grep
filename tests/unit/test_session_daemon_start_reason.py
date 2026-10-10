import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from tensor_grep.cli import (
    session_daemon,
    session_daemon_start_capture,
    session_daemon_start_diagnostics,
    session_daemon_trust,
)
from tensor_grep.cli.main import app
from tensor_grep.cli.session_daemon_start_diagnostics import DaemonStartError


def test_start_reports_child_stderr_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1)
    session_daemon._sessions_dir(project).mkdir(parents=True)
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def substituted_popen(_argv: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('RuntimeError: cannot establish per-user daemon "
                "secret (secret_dir_untrusted_owner_or_acl); refusing to serve\\n'); sys.exit(1)",
            ],
            **kwargs,
        )
        children.append(child)
        return child

    def probe(_root: Path) -> None:
        if children:
            assert children[0].wait(timeout=5) == 1
        return None

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", substituted_popen)
    monkeypatch.setattr(session_daemon, "_probe_daemon", probe)
    monkeypatch.setattr(session_daemon_start_diagnostics, "diagnose_user_secret", lambda: None)

    with pytest.raises(RuntimeError) as caught:
        session_daemon.start_session_daemon(str(project))

    assert "per-user daemon secret" in str(caught.value)
    assert "secret_dir_untrusted_owner_or_acl" in str(caught.value)
    assert list(session_daemon._sessions_dir(project).glob("daemon-start.*.stderr")) == []


def test_start_reports_final_child_error_after_long_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(session_daemon_start_diagnostics, "diagnose_user_secret", lambda: None)
    final_line = (
        "RuntimeError: cannot establish per-user daemon secret "
        "(secret_dir_untrusted_owner_or_acl); refusing to serve"
    )

    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def substituted_popen(_argv: object, **kwargs: object) -> subprocess.Popen[bytes]:
        payload = "warning: retrying\n" * 600 + final_line + "\n"
        child = real_popen(
            [sys.executable, "-c", f"import sys; sys.stderr.write({payload!r}); sys.exit(1)"],
            **kwargs,
        )
        children.append(child)
        return child

    def probe(_root: Path) -> None:
        if children:
            assert children[0].wait(timeout=5) == 1
        return None

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", substituted_popen)
    monkeypatch.setattr(session_daemon, "_probe_daemon", probe)

    with pytest.raises(RuntimeError) as caught:
        session_daemon.start_session_daemon(str(project))

    assert final_line in str(caught.value)


def test_start_capture_exists_during_child_and_is_removed_afterwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = session_daemon._sessions_dir(project)
    sessions.mkdir(parents=True)
    result_path = tmp_path / "capture-count.txt"
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1)
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def substituted_popen(_argv: object, **kwargs: object) -> subprocess.Popen[bytes]:
        script = (
            "import pathlib, sys; "
            f"p = pathlib.Path({str(result_path)!r}); "
            f"s = pathlib.Path({str(sessions)!r}); "
            "p.write_text(str(len(list(s.glob('daemon-start.*.stderr')))), encoding='utf-8'); "
            "sys.stderr.write('child startup failed\\n'); sys.exit(1)"
        )
        child = real_popen([sys.executable, "-c", script], **kwargs)
        children.append(child)
        return child

    def probe(_root: Path) -> None:
        if children:
            assert children[0].wait(timeout=5) == 1
        return None

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", substituted_popen)
    monkeypatch.setattr(session_daemon, "_probe_daemon", probe)
    monkeypatch.setattr(session_daemon_start_diagnostics, "diagnose_user_secret", lambda: None)

    with pytest.raises(RuntimeError):
        session_daemon.start_session_daemon(str(project))

    assert result_path.read_text(encoding="utf-8") == "1"
    assert list(sessions.glob("daemon-start.*.stderr")) == []


def test_real_daemon_starts_with_trusted_secret_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(secret_dir))
    if session_daemon_trust.diagnose_user_secret() is not None:
        pytest.skip("trusted secret dir unavailable on this host")
    project = tmp_path / "project"
    project.mkdir()

    try:
        assert session_daemon.start_session_daemon(str(project))["running"] is True
    finally:
        session_daemon.stop_session_daemon(str(project))


def test_spawn_refuses_symlink_at_exact_capture_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    sentinel = tmp_path / "sentinel.txt"
    original = b"must remain intact"
    sentinel.write_bytes(original)
    capture_path = sessions / "daemon-start.deadbeef.stderr"
    try:
        capture_path.symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"cannot create symlinks on this host: {exc}")
    monkeypatch.setattr(
        session_daemon_start_capture, "uuid4", lambda: SimpleNamespace(hex="deadbeef")
    )

    def unexpected_popen(*_args: object, **_kwargs: object) -> object:
        pytest.fail("a planted capture path must be refused before spawning")

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", unexpected_popen)

    with pytest.raises(FileExistsError):
        capture_fd, opened_path = session_daemon_start_capture.spawn_daemon_subprocess(
            project, sessions, lambda _root: None
        )
        session_daemon_start_capture.cleanup_daemon_start_capture(capture_fd, opened_path)

    assert sentinel.read_bytes() == original


@pytest.mark.parametrize("starts", [True, False])
def test_start_cleans_capture_after_child_writes_to_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, starts: bool
) -> None:
    monkeypatch.setattr(session_daemon_start_capture.os, "O_TEMPORARY", 0, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    sessions = session_daemon._sessions_dir(project)
    sessions.mkdir(parents=True)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1)
    live = {"host": "127.0.0.1", "port": 4321, "pid": 7, "started_at": "now"}
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def substituted_popen(_argv: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('child wrote to stderr\\n'); sys.exit(1)",
            ],
            **kwargs,
        )
        children.append(child)
        return child

    def probe(_root: Path) -> dict[str, object] | None:
        if not children:
            return None
        assert children[0].wait(timeout=5) == 1
        return live if starts else None

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", substituted_popen)
    monkeypatch.setattr(session_daemon, "_probe_daemon", probe)

    if starts:
        assert session_daemon.start_session_daemon(str(project))["running"] is True
    else:
        with pytest.raises(DaemonStartError):
            session_daemon.start_session_daemon(str(project))

    assert list(sessions.glob("daemon-start.*.stderr")) == []


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

    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: True)
    monkeypatch.setattr(session_daemon, "_release_daemon_start_lock", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 1)
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def substituted_popen(_argv: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = real_popen(
            [sys.executable, "-c", "import sys; sys.stderr.write('child failed\\n'); sys.exit(1)"],
            **kwargs,
        )
        children.append(child)
        return child

    def probe(_root: Path) -> None:
        if children:
            assert children[0].wait(timeout=5) == 1
        return None

    monkeypatch.setattr(session_daemon_start_capture.subprocess, "Popen", substituted_popen)
    monkeypatch.setattr(session_daemon, "_probe_daemon", probe)

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
        "daemon startup timed out (another tg process held the start lock) [secret_dir_unavailable]"
    )
    assert caught.value.reason_code == "secret_dir_unavailable"


def test_lock_loser_detail_and_message_include_trust_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(session_daemon, "_probe_daemon", lambda _root: None)
    monkeypatch.setattr(session_daemon, "_try_acquire_daemon_start_lock", lambda _root: False)
    monkeypatch.setattr(session_daemon, "_DAEMON_START_TIMEOUT_SECONDS", 0)
    code = "secret_dir_untrusted_owner_or_acl"
    monkeypatch.setattr(session_daemon, "diagnose_user_secret", lambda: code)

    with pytest.raises(DaemonStartError) as caught:
        session_daemon.start_session_daemon(str(project))

    assert f"[{code}]" in caught.value.detail
    assert f"[{code}]" in str(caught.value)


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
