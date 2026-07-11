"""Hardening regression tests for the external-LSP-provider message reader (audit MED, #116).

``_read_message`` trusted the framed ``Content-Length`` header with no upper bound before
allocating/reading the body from the provider subprocess's stdout, so a malicious or buggy
provider could force an unbounded read. It must refuse oversized (and malformed) frames.

Audit #116 extends this to the HEADER preamble itself (the same unbounded-header-skip shape
fixed for the MCP stdio reader in audit #49, ``mcp_server.py``): the loop that reads header
lines between the start of a message and the blank-line terminator had no per-line byte cap
and no iteration cap, so a malformed/malicious language-server subprocess streaming a single
giant no-newline line, or endless small header lines, could force an unbounded read/allocation
before the ``Content-Length`` size check ever ran.
"""

from __future__ import annotations

from io import BytesIO

from tensor_grep.cli.lsp_external_provider import (
    _MAX_LSP_HEADER_BYTES,
    _MAX_LSP_HEADER_LINES,
    _MAX_LSP_MESSAGE_BYTES,
    _read_message,
)


class _FakeStream:
    def __init__(self, header_lines: list[str]) -> None:
        self._lines = list(header_lines)
        self.read_called_with: int | None = None

    def readline(self, size: int = -1) -> str:
        # `size` is accepted (not `readline()`-only) to match the real stream interface
        # `_read_message` now calls via `_read_bounded_line` (audit #116) -- ignored here
        # since this fake always hands back a whole pre-chopped line regardless of the
        # requested bound; the boundedness tests below use a real `BytesIO` instead, whose
        # `readline(size)` truly truncates.
        del size
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


def test_read_message_bounds_giant_no_newline_header_line() -> None:
    """A single header line with no newline terminator, far larger than the per-line header
    cap, must not force an unbounded single ``readline()`` buffer allocation (audit #116;
    mirrors the MCP stdio audit #49 header-line byte bound).

    Pre-fix, ``stream.readline()`` took no size argument: fed a newline-free buffer, it reads
    every byte up to EOF in ONE call. The plain ``_read_message(stream) is None`` assertion
    alone would pass even pre-fix (the loop still terminates once the fake stream hits EOF on
    its *next* call) -- the ``stream.tell()`` bound below is what actually proves the read was
    capped rather than draining the whole buffer, and is the assertion that is RED pre-fix.
    """
    oversized = b"X" * (_MAX_LSP_HEADER_BYTES * 4)
    stream = BytesIO(oversized)

    assert _read_message(stream) is None
    assert stream.tell() <= _MAX_LSP_HEADER_BYTES


def test_read_message_bounds_header_line_count() -> None:
    """More header lines than the iteration cap, each individually tiny and well-formed, with
    no blank-line terminator anywhere, must not be drained without limit (audit #116; mirrors
    the MCP stdio audit #49 header-line-count bound).

    Pre-fix, the header loop had no iteration cap: fed enough small, colon-bearing lines it
    keeps consuming (and dict-inserting) every one of them until the fake stream's buffer runs
    dry. As with the giant-line case, ``stream.tell()`` -- not just the ``is None`` return --
    is what proves the loop stopped at the cap instead of just running out of fake data.
    """
    one_line = b"X-Pad: 1\r\n"
    stream = BytesIO(one_line * (_MAX_LSP_HEADER_LINES * 4))

    assert _read_message(stream) is None
    assert stream.tell() <= _MAX_LSP_HEADER_LINES * len(one_line)


def test_read_message_parses_small_message_with_multiple_headers() -> None:
    """Happy-path regression guard: the new bounded header loop must not disturb normal
    framing -- a small message with more than one header line (an extra ``Content-Type``
    header ahead of ``Content-Length``) still parses correctly."""
    body = b'{"jsonrpc": "2.0", "id": 1, "result": null}'
    framed = (
        b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        + body
    )
    stream = BytesIO(framed)

    assert _read_message(stream) == {"jsonrpc": "2.0", "id": 1, "result": None}


def test_notify_document_closed_evicts_doc_version(tmp_path, monkeypatch) -> None:
    """Audit LOW (leak): _doc_versions grew unbounded because closed URIs were never
    evicted (unlike _opened_documents). Closing a document must drop its version entry."""
    import tensor_grep.cli.lsp_external_provider as m

    monkeypatch.setattr(m, "_provider_command", lambda language: ["dummy-lsp"])
    client = m.ExternalLSPClient(language="python", workspace_root=tmp_path)

    client._doc_versions["file:///x.py"] = 5
    client._notify_document_closed("file:///x.py")

    assert "file:///x.py" not in client._doc_versions
