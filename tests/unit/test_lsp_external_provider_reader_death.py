"""A client whose stdout reader died must not be reported (or reused) as a healthy child.

`ExternalLSPClient._reader_loop` ends on ANY unreadable frame (`_read_message` returns None for an
oversized frame, a bad header, an empty body; or json decoding raises) after broadcasting "closed"
to the in-flight request -- but it never stops the child. `start()`'s fast path only asked
`process.poll() is None`, so the client kept reporting `running=True, initialized=True` and every
later request reused the SAME child, whose stdout nobody reads any more, and waited the full
`request_timeout_seconds` for a reply that could not arrive (the root cause in `last_error` was
overwritten by the timeout message, and an undrained stdout pipe can eventually block the child).

These tests drive a REAL child process (a tiny fake language server) so the reader thread, the pipe
and `poll()` are the production objects, not stubs.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from tensor_grep.cli import lsp_external_provider as provider
from tests.helpers.lsp_session import install_session


@pytest.fixture(autouse=True)
def _independent_of_installed_language_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    # The constructor resolves a provider binary (pyright-langserver for "python") and raises
    # FileNotFoundError where none is installed -- the CI GPU runner, but not this dev box, which
    # is how these tests first went red only in CI. They supply their own command, so resolution
    # must not depend on the machine.
    monkeypatch.setattr(provider, "_provider_command", lambda _language: ["unused-fake-lsp"])


# Answers `initialize`; on the FIRST workspace/symbol it emits a frame whose body is not JSON, which
# kills the client's reader thread while leaving the child running.
_FAKE_SERVER = r"""
import json
import sys

inp, out = sys.stdin.buffer, sys.stdout.buffer


def read():
    length = 0
    while True:
        line = inp.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length"):
            length = int(line.split(b":")[1])
    return json.loads(inp.read(length))


def send(obj):
    body = json.dumps(obj).encode()
    out.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    out.flush()


bad_sent = False
while True:
    message = read()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
    elif method == "workspace/symbol" and not bad_sent:
        bad_sent = True
        out.write(b"Content-Length: 5\r\n\r\n{{{{{")
        out.flush()
    elif method == "exit":
        break
"""


def _client(tmp_path: Path) -> provider.ExternalLSPClient:
    server = tmp_path / "fake_server.py"
    server.write_text(_FAKE_SERVER, encoding="utf-8")
    client = provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=3.0,
        initialize_timeout_seconds=10.0,
    )
    client.command = [sys.executable, str(server)]
    return client


def _kill_reader(client: provider.ExternalLSPClient) -> None:
    """Send the bad frame and wait until the reader thread has actually died."""
    with pytest.raises(provider.LSPTransportError):
        client.request("workspace/symbol", {"query": "x"})
    reader = client._reader_thread
    assert reader is not None
    reader.join(timeout=5.0)
    assert not reader.is_alive(), "precondition: the bad frame must have killed the reader"


def _wait_for_child_exit(client: provider.ExternalLSPClient, seconds: float = 10.0) -> bool:
    process = client.process
    assert process is not None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_a_dead_reader_does_not_leave_a_live_child_reported_running(tmp_path: Path) -> None:
    client = _client(tmp_path)
    try:
        client.start()
        _kill_reader(client)

        # the reader is gone, so the child it served is terminated rather than left running
        assert _wait_for_child_exit(client), "child still running with no reader draining it"
        assert client.status()["running"] is False
    finally:
        client.stop()


def test_a_dead_reader_triggers_a_restart_instead_of_a_timeout_on_the_same_child(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    try:
        client.start()
        _kill_reader(client)
        assert client.process is not None
        first_pid = client.process.pid
        assert _wait_for_child_exit(client), "child still running with no reader draining it"

        started = time.monotonic()
        with pytest.raises(provider.LSPTransportError):
            client.request("workspace/symbol", {"query": "y"})
        elapsed = time.monotonic() - started

        assert client.process is not None
        assert client.process.pid != first_pid, "the dead-reader child must be replaced"
        # the replacement answers (then emits its own bad frame) immediately; waiting the whole
        # 3s request timeout means the request went to the old, unread child
        assert elapsed < 2.0, elapsed
        assert "timeout" not in str(client.last_error)
    finally:
        client.stop()


def test_a_healthy_client_is_not_restarted(tmp_path: Path) -> None:
    # CONTROL: a live child with a live reader keeps its pid across start() calls.
    client = _client(tmp_path)
    try:
        client.start()
        assert client.process is not None
        pid = client.process.pid
        reader = client._reader_thread

        client.start()

        assert client.process.pid == pid
        assert client._reader_thread is reader
        assert client.status()["running"] is True
    finally:
        client.stop()


class _UnkillableChild:
    """A child that ignores terminate/kill and never exits (stand-in for D-state / denied kill)."""

    stdin = None
    stdout = None
    stderr = None
    pid = 424242

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        raise PermissionError("terminate denied")

    def kill(self) -> None:
        raise PermissionError("kill denied")

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired(cmd="lsp", timeout=timeout or 0.0)


def test_stop_discloses_a_child_it_could_not_kill_instead_of_dropping_it_silently(
    tmp_path: Path,
) -> None:
    client = provider.ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=0.2,
        initialize_timeout_seconds=1.0,
    )
    install_session(client, process=_UnkillableChild())  # type: ignore[assignment]

    client.stop()

    assert client.process is None
    assert "kill denied" in str(client.last_error), client.last_error
    assert "424242" in str(client.last_error)
