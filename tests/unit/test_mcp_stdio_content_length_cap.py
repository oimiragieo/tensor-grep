"""Audit #6 + audit #49: the MCP stdio Content-Length compatibility reader must be byte-exact and
bounded.

Audit #6 (shipped in #363): a hostile/buggy client sending a huge Content-Length must be refused,
not drive an unbounded stdin.read (memory DoS). Mirrors the LSP reader's 64MB cap.

Audit #49: three further defects in the same reader --
  1. `stdin.read(content_length)` read `content_length` CHARACTERS off a text-decoding stream, but
     Content-Length is a BYTE count -- any multi-byte UTF-8 payload desyncs the framed stream, so
     every message after it misparses.
  2. the header-line-skip loop (between the Content-Length line and the blank-line terminator) had
     no iteration cap and no byte cap, so a malformed client (endless headers, or no terminator)
     could hang the reader / exhaust memory before the body-size check ever mattered.
  3. (gate must-fix) the FIRST line read was still unbounded. In the newline-delimited transport --
     the OFFICIAL/primary MCP stdio path -- the whole message IS that first line, and the 64MB body
     cap guards only the framed body, so a no-newline / multi-GB newline-delimited message had NO
     size cap at all; and a `Content-Length:` header line with no newline grew memory before the
     body-size check ran.

All three are fixed by reading the stdio stream as raw bytes throughout (never through a
TextIOWrapper) and bounding EVERY line -- the first line at the full message budget, the header
lines at 64KB -- the same fail-closed way the body is bounded: never unbounded.
"""

import io
import json

import anyio
import pytest

from tensor_grep.cli import mcp_server


def _stdin(data: bytes) -> anyio.AsyncFile[bytes]:
    """A real byte-oriented AsyncFile, matching the production `sys.stdin.buffer` contract."""
    return anyio.wrap_file(io.BytesIO(data))


def _framed(body: str) -> bytes:
    encoded = body.encode("utf-8")
    return f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii") + encoded


# ---------------------------------------------------------------------------
# Audit #6 cap, re-verified against the byte-exact reader.
# ---------------------------------------------------------------------------


def test_oversized_content_length_is_refused_without_reading() -> None:
    # No trailing blank-line terminator on purpose: the size check must reject right after the
    # Content-Length line itself, never reaching (let alone unbounded-reading) anything after it.
    header = f"Content-Length: {mcp_server._MAX_MCP_STDIO_MESSAGE_BYTES + 1}\r\n".encode("ascii")
    stdin = _stdin(header)
    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert result is None
    # Never attempted the unbounded body read: nothing past the Content-Length line was consumed.
    assert stdin.wrapped.tell() == len(header)


def test_nonpositive_content_length_is_refused() -> None:
    stdin = _stdin(b"Content-Length: 0\r\n")
    assert anyio.run(mcp_server._read_stdio_message_payload, stdin) is None


def test_valid_content_length_still_reads_the_body() -> None:
    stdin = _stdin(b"Content-Length: 5\r\n\r\nhello")
    body = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert body == "hello"


# ---------------------------------------------------------------------------
# Audit #49 defect 1: char-vs-byte desync on a multi-byte UTF-8 body.
# ---------------------------------------------------------------------------


def test_multibyte_utf8_body_frames_correctly_and_does_not_desync_next_message() -> None:
    # "e" with an acute accent (\u00e9) and "o" with an umlaut (\u00f6) are each 2 UTF-8 bytes but
    # 1 character -- Content-Length (a byte count) and Python's str length (a char count) diverge
    # for this body. Escape sequences keep this source file itself ASCII-only (house rule).
    msg1 = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "search", "params": {"q": "h\u00e9llo w\u00f6rld"}},
        ensure_ascii=False,
    )
    msg2 = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
    assert len(msg1) != len(msg1.encode("utf-8")), "fixture must contain multi-byte UTF-8 chars"

    stdin = _stdin(_framed(msg1) + _framed(msg2))

    async def _read_both() -> tuple[str | None, str | None]:
        first = await mcp_server._read_stdio_message_payload(stdin)
        second = await mcp_server._read_stdio_message_payload(stdin)
        return first, second

    first, second = anyio.run(_read_both)

    assert first is not None
    assert json.loads(first) == json.loads(msg1)
    assert second is not None
    assert json.loads(second) == json.loads(msg2)


