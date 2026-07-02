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


def test_notify_document_closed_evicts_doc_version(tmp_path, monkeypatch) -> None:
    """Audit LOW (leak): _doc_versions grew unbounded because closed URIs were never
    evicted (unlike _opened_documents). Closing a document must drop its version entry."""
    import tensor_grep.cli.lsp_external_provider as m

    monkeypatch.setattr(m, "_provider_command", lambda language: ["dummy-lsp"])
    client = m.ExternalLSPClient(language="python", workspace_root=tmp_path)

    client._doc_versions["file:///x.py"] = 5
    client._notify_document_closed("file:///x.py")

    assert "file:///x.py" not in client._doc_versions
