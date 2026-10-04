"""``tg session daemon stop`` must not report an UNCONFIRMED stop as success (PR #1197 round 3).

When the helper returns ``running=True, stopped=False`` the CLI used to print "Session daemon not
running" and exit 0, in ``--json`` mode too. Now it prints an ASCII message saying shutdown could not
be confirmed (with the reason) and exits 2, using the structured ``error`` shape in JSON mode. These
tests drive the REAL CLI in a subprocess.
"""

from __future__ import annotations

import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import session_daemon as sd
from tensor_grep.cli import session_daemon_trust as trust
from tensor_grep.cli.runtime_paths import _expected_tg_version

_SRC = str(Path(sd.__file__).resolve().parents[2])


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(tmp_path / "secret"))


def _run_cli(root: Path, *flags: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": _SRC, "TG_SESSION_DAEMON_AUTOSTART": "0"}
    return subprocess.run(
        [sys.executable, "-m", "tensor_grep", "session", "daemon", "stop", str(root), *flags],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


class _UnauthorizedStopDaemon:
    """A loopback daemon that answers the FIRST signed ping correctly, replies to ``stop`` with
    ``{"ok": false, "error": {"code": "unauthorized"}}``, then stops answering pings while its
    listener stays open: the round-3 repro."""

    def __init__(self, root: Path) -> None:
        self.root = root
        outer = self
        self.pings = 0

        class _Handler(socketserver.StreamRequestHandler):
            def handle(self_inner) -> None:
                try:
                    request = json.loads(self_inner.rfile.readline())
                except ValueError:
                    return
                command = request.get("command")
                if command == "ping":
                    outer.pings += 1
                    if outer.pings > 1:
                        return  # close without a reply: the later ping fails
                    reply: dict[str, Any] = {"version": 1, "ok": True}
                    reply.update(
                        trust._ping_proof_fields(
                            request.get("nonce"), root, int(outer.server.server_address[1])
                        )
                    )
                elif command == "stop":
                    reply = {"version": 1, "ok": False, "error": {"code": "unauthorized"}}
                else:
                    reply = {"version": 1, "ok": False}
                self_inner.wfile.write((json.dumps(reply) + "\n").encode())

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        sd._write_daemon_metadata(
            root,
            {
                "version": 1,
                "package_version": _expected_tg_version(),
                "root": str(root),
                "host": "127.0.0.1",
                "port": int(self.server.server_address[1]),
                "pid": 1,
                "started_at": "x",
                "token": "tok",
            },
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_an_unconfirmed_stop_exits_2_with_an_ascii_message_in_both_modes(
    tmp_path: Path, as_json: bool
) -> None:
    root = tmp_path.resolve()
    daemon = _UnauthorizedStopDaemon(root)
    try:
        done = _run_cli(root, *(["--json"] if as_json else []))
        assert daemon.pings >= 2, "the later ping never happened: the test is vacuous"
        assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
        if as_json:
            payload = json.loads(done.stdout)
            assert payload["running"] is True
            assert payload["stopped"] is False
            assert payload["error"]["code"] == "stop_unconfirmed"
            message = payload["error"]["message"]
            assert "could not be confirmed" in message
            assert message.isascii()
            assert "Session daemon not running" not in done.stdout
        else:
            assert "could not be confirmed" in done.stderr
            assert done.stderr.isascii()
            assert "Session daemon not running" not in done.stdout + done.stderr
        assert sd._read_daemon_metadata(root) is not None  # and the metadata was kept
    finally:
        daemon.close()


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_a_clean_stop_of_a_real_daemon_still_exits_0(tmp_path: Path, as_json: bool) -> None:
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    sd._spawn_daemon_subprocess(root)
    live = None
    deadline = time.time() + 60
    while time.time() < deadline and live is None:
        live = sd._probe_daemon(root)
        time.sleep(0.2)
    assert live is not None, "the real daemon never became reachable"
    try:
        done = _run_cli(root, *(["--json"] if as_json else []))
        assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
        if as_json:
            payload = json.loads(done.stdout)
            assert payload["stopped"] is True
            assert payload["running"] is False
            assert "error" not in payload
        else:
            assert "Session daemon stopped" in done.stdout
    finally:
        try:
            import psutil

            psutil.Process(int(live["pid"])).kill()
        except Exception:
            pass


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_no_daemon_at_all_is_still_exit_0_not_running(tmp_path: Path, as_json: bool) -> None:
    root = tmp_path.resolve()
    done = _run_cli(root, *(["--json"] if as_json else []))
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)
    if as_json:
        payload = json.loads(done.stdout)
        assert payload["running"] is False
        assert payload["stopped"] is False
    else:
        assert "Session daemon not running" in done.stdout
