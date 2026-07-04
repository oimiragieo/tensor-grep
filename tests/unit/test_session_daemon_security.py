"""Security/retention regression tests for the session daemon and store.

Covers the audit findings hardened in ``session_daemon.py`` / ``session_store.py``:

* S3 - daemon IPC requires a per-daemon token and confines request paths to its root.
* I2 - ``open_session`` prunes on-disk sessions to a configurable retention bound.
* I7 - the PID-kill stop fallback refuses to terminate processes it cannot validate.
* S9 - session payload lookup is confined to the explicit root by default.

The token/path tests drive a real loopback server but only exercise the ``ping`` command,
so they do not require the compiled rust_core extension. The retention/lookup tests stub the
repo-map builder so they import only the light session-store path.
"""

from __future__ import annotations

import io
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import tensor_grep.cli.session_daemon as session_daemon
from tensor_grep.cli import session_store


def _serve(server: session_daemon._ThreadedSessionDaemon) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _raw_request(host: str, port: int, request: dict[str, Any]) -> dict[str, Any]:
    """Send a request verbatim (no token injection) to exercise the auth boundary."""
    return session_daemon._daemon_request(host, port, request)


# --------------------------------------------------------------------------- S3: auth


def test_is_authorized_requires_matching_token() -> None:
    server = session_daemon._ThreadedSessionDaemon(
        Path.cwd(), ("127.0.0.1", 0), token="s3cr3t-token"
    )
    try:
        assert server.is_authorized({"token": "s3cr3t-token"}) is True
        assert server.is_authorized({"token": "wrong"}) is False
        assert server.is_authorized({}) is False
        assert server.is_authorized({"token": ""}) is False
        assert server.is_authorized({"token": 123}) is False  # non-string rejected
    finally:
        server.server_close()


def test_tokenless_server_stays_backward_compatible() -> None:
    # A server constructed without a token (legacy/in-test path) must not reject requests.
    server = session_daemon._ThreadedSessionDaemon(Path.cwd(), ("127.0.0.1", 0))
    try:
        assert server.is_authorized({}) is True
        assert server.is_authorized({"command": "ping"}) is True
    finally:
        server.server_close()


def test_daemon_rejects_missing_and_wrong_token(tmp_path: Path) -> None:
    token = "correct-horse-battery-staple"
    server = session_daemon._ThreadedSessionDaemon(
        tmp_path.resolve(), ("127.0.0.1", 0), token=token
    )
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])

        # Missing token -> unauthorized.
        missing = _raw_request(host, port, {"command": "ping"})
        assert missing.get("error", {}).get("code") == "unauthorized"
        assert "ok" not in missing

        # Wrong token -> unauthorized.
        wrong = _raw_request(host, port, {"command": "ping", "token": "nope"})
        assert wrong.get("error", {}).get("code") == "unauthorized"

        # Correct token -> served.
        ok = _raw_request(host, port, {"command": "ping", "token": token})
        assert ok.get("ok") is True

        # The high-level helper injects the token transparently for legitimate callers.
        injected = session_daemon._daemon_request(host, port, {"command": "ping"}, token=token)
        assert injected.get("ok") is True
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_unauthorized_request_does_not_count_activity(tmp_path: Path) -> None:
    server = session_daemon._ThreadedSessionDaemon(
        tmp_path.resolve(), ("127.0.0.1", 0), token="tok"
    )
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        before = server.last_activity_at
        _raw_request(host, port, {"command": "ping"})  # rejected, no activity bump
        assert server.last_activity_at == before
        session_daemon._daemon_request(host, port, {"command": "ping"}, token="tok")
        assert server.last_activity_at >= before
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


# ----------------------------------------------------- round-3: pre-auth request DoS guard


def test_read_bounded_request_line_accepts_small_request() -> None:
    buf = io.BytesIO(b'{"command":"ping"}\n')
    assert session_daemon._read_bounded_request_line(buf, max_bytes=1024) == '{"command":"ping"}'


