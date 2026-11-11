from __future__ import annotations

import math
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import tensor_grep.cli.lsp_external_provider as provider_module
from tensor_grep.cli.lsp_external_provider import ExternalLSPClient
from tests.helpers.lsp_session import install_session

_READ_CHUNK_BYTES = 4096
_MAX_LINES = 50
_MAX_TEXT_BYTES = 64 * 1024
_MAX_LINE_BYTES = 8 * 1024


class _ChunkReader:
    def __init__(self, payload: bytes, chunk_bytes: int = _READ_CHUNK_BYTES) -> None:
        self.payload = payload
        self.chunk_bytes = chunk_bytes
        self.offset = 0
        self.read_sizes: list[int] = []
        self.read_bytes = 0

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        end = min(self.offset + size, self.offset + self.chunk_bytes, len(self.payload))
        chunk = self.payload[self.offset : end]
        self.offset = end
        self.read_bytes += len(chunk)
        return chunk

    def __iter__(self):
        # The old line iterator materialized a newline-free stream as one diagnostic line.
        yield self.payload


class _BoundedTail(list[str]):
    def append(self, line: str) -> None:
        assert len(self) < _MAX_LINES
        assert sum(len(item.encode("utf-8")) for item in self) + len(line.encode("utf-8")) <= (
            _MAX_TEXT_BYTES
        )
        super().append(line)


def _client_with_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stream: Any
) -> ExternalLSPClient:
    monkeypatch.setattr(provider_module, "_provider_command", lambda _language: ["fake-lsp"])
    client = ExternalLSPClient(language="python", workspace_root=tmp_path)
    session = install_session(client, process=SimpleNamespace(stderr=stream))
    session.stderr_tail = _BoundedTail()
    client.enable_debug_trace()
    client._stderr_loop(session)
    return client


def _diagnostic_trace(client: ExternalLSPClient) -> list[dict[str, Any]]:
    return [event for event in client.debug_trace() if event["event"] == "stderr"]


def _assert_diagnostic_limits(lines: list[str], trace: list[dict[str, Any]]) -> None:
    assert len(lines) <= _MAX_LINES
    assert sum(len(line.encode("utf-8")) for line in lines) <= _MAX_TEXT_BYTES
    messages = [str(event["message"]) for event in trace]
    assert len(messages) <= _MAX_LINES
    assert sum(len(message.encode("utf-8")) for message in messages) <= _MAX_TEXT_BYTES
    assert all(len(line.encode("utf-8")) <= _MAX_LINE_BYTES for line in lines + messages)


def test_newline_free_stderr_is_drained_in_bounded_chunks_and_truncated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"x" * (1024 * 1024)
    stream = _ChunkReader(payload)

    client = _client_with_stderr(monkeypatch, tmp_path, stream)

    assert stream.read_sizes
    assert all(size <= _READ_CHUNK_BYTES for size in stream.read_sizes)
    assert stream.read_bytes == len(payload)
    assert len(stream.read_sizes) <= math.ceil(len(payload) / _READ_CHUNK_BYTES) + 1
    tail = client.stderr_tail()
    trace = _diagnostic_trace(client)
    _assert_diagnostic_limits(tail, trace)
    assert len(tail) == len(trace) == 1
    assert "[truncated]" in tail[0]
    assert trace[0]["message"] == tail[0]


