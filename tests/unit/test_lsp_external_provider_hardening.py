"""Hardening regression tests for the external-LSP-provider message reader (audit MED).

``_read_message`` trusted the framed ``Content-Length`` header with no upper bound before
allocating/reading the body from the provider subprocess's stdout, so a malicious or buggy
provider could force an unbounded read. It must refuse oversized (and malformed) frames.
"""

from __future__ import annotations

from tensor_grep.cli.lsp_external_provider import _MAX_LSP_MESSAGE_BYTES, _read_message


class _FakeStream:
    def __init__(self, header_lines: list[str]) -> None:
        self._lines = list(header_lines)
        self.read_called_with: int | None = None

    def readline(self) -> str:
        return self._lines.pop(0) if self._lines else ""

    def read(self, n: int) -> str:
        self.read_called_with = n
        return ""


def test_read_message_refuses_oversized_content_length() -> None:
    stream = _FakeStream([f"Content-Length: {_MAX_LSP_MESSAGE_BYTES + 1}\r\n", "\r\n"])
    assert _read_message(stream) is None
    assert stream.read_called_with is None  # the oversized body read was never attempted


def test_read_message_refuses_malformed_content_length() -> None:
    stream = _FakeStream(["Content-Length: not-a-number\r\n", "\r\n"])
    assert _read_message(stream) is None
    assert stream.read_called_with is None