def test_read_bounded_request_line_refuses_oversized_pre_auth() -> None:
    # A hostile local client streams past the cap with no newline. An unbounded
    # readline() would buffer it all into memory BEFORE the token check; the bounded
    # read must refuse it (return None) without materializing an unbounded line.
    payload = b"A" * 4096  # no newline, exceeds the small cap
    assert session_daemon._read_bounded_request_line(io.BytesIO(payload), max_bytes=1024) is None


def test_read_bounded_request_line_returns_none_on_empty() -> None:
    assert session_daemon._read_bounded_request_line(io.BytesIO(b""), max_bytes=1024) is None


def test_read_bounded_request_line_returns_none_on_read_error() -> None:
    class _Boom:
        def readline(self, _size: int = -1) -> bytes:
            raise TimeoutError("slow/silent client")

    assert session_daemon._read_bounded_request_line(_Boom(), max_bytes=1024) is None


def test_daemon_handler_sets_socket_timeout() -> None:
    # A silent/slow client must not pin a worker thread forever before authenticating.
    timeout = session_daemon._SessionDaemonHandler.timeout
    assert timeout is not None and timeout > 0


def test_write_daemon_metadata_locks_down_token_file_on_windows(
    tmp_path: Path, monkeypatch
) -> None:
    # round-7 r7: on Windows, 0600 is a no-op ACL, so the token file must be restricted via icacls.
    calls: list[list[str]] = []

    def _fake_run(argv, **_kwargs):
        calls.append(list(argv))

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(session_daemon.subprocess, "run", _fake_run)
    monkeypatch.setattr(session_daemon.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "testuser")

    session_daemon._write_daemon_metadata(tmp_path.resolve(), {"token": "sekret", "version": 1})

    icacls_calls = [argv for argv in calls if argv and argv[0] == "icacls"]
    assert icacls_calls, "daemon.json write did not lock down ACLs on Windows"
    argv = icacls_calls[0]
    assert "/inheritance:r" in argv and "/grant:r" in argv and "testuser:F" in argv