def test_ascii_only_body_still_frames_correctly() -> None:
    """Control case: an ASCII-only body has char-count == byte-count, so it never exposed the bug.
    Kept alongside the multi-byte test so a future change can't "fix" one and break the other."""
    msg1 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    msg2 = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "pong", "params": {}})
    stdin = _stdin(_framed(msg1) + _framed(msg2))

    async def _read_both() -> tuple[str | None, str | None]:
        first = await mcp_server._read_stdio_message_payload(stdin)
        second = await mcp_server._read_stdio_message_payload(stdin)
        return first, second

    first, second = anyio.run(_read_both)
    assert first is not None and json.loads(first) == json.loads(msg1)
    assert second is not None and json.loads(second) == json.loads(msg2)


def test_short_body_read_is_a_clean_framing_error_not_a_hang() -> None:
    """Content-Length declares 100 bytes but the stream only has 5 before EOF: a clean framing
    error (None), never an exception, never a hang, never a silently-truncated parse."""
    stdin = _stdin(b"Content-Length: 100\r\n\r\nhello")
    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert result is None


def test_plain_newline_delimited_json_still_works() -> None:
    """The non-framed (official MCP stdio) path is untouched by the byte-vs-char fix."""
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}})
    stdin = _stdin((msg + "\n").encode("utf-8"))
    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)
    assert result is not None
    assert json.loads(result) == json.loads(msg)


# ---------------------------------------------------------------------------
# Audit #49 defect 2: the header-skip loop must be bounded (iteration count AND byte length).
# ---------------------------------------------------------------------------


def test_endless_header_lines_without_terminator_is_bounded() -> None:
    """A malformed/hostile client that streams far more header lines than any real client would,
    and never sends the blank-line terminator, must be refused -- and refused WITHOUT draining the
    whole malicious stream (proving the iteration cap fired, not just eventual EOF)."""
    header_line_count = mcp_server._MAX_MCP_STDIO_HEADER_LINES * 20
    headers = b"".join(f"X-Junk-{i}: v\r\n".encode("ascii") for i in range(header_line_count))
    raw = b"Content-Length: 5\r\n" + headers  # no blank-line terminator, ever
    stdin = _stdin(raw)

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is None
    # Bounded: did not consume anywhere near the full malicious header stream.
    consumed = stdin.wrapped.tell()
    assert consumed < len(raw) // 4, (
        f"consumed {consumed} of {len(raw)} bytes -- header loop was not bounded"
    )


def test_single_oversized_header_line_is_bounded() -> None:
    """A single pathological header line with no newline for a long time must not be buffered
    without limit in one readline() call -- bounded to a small multiple of the header byte cap,
    not proportional to how much the attacker sends."""
    giant_line = b"X-Junk: " + b"A" * (mcp_server._MAX_MCP_STDIO_HEADER_BYTES * 5)  # no b"\n"
    raw = b"Content-Length: 5\r\n" + giant_line
    stdin = _stdin(raw)

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is None
    consumed = stdin.wrapped.tell()
    assert consumed < mcp_server._MAX_MCP_STDIO_HEADER_BYTES * 3, (
        f"consumed {consumed} bytes -- single header line was not bounded"
    )


def test_header_line_count_at_the_cap_still_succeeds() -> None:
    """Boundary check: a header preamble that stays within both caps still parses normally."""
    # A handful of small, legitimate-shaped extra headers plus the blank-line terminator.
    headers = b"".join(f"X-Extra-{i}: v\r\n".encode("ascii") for i in range(5))
    raw = b"Content-Length: 5\r\n" + headers + b"\r\nhello"
    stdin = _stdin(raw)

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result == "hello"


