"""Audit #6: the MCP stdio Content-Length compatibility reader must bound the framed read -- a
hostile/buggy client sending a huge Content-Length must be refused, not drive an unbounded
stdin.read (memory DoS). Mirrors the LSP reader's 64MB cap."""

import anyio

from tensor_grep.cli import mcp_server


class _FakeStdin:
    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.read_bytes_requested: int | None = None

    async def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""

    async def read(self, n: int) -> str:
        self.read_bytes_requested = n
        return "x" * min(n, 8)


def test_oversized_content_length_is_refused_without_reading() -> None:
    stdin = _FakeStdin([f"Content-Length: {mcp_server._MAX_MCP_STDIO_MESSAGE_BYTES + 1}\r\n"])
    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert result is None
    assert stdin.read_bytes_requested is None  # never attempted the unbounded read


def test_nonpositive_content_length_is_refused() -> None:
    stdin = _FakeStdin(["Content-Length: 0\r\n"])
    assert anyio.run(mcp_server._read_stdio_message_payload, stdin) is None


def test_valid_content_length_still_reads_the_body() -> None:
    stdin = _FakeStdin(["Content-Length: 5\r\n", "\r\n", "hello"])
    body = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert stdin.read_bytes_requested == 5
    assert body is not None
