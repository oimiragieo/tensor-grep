"""TDD for task #94 PR-1 safety addition #2: close the daemon/client package-version skew gap.

Before this PR, ``daemon.json`` recorded only the payload-schema ``version`` (an integer,
``session_store._SESSION_VERSION``), and ``_probe_daemon`` did no version comparison at all -- it
only pinged the daemon and checked ``response.get("ok")``. A daemon that survives a ``tg upgrade``
(daemons live up to ``TG_SESSION_DAEMON_MAX_UPTIME_SECONDS``, 24h default) would keep serving
stale-code responses to a freshly-upgraded client with no signal that anything was wrong.

The fix: the daemon writes the installed tensor-grep PACKAGE version (``runtime_paths.
_expected_tg_version()``, the same accessor already used to detect native-binary version skew)
into ``daemon.json`` at startup, and ``_probe_daemon`` treats a mismatch -- including a
``daemon.json`` from before this PR that has no ``package_version`` field at all -- as no-daemon
(``None``), which routes the caller to the existing cold path + non-blocking respawn. That respawn
(``_spawn_daemon_subprocess``) already unconditionally removes the stale ``daemon.json`` before
launching, so the stale metadata self-heals; the orphaned old daemon process eventually shuts
itself down via the existing idle/max-uptime lifecycle monitor.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from tensor_grep.cli import session_daemon
from tensor_grep.cli.runtime_paths import _expected_tg_version


def _serve(server: session_daemon._ThreadedSessionDaemon) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _real_daemon(root: Path, token: str = "test-token") -> session_daemon._ThreadedSessionDaemon:
    """Start a REAL (in-process, threaded, loopback) session daemon for `root`, mirroring the
    helper in test_symbol_daemon_autostart.py."""
    return session_daemon._ThreadedSessionDaemon(root, ("127.0.0.1", 0), token=token)


def _publish_daemon_metadata(
    server: session_daemon._ThreadedSessionDaemon,
    root: Path,
    token: str,
    *,
    package_version: str | None,
) -> None:
    """Hand-write daemon.json the way run_session_daemon_server does, with a controllable
    package_version (including omitting the field entirely, to simulate a pre-PR-1 daemon)."""
    host, port = server.server_address
    payload: dict[str, Any] = {
        "version": 1,
        "root": str(root),
        "host": str(host),
        "port": int(port),
        "pid": 0,
        "started_at": "test",
        "token": token,
    }
    if package_version is not None:
        payload["package_version"] = package_version
    session_daemon._write_daemon_metadata(root, payload)


def test_probe_daemon_rejects_package_version_mismatch(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = _real_daemon(root)
    _serve(server)
    try:
        _publish_daemon_metadata(server, root, "test-token", package_version="0.0.0-stale-fixture")
        assert session_daemon._probe_daemon(root) is None
    finally:
        server.shutdown()
        server.server_close()


def test_probe_daemon_rejects_missing_package_version_field(tmp_path: Path) -> None:
    """A pre-PR-1 daemon.json (written before this field existed) must fail closed to the cold
    path, not be trusted just because the ping still succeeds."""
    root = tmp_path.resolve()
    server = _real_daemon(root)
    _serve(server)
    try:
        _publish_daemon_metadata(server, root, "test-token", package_version=None)
        assert session_daemon._probe_daemon(root) is None
    finally:
        server.shutdown()
        server.server_close()


def test_probe_daemon_serves_when_package_version_matches(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    server = _real_daemon(root)
    _serve(server)
    try:
        _publish_daemon_metadata(server, root, "test-token", package_version=_expected_tg_version())
        metadata = session_daemon._probe_daemon(root)
        assert metadata is not None
        assert metadata["package_version"] == _expected_tg_version()
    finally:
        server.shutdown()
        server.server_close()


def test_request_running_session_daemon_falls_through_on_version_mismatch(
    tmp_path: Path,
) -> None:
    """One layer up: request_running_session_daemon (the function the autostart fast path
    actually calls) must return None -- not raise, not serve a stale answer -- on a version
    mismatch, exactly as it already does for "no daemon reachable"."""
    root = tmp_path.resolve()
    server = _real_daemon(root)
    _serve(server)
    try:
        _publish_daemon_metadata(server, root, "test-token", package_version="0.0.0-stale-fixture")
        result = session_daemon.request_running_session_daemon(
            str(root), {"command": "defs", "path": str(root), "symbol": "helper"}
        )
        assert result is None
    finally:
        server.shutdown()
        server.server_close()


def test_real_daemon_subprocess_writes_current_package_version(tmp_path: Path) -> None:
    """Write-side proof against the REAL production path (run_session_daemon_server via a real
    subprocess spawn, reusing start_session_daemon/stop_session_daemon) -- not just the
    hand-written fixture above."""
    root = tmp_path.resolve()
    started = session_daemon.start_session_daemon(str(root))
    try:
        assert started["running"] is True
        metadata = session_daemon._read_daemon_metadata(root)
        assert metadata is not None
        assert metadata.get("package_version") == _expected_tg_version()
        # And the full _probe_daemon round-trip (real socket ping + the new version check)
        # must accept the daemon it just spawned.
        assert session_daemon._probe_daemon(root) is not None
    finally:
        session_daemon.stop_session_daemon(str(root))