# ---------------------------------------------------------------------------
# Audit #49 defect 3 (gate must-fix): the FIRST line must be bounded too. The cap is monkeypatched
# DOWN so these tests prove the bounding logic without allocating the real 64MB budget (and without
# risking an OOM against the pre-fix unbounded read).
# ---------------------------------------------------------------------------


def test_giant_first_line_without_newline_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostile `Content-Length:` header line with no newline must not be read/buffered unbounded
    BEFORE the size check -- the exact 'exhaust memory before the size check runs' hole. Bounded to
    the (patched) first-line cap, not proportional to how much the attacker streams."""
    # raising=False so the behavioral assertion below (not the constant's existence) is what fails
    # against a pre-fix reader whose first read is unbounded: it would consume the whole stream.
    monkeypatch.setattr(mcp_server, "_MAX_MCP_STDIO_FIRST_LINE_BYTES", 4096, raising=False)
    # A single first line with no newline that far exceeds the patched cap.
    raw = b"Content-Length: " + b"9" * (4096 * 20)
    stdin = _stdin(raw)

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is None
    consumed = stdin.wrapped.tell()
    assert consumed <= 4096, (
        f"consumed {consumed} of {len(raw)} bytes -- the first line was not bounded"
    )


def test_oversized_newline_delimited_message_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The PRIMARY path: a newline-delimited message (no Content-Length framing) larger than the
    message cap and with no newline within it must be refused -- not read unbounded. Without this
    bound the newline-delimited transport had NO message-size cap at all."""
    # raising=False so the behavioral assertion below (not the constant's existence) is what fails
    # against a pre-fix reader whose first read is unbounded: it would consume the whole message.
    monkeypatch.setattr(mcp_server, "_MAX_MCP_STDIO_FIRST_LINE_BYTES", 4096, raising=False)
    # A big JSON-ish blob, no Content-Length prefix, no newline, exceeding the patched cap.
    raw = b'{"jsonrpc":"2.0","method":"x","params":{"blob":"' + b"A" * (4096 * 20) + b'"}}'
    stdin = _stdin(raw)

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is None
    consumed = stdin.wrapped.tell()
    assert consumed <= 4096, (
        f"consumed {consumed} of {len(raw)} bytes -- the newline-delimited message was not bounded"
    )


def test_newline_delimited_message_just_under_cap_is_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard against over-eager rejection: a valid newline-delimited message that fits within the
    cap (terminated by its trailing newline) is returned intact, never truncated. A regression guard
    -- it should hold both before and after the fix (raising=False keeps it constant-agnostic)."""
    monkeypatch.setattr(mcp_server, "_MAX_MCP_STDIO_FIRST_LINE_BYTES", 4096, raising=False)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * 1000}}
    msg = json.dumps(payload)
    assert len(msg.encode("utf-8")) < 4096, "fixture must fit under the patched cap"
    stdin = _stdin((msg + "\n").encode("utf-8"))

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is not None
    assert json.loads(result) == payload


def test_first_line_read_uses_the_full_message_budget_by_default() -> None:
    """The real (un-patched) first-line cap is the message budget + 1 (room for a trailing newline),
    so a legit multi-KB newline-delimited message is never truncated at the header-line 64KB cap."""
    assert mcp_server._MAX_MCP_STDIO_FIRST_LINE_BYTES == mcp_server._MAX_MCP_STDIO_MESSAGE_BYTES + 1
    # A message well over the 64KB header-line cap but far under the 64MB message budget must pass.
    payload = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": "x" * 200_000}}
    msg = json.dumps(payload)
    assert len(msg.encode("utf-8")) > mcp_server._MAX_MCP_STDIO_HEADER_BYTES
    stdin = _stdin((msg + "\n").encode("utf-8"))

    result = anyio.run(mcp_server._read_stdio_message_payload, stdin)

    assert result is not None
    assert json.loads(result) == payload