def test_stderr_incrementally_decodes_split_and_malformed_utf8(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"ready \xe2\x82\xac\xff\r\npartial \xe2\x82"
    stream = _ChunkReader(payload, chunk_bytes=1)

    client = _client_with_stderr(monkeypatch, tmp_path, stream)

    expected = [part.rstrip("\r") for part in payload.decode("utf-8", errors="replace").split("\n")]
    assert client.stderr_tail() == expected
    assert [event["message"] for event in _diagnostic_trace(client)] == expected
    assert stream.read_bytes == len(payload)
    assert all(size <= _READ_CHUNK_BYTES for size in stream.read_sizes)


def test_multibyte_line_limit_counts_encoded_utf8_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client_with_stderr(monkeypatch, tmp_path, _ChunkReader(("é" * 8192).encode("utf-8")))

    line = client.stderr_tail()[0]
    assert len(line.encode("utf-8")) == _MAX_LINE_BYTES
    assert line.endswith("[truncated]")
    assert _diagnostic_trace(client)[0]["message"] == line


def test_many_short_stderr_lines_bound_the_tail_and_debug_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = "".join(f"diagnostic {index:04d}\n" for index in range(1000)).encode()
    client = _client_with_stderr(monkeypatch, tmp_path, _ChunkReader(payload))

    tail = client.stderr_tail()
    trace = _diagnostic_trace(client)
    _assert_diagnostic_limits(tail, trace)
    assert tail == [f"diagnostic {index:04d}" for index in range(950, 1000)]
    assert [event["message"] for event in trace] == [
        f"diagnostic {index:04d}" for index in range(_MAX_LINES)
    ]
    for index in range(75):
        client._record_debug_trace(event="send_request", request_id=index)
    assert len([event for event in client.debug_trace() if event["event"] == "send_request"]) == 75


def test_total_text_budget_bounds_tail_and_stderr_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = [chr(ord("a") + index) * _MAX_LINE_BYTES for index in range(10)]
    payload = ("\n".join(expected) + "\n").encode("utf-8")
    client = _client_with_stderr(monkeypatch, tmp_path, _ChunkReader(payload))

    tail = client.stderr_tail()
    trace = _diagnostic_trace(client)
    _assert_diagnostic_limits(tail, trace)
    assert tail == expected[-8:]
    assert [event["message"] for event in trace] == expected[:8]


def test_real_provider_captures_short_stderr_while_running_and_stops_promptly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = textwrap.dedent(
        """
        import json
        import sys

        sys.stderr.buffer.write(b"provider ready\\n")
        sys.stderr.buffer.flush()

        def read_message():
            header = bytearray()
            while not header.endswith(b"\\r\\n\\r\\n"):
                byte = sys.stdin.buffer.read(1)
                if not byte:
                    return None
                header.extend(byte)
            length = next(
                int(line.split(b":", 1)[1])
                for line in header.split(b"\\r\\n")
                if line.lower().startswith(b"content-length:")
            )
            return json.loads(sys.stdin.buffer.read(length))

        def send(message):
            body = json.dumps(message, separators=(",", ":")).encode()
            sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\\r\\n\\r\\n" + body)
            sys.stdout.buffer.flush()

        while True:
            message = read_message()
            if message is None:
                break
            method = message.get("method")
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {}}})
            elif method == "shutdown":
                send({"jsonrpc": "2.0", "id": message["id"], "result": None})
            elif method == "exit":
                break
        """
    )
    monkeypatch.setattr(provider_module, "_provider_command", lambda _language: ["fake-lsp"])
    client = ExternalLSPClient(
        language="python",
        workspace_root=tmp_path,
        request_timeout_seconds=1.5,
        initialize_timeout_seconds=1.5,
    )
    client.command = [sys.executable, "-u", "-c", server]
    client.stop_grace_seconds = 1.0
    client.enable_debug_trace()

    stderr_seen = threading.Event()
    record_debug_trace = ExternalLSPClient._record_debug_trace

    def observe_stderr(self: ExternalLSPClient, **kwargs: Any) -> None:
        record_debug_trace(self, **kwargs)
        if kwargs.get("event") == "stderr":
            stderr_seen.set()

    monkeypatch.setattr(ExternalLSPClient, "_record_debug_trace", observe_stderr)
    try:
        client.start()
        session = client.current_session()
        assert stderr_seen.wait(timeout=1.0), "stderr was not read while the provider stayed live"
        assert session.stderr_tail == ["provider ready"]
        assert client.status()["running"] is True
        started = time.monotonic()
        client.stop()
        assert time.monotonic() - started < 2.0
        assert client.status()["running"] is False
        _assert_diagnostic_limits(session.stderr_tail, _diagnostic_trace(client))
    finally:
        client.stop()
