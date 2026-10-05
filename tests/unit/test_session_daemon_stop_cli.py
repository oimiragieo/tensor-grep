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
def _env(trusted_daemon_secret_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Not a bare ``tmp_path / "secret"``: a dev box whose %TEMP% grants foreign accounts modify
    # rights fails the product's (correct) ancestor-trust walk. See ``tests/conftest.py``.
    monkeypatch.setenv("TG_DAEMON_SECRET_DIR", str(trusted_daemon_secret_dir))


def _run_cli(root: Path, *flags: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": _SRC,
        "TG_SESSION_DAEMON_AUTOSTART": "0",
        "TG_DISABLE_NATIVE_TG": "1",
    }
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


# ---- errors raised by the stop helper itself (round 4) ----


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_an_exception_exits_2_with_ascii_only_output_and_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    from typer.testing import CliRunner

    from tensor_grep.cli.main import app

    def _boom(path: str) -> dict[str, Any]:
        raise ValueError("bad root: caf\u00e9")

    monkeypatch.setattr(sd, "stop_session_daemon", _boom)
    args = ["session", "daemon", "stop", str(tmp_path), *(["--json"] if as_json else [])]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2, (result.exit_code, result.output)
    assert result.output.isascii(), repr(result.output)  # tg's own message: ASCII only
    if as_json:
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "stop_failed"
        assert "caf\\xe9" in payload["error"]["message"]
        assert payload["error"]["message"].isascii()
    else:
        assert "Session daemon stop failed" in result.output
        assert "caf\\xe9" in result.output  # escaped, not printed


def test_cli_real_daemon_with_a_changed_token_and_a_dead_pid_exits_2(tmp_path: Path) -> None:
    """Round-5 repro through the shipped CLI: real daemon, same port, token changed so the ping
    cannot authenticate, recorded pid replaced by one the OS confirms is absent."""
    psutil = pytest.importorskip("psutil")
    root = (tmp_path / "rootA").resolve()
    root.mkdir()
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait(timeout=30)
    if psutil.pid_exists(done.pid):
        pytest.skip("the pid was recycled before the probe")
    sd._spawn_daemon_subprocess(root)
    live = None
    deadline = time.time() + 60
    while time.time() < deadline and live is None:
        live = sd._probe_daemon(root)
        time.sleep(0.2)
    assert live is not None, "the real daemon never became reachable"
    real_pid = int(live["pid"])
    try:
        meta = sd._read_daemon_metadata(root)
        assert meta is not None
        sd._write_daemon_metadata(root, {**meta, "token": "changed-token", "pid": done.pid})
        done_cli = _run_cli(root, "--json")
        assert done_cli.returncode == 2, (done_cli.returncode, done_cli.stdout, done_cli.stderr)
        payload = json.loads(done_cli.stdout)
        assert payload["running"] is True
        assert payload["stopped"] is False
        assert payload["error"]["code"] == "stop_unconfirmed"
        assert payload["unconfirmed_reason"] == "endpoint_still_accepting_connections"
        assert psutil.pid_exists(real_pid), "the real daemon is gone: it should be untouched"
        assert sd._read_daemon_metadata(root) is not None
    finally:
        try:
            psutil.Process(real_pid).kill()
        except Exception:
            pass