def test_restrict_windows_file_is_noop_off_windows(tmp_path: Path, monkeypatch) -> None:
    called: list[object] = []
    monkeypatch.setattr(session_daemon.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(session_daemon.sys, "platform", "linux")
    session_daemon._restrict_windows_file_to_current_user(tmp_path / "daemon.json")
    assert called == []  # no subprocess spawned on non-Windows


# ---------------------------------------------------------------- S3: path confinement


def test_confine_path_to_root_rejects_escape(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    inside = root / "src"
    inside.mkdir()
    outside = (tmp_path / "other").resolve()
    outside.mkdir()

    assert session_daemon._confine_path_to_root(root, root) == root
    assert session_daemon._confine_path_to_root(root, inside) == inside
    # Escaping paths fall back to the daemon root.
    assert session_daemon._confine_path_to_root(root, outside) == root
    assert session_daemon._confine_path_to_root(root, root.parent) == root


def test_resolve_daemon_request_path_confines_absolute_escape(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    (root / "src").mkdir()
    outside = (tmp_path / "secrets").resolve()
    outside.mkdir()

    # Absolute path escaping the root is refused (confined back to root).
    assert session_daemon._resolve_daemon_request_path(root, str(outside)) == str(root)
    # A path inside the root is preserved.
    assert session_daemon._resolve_daemon_request_path(root, str(root / "src")) == str(root / "src")
    # Empty request path resolves to the root.
    assert session_daemon._resolve_daemon_request_path(root, "") == str(root)
    # Relative paths are rooted under the daemon root and stay confined.
    assert session_daemon._resolve_daemon_request_path(root, "src") == str(root / "src")
    assert session_daemon._resolve_daemon_request_path(root, "../secrets") == str(root)


def test_daemon_request_path_field_cannot_escape_root(tmp_path: Path, monkeypatch) -> None:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()

    seen_paths: list[str] = []

    def _fake_serve(
        session_id: str,
        request: dict[str, Any],
        path: str,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        seen_paths.append(path)
        return {"ok": True, "session_id": session_id, "path": path}

    # Avoid touching the real session store / repo map for this routing-only assertion.
    monkeypatch.setattr(session_daemon, "serve_session_request", _fake_serve)
    monkeypatch.setattr(
        session_daemon,
        "_implicit_session_id_for_request",
        lambda server, *, command, session_id, path, request: session_id or "session-x",
    )
    monkeypatch.setattr(
        session_daemon,
        "_load_payload_with_status_retry",
        lambda cache, session_id, path: ({"root": str(root), "repo_map": {}}, "miss"),
    )

    server = session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token="tok")
    thread = _serve(server)
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        response = session_daemon._daemon_request(
            host,
            port,
            {
                "command": "defs",
                "session_id": "session-x",
                "path": str(outside),
                "root": str(outside),
                "symbol": "foo",
            },
            token="tok",
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert response.get("ok") is True
    # The path the handler dispatched with must be confined to the daemon root, never `outside`.
    assert seen_paths == [str(root)]


# -------------------------------------------------- round-3: atomic-write permission window


def test_write_json_atomic_creates_sensitive_temp_without_world_readable_window(
    tmp_path: Path, monkeypatch
) -> None:
    """A mode-restricted atomic write must CREATE its temp at that mode, not create it
    world-readable and chmod afterwards (a window where another user could read the token)."""
    target = tmp_path / "daemon.json"
    created_modes: list[int] = []
    real_open = os.open

    def _spy_open(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
        if flags & os.O_CREAT:
            created_modes.append(mode)
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(session_store.os, "open", _spy_open)
    session_store._write_json_atomic(target, {"token": "s3cr3t"}, mode=0o600)

    assert target.exists()
    # The temp had to be created via os.open with an explicit restrictive mode ...
    assert created_modes, "sensitive temp must be created via os.open(O_CREAT, mode)"
    # ... and that mode must never grant group/other any bits.
    assert all((created_mode & 0o077) == 0 for created_mode in created_modes)


def test_write_json_atomic_final_mode_is_restrictive_on_posix(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits are only meaningful on POSIX")
    target = tmp_path / "daemon.json"
    session_store._write_json_atomic(target, {"token": "s3cr3t"}, mode=0o600)
    assert (target.stat().st_mode & 0o777) == 0o600


def test_write_json_atomic_default_mode_unchanged(tmp_path: Path) -> None:
    # Non-sensitive writes (index/session payloads) keep the default-perms path.
    target = tmp_path / "index.json"
    session_store._write_json_atomic(target, {"records": []})
    assert json.loads(target.read_text(encoding="utf-8")) == {"records": []}


# ------------------------------------------------------------------- I2: retention bound


def _stub_repo_map(monkeypatch) -> None:
    def _fake_build_repo_map(root: Path, *, max_repo_files: int | None = None) -> dict[str, Any]:
        return {
            "related_paths": [],
            "files": [],
            "symbols": [],
            "scan_limit": None,
            "path": str(root),
        }

    monkeypatch.setattr(session_store, "build_repo_map", _fake_build_repo_map)


def test_open_session_prunes_oldest_payloads(tmp_path: Path, monkeypatch) -> None:
    _stub_repo_map(monkeypatch)
    monkeypatch.setenv(session_store._SESSION_MAX_ENV, "3")
    project = (tmp_path / "project").resolve()
    project.mkdir()

    opened_ids: list[str] = []
    for _ in range(6):
        result = session_store.open_session(str(project))
        opened_ids.append(result.session_id)

    records = session_store._load_index(project)
    assert len(records) == 3, "index must be bounded to TG_SESSION_MAX"

    retained_ids = {record.session_id for record in records}
    # The three most recently opened sessions are retained (index is newest-first).
    assert retained_ids == set(opened_ids[-3:])

    # Dropped payload files are unlinked; retained ones still exist.
    for dropped_id in opened_ids[:-3]:
        assert not session_store._session_payload_path(project, dropped_id).exists()
    for kept_id in opened_ids[-3:]:
        assert session_store._session_payload_path(project, kept_id).exists()


def test_session_max_defaults_to_64(monkeypatch) -> None:
    monkeypatch.delenv(session_store._SESSION_MAX_ENV, raising=False)
    assert session_store._configured_session_max() == session_store._DEFAULT_SESSION_MAX == 64
    monkeypatch.setenv(session_store._SESSION_MAX_ENV, "0")
    assert session_store._configured_session_max() == 64  # non-positive falls back
    monkeypatch.setenv(session_store._SESSION_MAX_ENV, "garbage")
    assert session_store._configured_session_max() == 64


def test_prune_session_records_keeps_newest(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    sessions_dir = session_store._sessions_dir(root)
    sessions_dir.mkdir(parents=True)
    records: list[session_store.SessionRecord] = []
    for index in range(5):
        session_id = f"session-{index}"
        session_store._session_payload_path(root, session_id).write_text("{}", encoding="utf-8")
        records.append(
            session_store.SessionRecord(
                version=session_store._SESSION_VERSION,
                session_id=session_id,
                root=str(root),
                created_at=f"2026-01-0{index}T00:00:00+00:00",
                file_count=0,
                symbol_count=0,
            )
        )

    retained = session_store._prune_session_records(root, records, max_records=2)
    assert [record.session_id for record in retained] == ["session-0", "session-1"]
    assert session_store._session_payload_path(root, "session-0").exists()
    for dropped in ("session-2", "session-3", "session-4"):
        assert not session_store._session_payload_path(root, dropped).exists()


# ----------------------------------------------------------------------- I7: PID kill


def test_terminate_daemon_by_pid_refuses_unvalidated(monkeypatch) -> None:
    # Without psutil validation, the kill is skipped (never kills an unrelated pid).
    monkeypatch.setattr(session_daemon, "_pid_looks_like_tg_daemon", lambda pid: False)
    assert session_daemon._terminate_daemon_by_pid({"pid": 999999}) is False
    assert session_daemon._terminate_daemon_by_pid(None) is False
    assert session_daemon._terminate_daemon_by_pid({}) is False
    assert session_daemon._terminate_daemon_by_pid({"pid": "not-an-int"}) is False


def test_terminate_daemon_by_pid_refuses_self(monkeypatch) -> None:
    # Even if validation passes, the daemon must never signal its own pid.
    monkeypatch.setattr(session_daemon, "_pid_looks_like_tg_daemon", lambda pid: True)
    assert session_daemon._terminate_daemon_by_pid({"pid": os.getpid()}) is False


def test_pid_looks_like_tg_daemon_rejects_invalid() -> None:
    assert session_daemon._pid_looks_like_tg_daemon(0) is False
    assert session_daemon._pid_looks_like_tg_daemon(-1) is False


# --------------------------------------------------------------- I7: idle/uptime shutdown


def test_lifecycle_seconds_config_parsing(monkeypatch) -> None:
    env = "TG_TEST_LIFECYCLE"
    monkeypatch.delenv(env, raising=False)
    assert session_daemon._configured_lifecycle_seconds(env, 42.0) == 42.0
    monkeypatch.setenv(env, "1.5")
    assert session_daemon._configured_lifecycle_seconds(env, 42.0) == 1.5
    monkeypatch.setenv(env, "garbage")
    assert session_daemon._configured_lifecycle_seconds(env, 42.0) == 42.0
    # Non-positive disables the limit (zero is honored as a disable sentinel by the monitor).
    monkeypatch.setenv(env, "0")
    assert session_daemon._configured_lifecycle_seconds(env, 42.0) == 0.0


def test_lifecycle_monitor_shuts_down_on_max_uptime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(session_daemon, "_DAEMON_LIFECYCLE_POLL_SECONDS", 0.01)
    monkeypatch.setenv(session_daemon._DAEMON_MAX_UPTIME_SECONDS_ENV, "0.05")
    monkeypatch.setenv(session_daemon._DAEMON_IDLE_SHUTDOWN_SECONDS_ENV, "0")

    server = session_daemon._ThreadedSessionDaemon(
        tmp_path.resolve(), ("127.0.0.1", 0), token="tok"
    )
    serve_thread = _serve(server)
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=session_daemon._run_daemon_lifecycle_monitor,
        args=(server, stop_event),
        daemon=True,
    )
    try:
        # Backdate the start so the max-uptime threshold is already exceeded.
        server.started_at -= 10.0
        monitor.start()
        serve_thread.join(timeout=3)
        assert not serve_thread.is_alive(), "daemon should self-shutdown past max uptime"
    finally:
        stop_event.set()
        server.shutdown()
        serve_thread.join(timeout=2)
        server.server_close()


def test_lifecycle_monitor_returns_when_both_limits_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(session_daemon._DAEMON_MAX_UPTIME_SECONDS_ENV, "0")
    monkeypatch.setenv(session_daemon._DAEMON_IDLE_SHUTDOWN_SECONDS_ENV, "0")
    server = session_daemon._ThreadedSessionDaemon(tmp_path.resolve(), ("127.0.0.1", 0))
    stop_event = threading.Event()
    try:
        # With both limits disabled the monitor exits immediately instead of looping forever.
        monitor = threading.Thread(
            target=session_daemon._run_daemon_lifecycle_monitor,
            args=(server, stop_event),
            daemon=True,
        )
        monitor.start()
        monitor.join(timeout=2)
        assert not monitor.is_alive()
    finally:
        stop_event.set()
        server.server_close()


# -------------------------------------------------------------------- S9: lookup confinement


def test_get_session_rejects_root_mismatch(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    other = (tmp_path / "elsewhere").resolve()
    other.mkdir()
    sessions_dir = session_store._sessions_dir(root)
    sessions_dir.mkdir(parents=True)
    session_id = "session-mismatch"
    # Payload claims a different root than the directory it is stored under.
    payload_path = session_store._session_payload_path(root, session_id)
    payload_path.write_text(
        json.dumps({"root": str(other), "repo_map": {"files": [], "symbols": []}}),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        session_store.get_session(session_id, str(root))


def test_get_session_accepts_matching_root(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    sessions_dir = session_store._sessions_dir(root)
    sessions_dir.mkdir(parents=True)
    session_id = "session-ok"
    session_store._session_payload_path(root, session_id).write_text(
        json.dumps({"root": str(root), "repo_map": {"files": [], "symbols": []}}),
        encoding="utf-8",
    )
    payload = session_store.get_session(session_id, str(root))
    assert payload["root"] == str(root)


def test_nearby_lookup_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(session_store._SESSION_NEARBY_LOOKUP_ENV, raising=False)
    assert session_store._nearby_lookup_enabled() is False

    parent = (tmp_path / "parent").resolve()
    child = parent / "child"
    child.mkdir(parents=True)
    sibling_session = "session-sibling"
    # Place a payload under the PARENT (a "nearby" root) but query from the CHILD.
    parent_sessions = session_store._sessions_dir(parent)
    parent_sessions.mkdir(parents=True)
    session_store._session_payload_path(parent, sibling_session).write_text(
        json.dumps({"root": str(parent), "repo_map": {"files": [], "symbols": []}}),
        encoding="utf-8",
    )
    # An index.json is required for the parent to be discoverable as a "nearby" root.
    session_store._write_index(
        parent,
        [
            session_store.SessionRecord(
                version=session_store._SESSION_VERSION,
                session_id=sibling_session,
                root=str(parent),
                created_at="2026-01-01T00:00:00+00:00",
                file_count=0,
                symbol_count=0,
            )
        ],
    )

    # With confinement on (default), the child must NOT resolve into the parent's payload.
    resolved = session_store._session_root_for_payload(sibling_session, str(child))
    assert resolved == child
    with pytest.raises(FileNotFoundError):
        session_store.get_session(sibling_session, str(child))

    # Opting in re-enables the legacy cross-root discovery.
    monkeypatch.setenv(session_store._SESSION_NEARBY_LOOKUP_ENV, "1")
    assert session_store._nearby_lookup_enabled() is True
    resolved_optin = session_store._session_root_for_payload(sibling_session, str(child))
    assert resolved_optin == parent
